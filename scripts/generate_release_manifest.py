"""Write RELEASE_MANIFEST.json and SHA256SUMS.txt for the project (standard library only).

    python scripts/generate_release_manifest.py
Excluded: virtual envs, node_modules, dist, caches, .env files, the manifest files themselves.
"""
import hashlib, json, sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
EXCLUDE_DIRS = {"__pycache__", ".pytest_cache", ".git", ".venv", "venv", "node_modules", "dist", "cache", "shots"}
EXCLUDE_FILES = {"RELEASE_MANIFEST.json", "SHA256SUMS.txt", ".env", ".DS_Store"}


def files():
    for p in sorted(ROOT.rglob("*")):
        rel = p.relative_to(ROOT)
        if p.is_file() and not (set(rel.parts) & EXCLUDE_DIRS) and p.name not in EXCLUDE_FILES and not p.name.startswith("e2e_result_"):
            yield rel


def sha(p: Path) -> str:
    h = hashlib.sha256()
    with p.open("rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


entries = [{"path": r.as_posix(), "bytes": (ROOT / r).stat().st_size, "sha256": sha(ROOT / r)} for r in files()]
(ROOT / "RELEASE_MANIFEST.json").write_text(json.dumps(
    {"project": "Traffic_Bottleneck_Final", "build_date": "2026-11-12", "file_count": len(entries),
     "total_bytes": sum(e["bytes"] for e in entries), "files": entries}, indent=1), encoding="utf-8")
(ROOT / "SHA256SUMS.txt").write_text("".join(f"{e['sha256']}  {e['path']}\n" for e in entries), encoding="utf-8")
print(f"{len(entries)} files hashed")
