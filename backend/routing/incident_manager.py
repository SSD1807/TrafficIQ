"""Thread-safe incident registry with severity, directionality and expiry.

Three sources are supported:

* ``manual``     - created by a user through the API / Incident Management page.
* ``simulation`` - created for what-if testing.
* ``tomtom``     - imported from the TomTom Traffic Incident Details feed (see services/incident_sync.py).

The registry has two layers:

* **edge records** (``add`` / ``list_active`` / ``edge_effects``) - what the routing engine prices. Manual and
  simulated incidents are one record each; a provider incident matched to the road graph yields one record per matched
  directed edge (``id = tomtom:<provider id>:<u>:<v>:<key>``).
* **provider records** (``sync_provider_incidents``) - one record per provider incident, matched or not, holding the
  original provider data next to the match result. Unmatched provider incidents live ONLY here and never touch a road.

Synchronisation is idempotent: repeated polling never duplicates, changed incidents are updated in place, incidents the
provider no longer reports are removed, and manual/simulated records are never touched.
"""
from __future__ import annotations

import hashlib
import json
from datetime import datetime, timedelta, timezone
from threading import RLock
from typing import Any, Iterable
from uuid import uuid4

VALID_KINDS = {"accident", "road_closure", "road_work", "obstruction", "hazard", "traffic_jam"}
VALID_SOURCES = {"manual", "simulation", "tomtom"}
DEFAULT_PROVIDER_STALE_SECONDS = 900.0


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _fingerprint(entry: dict[str, Any]) -> str:
    """Stable hash of everything that changes how an incident is shown or priced."""
    inc, match = entry["incident"], entry["match"]
    basis = {k: inc.get(k) for k in ("kind", "severity", "description", "start_time", "end_time", "confirmed", "geometry",
                                      "time_validity", "probability_of_occurrence", "magnitude_of_delay", "delay_seconds",
                                      "length_m", "from", "to", "road_numbers", "number_of_reports", "last_report_time")}
    basis["match"] = [match.get("status"), [(e["u"], e["v"], e.get("key")) for e in match.get("edges", [])],
                      bool(match.get("bidirectional"))]
    basis["routing_applied"] = entry.get("routing_applied")
    return hashlib.sha1(json.dumps(basis, sort_keys=True, default=str).encode()).hexdigest()


