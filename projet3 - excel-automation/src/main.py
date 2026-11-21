"""One command: every .xlsx in input/ -> one clean consolidated report in output/.

    python -m src.main
    python -m src.main --input "C:/path/to/client/files"
"""
import argparse
import logging
from pathlib import Path

from . import clean, report

ROOT = Path(__file__).resolve().parent.parent


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    ap = argparse.ArgumentParser()
    ap.add_argument("--input", type=Path, default=ROOT / "input")
    ap.add_argument("--output", type=Path, default=ROOT / "output" / "consolidated_sales.xlsx")
    args = ap.parse_args()

    good, rejected, errors = clean.load_folder(args.input)
    path = report.build(good, rejected, errors, args.output)
    logging.info("%d files -> %d clean rows, %d rejected, %d skipped -> %s",
                 good["source_file"].nunique(), len(good), len(rejected), len(errors), path)


if __name__ == "__main__":
    main()
