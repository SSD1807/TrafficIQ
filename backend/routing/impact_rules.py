"""Incident impact rules: ONE place that decides how an incident changes road cost and whether a route is affected.

Everything numeric lives in :class:`ImpactRules` (defaults below, overridable with a JSON file named by the
``INCIDENT_IMPACT_RULES`` environment variable), so no threshold is buried in routing code.

How an incident becomes a routing effect (per matched directed road edge)
-------------------------------------------------------------------------
1. **Confirmed full closure** (provider category *Road closed*, certain/probable, active now, matched to a road edge):
   effect ``block`` - the edge is removed from the routing graph. Severity alone NEVER blocks a road: an accident
   with "major" delay stays passable.
2. **Everything else** is ``penalty``: ``travel_time = base_time * cost_factor + delay_s``.
   * ``cost_factor`` and the fallback delay come from the per-kind rule: ``1 + factor_per_severity * severity`` and
     ``delay_base_s + delay_per_severity_s * severity``.
   * If TomTom reported the incident's total ``delay`` (seconds, > 0; 0 means "no estimate"), that PROVIDER value is used instead (shared across the
     matched edges in proportion to their length) and the factor stays 1.0, so the delay is not counted twice.
   * ``severity`` is the provider's magnitudeOfDelay mapped to 0..1 (minor 0.3 / moderate 0.6 / major 0.9). When the
     provider gives none, a per-kind default is used and labelled ``severity_source = default_for_kind``.
3. Lane restrictions: TomTom's Incident Details API does not report the number of blocked lanes, so none is assumed.
   A *Lane closed* report is treated as a partial restriction (penalty), never as a closure.

Reroute decision (see ``routing.engine``)
-----------------------------------------
The engine prices the original (free-flow) route and the best alternative on the incident-priced graph. It recommends the
alternative when the original is blocked, or when it saves at least ``reroute_min_saving_s`` seconds AND
``reroute_min_saving_pct`` percent of the incident-priced time. Otherwise the original route is kept with a warning.
These are cost-model estimates: no live per-road speed is available from the incident feed, so the result is an
indication, not a precise travel-time prediction.

Impact score (0-100, for ranking and display only; the reroute decision uses the cost comparison above)::

    score = min(100, 100 * kind_weight * severity + min(length_points_cap, affected_km * length_points_per_km)
                                                  + min(delay_points_cap, delay_s / delay_s_per_point))
"""
from __future__ import annotations

import json
import os
from dataclasses import asdict, dataclass, field, fields
from pathlib import Path
from typing import Any


@dataclass
class KindRule:
    factor_per_severity: float
    delay_base_s: float
    delay_per_severity_s: float
    weight: float                       # share of full impact for the display score (closure = 1.0)


DEFAULT_KINDS: dict[str, KindRule] = {
    # The first four reproduce the cost model that shipped with the project, so existing behaviour is preserved.
    "accident": KindRule(2.0, 30.0, 180.0, 0.85),
    "traffic_jam": KindRule(1.2, 10.0, 60.0, 0.5),
    "road_work": KindRule(0.8, 15.0, 90.0, 0.55),
    "obstruction": KindRule(0.8, 15.0, 90.0, 0.6),
    "hazard": KindRule(0.8, 15.0, 90.0, 0.45),
}


