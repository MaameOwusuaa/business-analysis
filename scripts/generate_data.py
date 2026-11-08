"""Regenerate the synthetic Kasa Telecom dataset.

Examples
--------
Write CSVs only (no database needed)::

    python scripts/generate_data.py --out data/generated

Write CSVs and load them into a workspace::

    python scripts/generate_data.py --out data/generated --load-workspace 1

The same --seed always produces byte-identical CSVs (see manifest.json).
"""
from __future__ import annotations

import argparse
import sys
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from insightforge.datagen.telco import GeneratorConfig, generate, write_csvs  # noqa: E402


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--customers", type=int, default=8000)
    ap.add_argument("--seed", type=int, default=42)
    ap.add_argument("--as-of", type=date.fromisoformat, default=date(2026, 9, 30))
    ap.add_argument("--out", type=Path, default=Path("data/generated"))
    ap.add_argument("--clean", action="store_true", help="skip injecting data-quality issues")
    ap.add_argument("--load-workspace", type=int, help="also load into this workspace id")
    args = ap.parse_args()

    cfg = GeneratorConfig(n_customers=args.customers, seed=args.seed, as_of=args.as_of,
                          imperfections=not args.clean)
    data = generate(cfg)
    manifest = write_csvs(data, args.out, cfg)
    for name, info in manifest["tables"].items():
        print(f"{name:<16} {info['rows']:>8,} rows  sha256={info['sha256'][:12]}")
    if args.load_workspace:
        from insightforge.ingest import load_synthetic

        ds = load_synthetic(args.load_workspace, args.customers, args.seed, args.as_of)
        print(f"Loaded into workspace {args.load_workspace} as dataset {ds}")


if __name__ == "__main__":
    main()
