"""Pre-download and cache the Bhubaneswar drivable road network (run once, ~1-5 minutes).

    python scripts/download_osm.py                 # download via Overpass
    python scripts/download_osm.py --file my.osm   # or import an offline .osm XML export

The dashboard and API reuse cache/bhubaneswar_drive.graphml afterwards.
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from backend.dependencies import get_provider  # noqa: E402


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--file", help="local .osm XML file to import instead of downloading")
    args = ap.parse_args()
    prov = get_provider()
    try:
        prov.load_osm_xml(args.file) if args.file else prov.download()
    except Exception as exc:
        print(f"FAILED: {exc}", file=sys.stderr)
        print("Tip: check your internet/firewall for overpass-api.de, or export an .osm file from openstreetmap.org and use --file.", file=sys.stderr)
        return 1
    s = prov.summary()
    print(f"OK  source={s['source']}  nodes={s['nodes']:,}  edges={s['edges']:,}  named={s['named_edges']:,}  one-way={s['oneway_edges']:,}")
    print(f"Cached at {prov.cache_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
