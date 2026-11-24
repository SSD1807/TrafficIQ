"""Verify the project against RELEASE_MANIFEST.json (standard library only).

    python scripts/verify_release.py [project_dir]
Exit 0 only if no file is missing, has a different size/SHA-256, or is present but unlisted.
"""
import hashlib, json, sys
from pathlib import Path

root = Path(sys.argv[1] if len(sys.argv) > 1 else Path(__file__).resolve().parents[1]).resolve()
EXCLUDE_DIRS = {"__pycache__", ".pytest_cache", ".git", ".venv", "venv", "node_modules", "dist", "cache", "shots"}
EXCLUDE_FILES = {"RELEASE_MANIFEST.json", "SHA256SUMS.txt", ".env", ".DS_Store"}
manifest = json.loads((root / "RELEASE_MANIFEST.json").read_text(encoding="utf-8"))
listed = {f["path"]: f for f in manifest["files"]}
missing, bad = [], []
for rel, f in listed.items():
    p = root / rel
    if not p.is_file():
        missing.append(rel); continue
    h = hashlib.sha256(p.read_bytes()).hexdigest()
    if p.stat().st_size != f["bytes"] or h != f["sha256"]:
        bad.append(rel)
unlisted = [p.relative_to(root).as_posix() for p in root.rglob("*")
            if p.is_file() and not (set(p.relative_to(root).parts) & EXCLUDE_DIRS) and p.name not in EXCLUDE_FILES
            and not p.name.startswith("e2e_result_") and p.relative_to(root).as_posix() not in listed]
print(f"listed={len(listed)} missing={len(missing)} mismatched={len(bad)} unlisted={len(unlisted)}")
for label, items in (("MISSING", missing), ("MISMATCH", bad), ("UNLISTED", unlisted)):
    for i in items[:20]:
        print(f"  {label}: {i}")
sys.exit(0 if not (missing or bad or unlisted) else 1)
