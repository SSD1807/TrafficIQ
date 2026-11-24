"""Independently verify a project directory against its RELEASE_MANIFEST.json.

    python scripts/verify_manifest.py [project_dir]
Checks, for every listed file: exists, exact size, exact SHA-256. Also reports files present on disk but NOT listed.
Exit code 0 only if there are no missing, mismatched or unlisted files. Uses only the standard library (does not import generate_manifest).
"""
from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path

EXCLUDE_DIRS = {"__pycache__", ".pytest_cache", ".git", ".venv", "venv", "cache", "node_modules"}
EXCLUDE_FILES = {"RELEASE_MANIFEST.json", ".env", ".DS_Store"}


def main() -> int:
    root = Path(sys.argv[1] if len(sys.argv) > 1 else Path(__file__).resolve().parents[1]).resolve()
    manifest = json.loads((root / "RELEASE_MANIFEST.json").read_text())
    listed = {f["path"]: f for f in manifest["files"]}
    missing, bad_size, bad_hash = [], [], []
    for rel, f in listed.items():
        p = root / rel
        if not p.is_file():
            missing.append(rel); continue
        if p.stat().st_size != f["size_bytes"]:
            bad_size.append(rel)
        if hashlib.sha256(p.read_bytes()).hexdigest() != f["sha256"]:
            bad_hash.append(rel)
    on_disk = {p.relative_to(root).as_posix() for p in root.rglob("*")
               if p.is_file() and not (set(p.relative_to(root).parts[:-1]) & EXCLUDE_DIRS) and p.name not in EXCLUDE_FILES and p.suffix != ".pyc"}
    unlisted = sorted(on_disk - set(listed))
    print(f"manifest entries: {len(listed)} | declared count: {manifest['file_count_excluding_manifest']} | files on disk (excl. manifest): {len(on_disk)}")
    print(f"missing: {len(missing)} | size mismatch: {len(bad_size)} | sha256 mismatch: {len(bad_hash)} | unlisted on disk: {len(unlisted)}")
    for label, items in (("MISSING", missing), ("SIZE", bad_size), ("HASH", bad_hash), ("UNLISTED", unlisted)):
        for i in items[:20]:
            print(f"  {label}: {i}")
    ok = not (missing or bad_size or bad_hash or unlisted) and len(listed) == manifest["file_count_excluding_manifest"] == len(on_disk)
    print("RESULT:", "PASS - every entry verified, no unlisted files" if ok else "FAIL")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