@dataclass
class ImpactRules:
    kinds: dict[str, KindRule] = field(default_factory=lambda: {k: KindRule(**asdict(v)) for k, v in DEFAULT_KINDS.items()})
    closure_blocks: bool = True
    use_provider_delay: bool = True
    major_severity: float = 0.8         # >= major; magnitudeOfDelay 3 maps to 0.9
    moderate_severity: float = 0.5      # >= moderate; magnitudeOfDelay 2 maps to 0.6
    reroute_min_saving_s: float = 120.0
    reroute_min_saving_pct: float = 5.0
    max_provider_delay_s: float = 3 * 3600.0   # ignore absurd provider delays instead of pricing them
    length_points_cap: float = 15.0
    length_points_per_km: float = 5.0
    delay_points_cap: float = 15.0
    delay_s_per_point: float = 60.0

    # ------------------------------------------------------------------ construction
    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "ImpactRules":
        rules = cls()
        simple = {f.name for f in fields(cls)} - {"kinds"}
        for key, value in data.items():
            if key in simple:
                setattr(rules, key, type(getattr(rules, key))(value))
            elif key == "kinds":
                for kind, spec in value.items():
                    base = rules.kinds.get(kind, KindRule(1.0, 0.0, 0.0, 0.5))
                    rules.kinds[kind] = KindRule(**{**asdict(base), **spec})
            else:
                raise ValueError(f"Unknown impact rule '{key}'")
        return rules

    @classmethod
    def from_env(cls) -> "ImpactRules":
        path = os.getenv("INCIDENT_IMPACT_RULES", "").strip()
        if not path:
            return cls()
        return cls.from_dict(json.loads(Path(path).read_text(encoding="utf-8")))

    def to_public(self) -> dict[str, Any]:
        return asdict(self)

    # ------------------------------------------------------------------ classification
    def classify(self, kind: str, severity: float, *, confirmed: bool = True, category: str | None = None) -> str:
        if kind == "road_closure":
            return "confirmed_closure" if confirmed else "unconfirmed_closure"
        if kind == "accident":
            return "major_accident" if severity >= self.major_severity else "moderate_accident" if severity >= self.moderate_severity else "minor_accident"
        if kind == "road_work":
            return "construction"
        if kind == "obstruction":
            return "lane_restriction" if (category or "").lower().startswith("lane") else "obstruction"
        if kind == "traffic_jam":
            return "traffic_jam"
        return "hazard"

    # ------------------------------------------------------------------ per-edge effect used by the routing graph
    def edge_effect(self, record: dict[str, Any]) -> dict[str, Any]:
        """Effect of one edge-level incident record on that edge. Works for manual, simulated and provider records."""
        kind, severity = record["kind"], float(record.get("severity", 0.5))
        if kind == "road_closure":
            if self.closure_blocks:
                return {"effect": "block", "cost_factor": 1.0, "delay_s": 0.0, "delay_basis": "closure"}
            kind = "obstruction"
        rule = self.kinds.get(kind) or self.kinds["hazard"]
        provider_delay = record.get("edge_delay_s")
        if self.use_provider_delay and provider_delay is not None and 0 <= float(provider_delay) <= self.max_provider_delay_s:
            return {"effect": "penalty", "cost_factor": 1.0, "delay_s": float(provider_delay), "delay_basis": "provider"}
        return {"effect": "penalty", "cost_factor": 1.0 + rule.factor_per_severity * severity,
                "delay_s": rule.delay_base_s + rule.delay_per_severity_s * severity, "delay_basis": "rule_default"}

    # ------------------------------------------------------------------ incident-level analysis (display + explanation)
    def impact_score(self, kind: str, severity: float, *, affected_m: float = 0.0, delay_s: float | None = None) -> float:
        rule = self.kinds.get(kind)
        weight = 1.0 if kind == "road_closure" else (rule.weight if rule else 0.4)
        score = 100.0 * weight * float(severity)
        score += min(self.length_points_cap, (affected_m / 1000.0) * self.length_points_per_km)
        if delay_s:
            score += min(self.delay_points_cap, float(delay_s) / self.delay_s_per_point)
        return round(min(100.0, score), 1)

    def explain(self, incident: dict[str, Any], *, affected_m: float = 0.0) -> dict[str, Any]:
        """Human-readable, data-grounded impact explanation for one incident (provider or manual)."""
        kind, severity = incident["kind"], float(incident.get("severity", 0.5))
        confirmed = bool(incident.get("confirmed", True))
        cls = self.classify(kind, severity, confirmed=confirmed, category=incident.get("category"))
        delay = incident.get("delay_seconds")
        delay = delay if (delay is not None and float(delay) > 0) else None      # 0 = "no estimate reported"
        if cls == "confirmed_closure":
            effect, text = "block", "Confirmed road closure: the closed road segment is removed from routing."
        elif cls == "unconfirmed_closure":
            effect, text = "none", "Reported closure is not confirmed by the provider (probability too low); shown but not applied."
        else:
            effect = "penalty"
            basis = f"provider-reported delay of {int(delay)} s" if (delay is not None and self.use_provider_delay) else "rule-based delay (no provider delay reported)"
            text = f"{cls.replace('_', ' ').capitalize()}: road stays passable; a cost penalty is applied using {basis}."
        return {"impact_class": cls, "routing_effect": effect, "impact_score": self.impact_score(kind, severity, affected_m=affected_m, delay_s=delay),
                "explanation": text, "severity_source": incident.get("severity_source")}


_RULES: ImpactRules | None = None


def get_rules() -> ImpactRules:
    global _RULES
    if _RULES is None:
        _RULES = ImpactRules.from_env()
    return _RULES


def set_rules(rules: ImpactRules | None) -> None:
    """Override (tests) or reset (None) the process-wide rules."""
    global _RULES
    _RULES = rules