class IncidentManager:
    def __init__(self, provider_stale_after_seconds: float = DEFAULT_PROVIDER_STALE_SECONDS) -> None:
        self._items: dict[str, dict[str, Any]] = {}
        self._provider: dict[str, dict[str, Any]] = {}     # provider incident id -> provider record (source "tomtom")
        self._lock = RLock()
        self.provider_stale_after_seconds = float(provider_stale_after_seconds)

    # ------------------------------------------------------------------ edge records (manual / simulation / tomtom)
    def add(
        self,
        edge_u: str | int,
        edge_v: str | int,
        *,
        kind: str = "accident",
        severity: float = 0.5,
        source: str = "manual",
        expires_in_seconds: int = 3600,
        bidirectional: bool = False,
        description: str = "",
        external_id: str | None = None,
        edge_key: str | int | None = None,
        provider_id: str | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        kind = kind.strip().lower()
        source = source.strip().lower()
        if kind not in VALID_KINDS:
            raise ValueError(f"kind must be one of {sorted(VALID_KINDS)}")
        if source not in VALID_SOURCES:
            raise ValueError(f"source must be one of {sorted(VALID_SOURCES)}")
        if not 0.0 <= float(severity) <= 1.0:
            raise ValueError("severity must be between 0 and 1")
        if int(expires_in_seconds) < 0:
            raise ValueError("expires_in_seconds cannot be negative")
        now = _utcnow()
        item = {
            "id": external_id or str(uuid4()),
            "edge_u": str(edge_u),
            "edge_v": str(edge_v),
            "edge_key": None if edge_key is None else str(edge_key),
            "kind": kind,
            "severity": float(severity),
            "source": source,
            "created_at": now.isoformat(),
            "expires_at": (now + timedelta(seconds=int(expires_in_seconds))).isoformat(),
            "bidirectional": bool(bidirectional),
            "description": description,
            "provider_id": provider_id,
            "last_seen_at": now.isoformat() if source == "tomtom" else None,
            **(metadata or {}),
        }
        with self._lock:
            self._purge_locked(now)
            self._items[item["id"]] = item
        return dict(item)

    def _purge_locked(self, now: datetime | None = None) -> None:
        now = now or _utcnow()
        expired = [k for k, item in self._items.items() if datetime.fromisoformat(item["expires_at"]) <= now]
        for key in expired:
            self._items.pop(key, None)
        # Provider records whose edge records all expired (safety net when polling stopped) are dropped as well.
        for pid in [p for p, rec in self._provider.items()
                    if now - datetime.fromisoformat(rec["last_seen_at"]) > timedelta(seconds=self.provider_stale_after_seconds * 2)]:
            self._drop_provider_locked(pid)

    def purge_expired(self) -> int:
        with self._lock:
            before = len(self._items) + len(self._provider)
            self._purge_locked()
            return before - len(self._items) - len(self._provider)

    def remove(self, incident_id: str) -> bool:
        with self._lock:
            return self._items.pop(str(incident_id), None) is not None

    def get(self, incident_id: str) -> dict[str, Any] | None:
        with self._lock:
            self._purge_locked()
            item = self._items.get(str(incident_id))
            return dict(item) if item else None

    def clear(self, source: str | None = None) -> int:
        with self._lock:
            if source is None or source == "tomtom":
                removed_provider = len(self._provider)
                self._provider.clear()
            else:
                removed_provider = 0
            if source is None:
                count = len(self._items)
                self._items.clear()
                return count + removed_provider
            keys = [k for k, v in self._items.items() if v["source"] == source]
            for key in keys:
                self._items.pop(key, None)
            return len(keys) if source != "tomtom" else removed_provider

    def list_active(self) -> list[dict[str, Any]]:
        with self._lock:
            self._purge_locked()
            return [self._decorate(v) for v in self._items.values()]

    def _is_stale_locked(self, item: dict[str, Any], now: datetime) -> bool:
        if item["source"] != "tomtom" or not item.get("last_seen_at"):
            return False
        return (now - datetime.fromisoformat(item["last_seen_at"])).total_seconds() > self.provider_stale_after_seconds

    def _decorate(self, item: dict[str, Any]) -> dict[str, Any]:
        out = dict(item)
        out["stale"] = self._is_stale_locked(item, _utcnow())
        return out

    def edge_effects(self, exclude_sources: Iterable[str] = ()) -> dict[tuple[str, str], list[dict[str, Any]]]:
        """Directed-edge -> incidents that should price that edge. Stale provider records are not applied."""
        excluded = set(exclude_sources)
        effects: dict[tuple[str, str], list[dict[str, Any]]] = {}
        now = _utcnow()
        with self._lock:
            self._purge_locked(now)
            items = [dict(i) for i in self._items.values()
                     if i["source"] not in excluded and not self._is_stale_locked(i, now)]
        for incident in items:
            effects.setdefault((incident["edge_u"], incident["edge_v"]), []).append(incident)
            if incident["bidirectional"]:
                effects.setdefault((incident["edge_v"], incident["edge_u"]), []).append(incident)
        return effects

    def view(self, exclude_sources: Iterable[str] = ()) -> "_FilteredIncidents":
        """Read-only manager look-alike that hides some sources (used for 'with vs without live incidents' comparisons)."""
        return _FilteredIncidents(self, set(exclude_sources))

    # ------------------------------------------------------------------ provider incidents
    def _drop_provider_locked(self, pid: str) -> None:
        rec = self._provider.pop(pid, None)
        if rec:
            for rid in rec.get("registry_ids", []):
                self._items.pop(rid, None)

    def sync_provider_incidents(self, entries: list[dict[str, Any]], *, ttl_seconds: float | None = None,
                                now: datetime | None = None) -> dict[str, int]:
        """Reconcile the registry with a COMPLETE, successful provider response.

        ``entries``: [{"incident": <normalised provider incident>, "match": <MatchResult.to_public()>,
                       "routing_applied": bool, "routing_skip_reason": str | None}]
        Call this only after a successful fetch. A failed request must NOT call it (so nothing is erased).
        An empty ``entries`` list is a valid answer ("no incidents") and removes every provider incident.
        """
        now = now or _utcnow()
        ttl = float(ttl_seconds if ttl_seconds is not None else self.provider_stale_after_seconds * 2)
        stats = {"added": 0, "updated": 0, "unchanged": 0, "removed": 0, "matched": 0, "unmatched": 0, "duplicates_ignored": 0}
        with self._lock:
            seen: set[str] = set()
            for entry in entries:
                inc = entry["incident"]
                pid = inc["provider_id"]
                if pid in seen:
                    stats["duplicates_ignored"] += 1
                    continue
                seen.add(pid)
                fp = _fingerprint(entry)
                prev = self._provider.get(pid)
                expires = (now + timedelta(seconds=ttl)).isoformat()
                if prev is not None and prev["fingerprint"] == fp:
                    prev["last_seen_at"] = now.isoformat()
                    for rid in prev["registry_ids"]:
                        if rid in self._items:
                            self._items[rid]["last_seen_at"], self._items[rid]["expires_at"] = now.isoformat(), expires
                    stats["unchanged"] += 1
                else:
                    first_seen = prev["first_seen_at"] if prev else now.isoformat()
                    if prev:
                        for rid in prev["registry_ids"]:
                            self._items.pop(rid, None)
                    registry_ids = self._create_edge_records_locked(entry, now, expires) if entry.get("routing_applied") else []
                    self._provider[pid] = {
                        "provider_id": pid, "incident": inc, "match": entry["match"], "fingerprint": fp,
                        "routing_applied": bool(registry_ids), "routing_skip_reason": entry.get("routing_skip_reason"),
                        "registry_ids": registry_ids, "first_seen_at": first_seen, "updated_at": now.isoformat(),
                        "last_seen_at": now.isoformat(),
                    }
                    stats["updated" if prev else "added"] += 1
                stats["matched" if entry["match"].get("status") == "matched" else "unmatched"] += 1
            for pid in [p for p in self._provider if p not in seen]:
                self._drop_provider_locked(pid)
                stats["removed"] += 1
        return stats

    def _create_edge_records_locked(self, entry: dict[str, Any], now: datetime, expires_iso: str) -> list[str]:
        inc, match = entry["incident"], entry["match"]
        ids: list[str] = []
        edges = match.get("edges", [])
        # TomTom reports ONE delay for the whole incident; share it over the matched edges by length (no invented value).
        total_len = sum(float(e.get("length_m") or 0.0) for e in edges)
        provider_delay = inc.get("delay_seconds")
        if provider_delay is not None and float(provider_delay) <= 0:
            provider_delay = None      # TomTom reports 0 when it has no delay estimate (always for closures): treat as "not reported"
        for edge in edges:
            if provider_delay is None:
                edge_delay = None
            elif total_len > 0:
                edge_delay = float(provider_delay) * float(edge.get("length_m") or 0.0) / total_len
            else:
                edge_delay = float(provider_delay) / max(1, len(edges))
            rid = f"tomtom:{inc['provider_id']}:{edge['u']}:{edge['v']}:{edge.get('key')}"
            self._items[rid] = {
                "id": rid, "edge_u": edge["u"], "edge_v": edge["v"], "edge_key": edge.get("key"),
                "kind": inc["kind"], "severity": float(inc["severity"]), "source": "tomtom",
                "created_at": now.isoformat(), "expires_at": expires_iso,
                "bidirectional": bool(match.get("bidirectional")),
                "description": inc.get("description", ""), "provider_id": inc["provider_id"],
                "last_seen_at": now.isoformat(),
                "match_confidence": match.get("confidence"),
                "edge_delay_s": edge_delay,
                "category": inc.get("category"),
            }
            ids.append(rid)
        return ids

    def provider_records(self) -> list[dict[str, Any]]:
        now = _utcnow()
        with self._lock:
            self._purge_locked(now)
            out = []
            for rec in self._provider.values():
                stale = (now - datetime.fromisoformat(rec["last_seen_at"])).total_seconds() > self.provider_stale_after_seconds
                out.append({**rec, "stale": stale, "registry_ids": list(rec["registry_ids"])})
            return out

    # ------------------------------------------------------------------ unified feed
    def feed(self) -> list[dict[str, Any]]:
        """One entry per incident as a user sees it: manual/simulated edge incidents + TomTom provider incidents."""
        entries: list[dict[str, Any]] = []
        for item in self.list_active():
            if item["source"] == "tomtom":
                continue
            entries.append({
                "id": item["id"], "source": item["source"], "kind": item["kind"], "description": item["description"],
                "severity": item["severity"], "severity_source": "user_defined", "status": "applied",
                "matched": True, "routing_applied": True, "confirmed": True, "stale": False,
                "start_time": item["created_at"], "end_time": None, "expires_at": item["expires_at"],
                "created_at": item["created_at"], "last_seen_at": None, "provider_id": None,
                "edges": [{"u": item["edge_u"], "v": item["edge_v"], "key": item.get("edge_key")}],
                "bidirectional": item["bidirectional"], "geometry": None, "latitude": None, "longitude": None,
                "match": None, "provider": None,
            })
        for rec in self.provider_records():
            inc, match = rec["incident"], rec["match"]
            matched = match.get("status") == "matched"
            applied = rec["routing_applied"] and not rec["stale"]
            if not matched:
                status = "unmatched"
            elif rec["stale"]:
                status = "stale"
            elif applied:
                status = "applied"
            else:
                status = "matched_not_applied"
            entries.append({
                "id": f"tomtom:{rec['provider_id']}", "source": "tomtom", "kind": inc["kind"], "description": inc["description"],
                "severity": inc["severity"], "severity_source": inc["severity_source"], "status": status,
                "matched": matched, "routing_applied": applied, "routing_skip_reason": rec.get("routing_skip_reason"),
                "confirmed": inc["confirmed"], "stale": rec["stale"],
                "start_time": inc["start_time"], "end_time": inc["end_time"], "expires_at": None,
                "created_at": rec["first_seen_at"], "last_seen_at": rec["last_seen_at"], "updated_at": rec["updated_at"],
                "provider_id": rec["provider_id"],
                "edges": [{"u": e["u"], "v": e["v"], "key": e.get("key"), "road_name": e.get("road_name"),
                           "length_m": e.get("length_m")} for e in match.get("edges", [])],
                "bidirectional": bool(match.get("bidirectional")), "geometry": inc["geometry"],
                "latitude": inc["latitude"], "longitude": inc["longitude"], "match": match,
                "provider": {k: inc.get(k) for k in ("category", "icon_category", "magnitude_of_delay", "magnitude_label",
                                                      "delay_seconds", "length_m", "from", "to", "road_numbers",
                                                      "time_validity", "probability_of_occurrence", "number_of_reports",
                                                      "last_report_time", "provider_id_synthetic")},
            })
        return entries

    def counts(self) -> dict[str, int]:
        entries = self.feed()
        tom = [e for e in entries if e["source"] == "tomtom"]
        return {
            "manual": sum(e["source"] == "manual" for e in entries),
            "simulation": sum(e["source"] == "simulation" for e in entries),
            "tomtom": len(tom),
            "tomtom_matched": sum(e["matched"] for e in tom),
            "tomtom_unmatched": sum(not e["matched"] for e in tom),
            "tomtom_applied_to_routing": sum(e["routing_applied"] for e in tom),
            "total": len(entries),
        }


class _FilteredIncidents:
    """Duck-typed stand-in for IncidentManager (what RouteOptimizer needs) that hides some sources."""

    def __init__(self, manager: IncidentManager, excluded: set[str]):
        self._m, self._excluded = manager, excluded

    def edge_effects(self):
        return self._m.edge_effects(self._excluded)

    def list_active(self):
        return [i for i in self._m.list_active() if i["source"] not in self._excluded]
