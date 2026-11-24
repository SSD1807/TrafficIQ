"""Write RELEASE_MANIFEST.json: every shipped file with exact size and SHA-256.

    python scripts/generate_manifest.py
Excluded by design: RELEASE_MANIFEST.json itself (it cannot contain its own hash), caches, bytecode, .env, local cache/ graphs.
Run it LAST, after every other file is final, then build the ZIP from the directory.
"""
from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
EXCLUDE_DIRS = {"__pycache__", ".pytest_cache", ".git", ".venv", "venv", "cache", "node_modules"}
EXCLUDE_FILES = {"RELEASE_MANIFEST.json", ".env", ".DS_Store"}


def release_files(root: Path = ROOT) -> list[Path]:
    out = []
    for p in sorted(root.rglob("*")):
        rel = p.relative_to(root)
        if p.is_file() and not (set(rel.parts[:-1]) & EXCLUDE_DIRS) and p.name not in EXCLUDE_FILES and p.suffix != ".pyc":
            out.append(p)
    return out


def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def main() -> int:
    files = [{"path": p.relative_to(ROOT).as_posix(), "size_bytes": p.stat().st_size, "sha256": sha256(p)} for p in release_files()]
    manifest = {
        "project": "Traffic Bottleneck Prediction & Smart Routing System",
        "release_type": "v3_final_pinned_verified",
        "release_date": "2026-11-10",
        "file_count_excluding_manifest": len(files),
        "excluded_by_design": ["RELEASE_MANIFEST.json (self-referential)", "__pycache__/", ".pytest_cache/", "cache/ (downloaded OSM graph)", ".env"],
        "honesty_statement": ("Synthetic-data demonstrator. Not a live Bhubaneswar traffic system. TomTom-to-edge routing is verified only against mocked "
                              "responses; real OSM download, live Open-Meteo, TomTom, OpenAI and Windows/Python 3.14 execution are unverified. See FEATURE_STATUS.md."),
        "files": files,
    }
    (ROOT / "RELEASE_MANIFEST.json").write_text(json.dumps(manifest, indent=2) + "\n")
    print(f"manifest written: {len(files)} files")
    return 0


if __name__ == "__main__":
    sys.exit(main())
