"""Command-line entry point: `invoice-lines FILE` or `python -m invoice_lines FILE`."""

from __future__ import annotations

import argparse
import sys
from decimal import Decimal
from typing import List, Optional

from .reader import InvoiceTotal, MalformedRow, OutOfOrderInvoice, iter_invoice_totals


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="invoice-lines",
        description="Check that invoice line items sum to each invoice's stated total.",
    )
    parser.add_argument(
        "csv_path",
        help="path to a CSV of invoice line items, or - to read from stdin",
    )
    parser.add_argument(
        "--tolerance",
        type=Decimal,
        default=Decimal("0.01"),
        help="largest acceptable difference before an invoice is reported (default: 0.01)",
    )
    parser.add_argument(
        "--all",
        action="store_true",
        help="print every invoice checked, not just the ones that don't match",
    )
    return parser


def _open(path: str):
    if path == "-":
        return sys.stdin
    return open(path, newline="", encoding="utf-8")


def _print_result(total: InvoiceTotal, bad: bool) -> None:
    marker = "MISMATCH" if bad else "ok"
    print(
        f"{marker}\t{total.invoice_id}\tstated={total.stated_total}\t"
        f"computed={total.computed_total}\tdiff={total.difference}\tlines={total.line_count}"
    )


def run(argv: Optional[List[str]] = None) -> int:
    args = build_parser().parse_args(argv)
    checked = 0
    mismatches = 0

    source = _open(args.csv_path)
    try:
        for total in iter_invoice_totals(source):
            checked += 1
            bad = abs(total.difference) > args.tolerance
            if bad:
                mismatches += 1
            if bad or args.all:
                _print_result(total, bad)
    except (MalformedRow, OutOfOrderInvoice) as exc:
        print(f"invoice-lines: {exc}", file=sys.stderr)
        return 2
    finally:
        if source is not sys.stdin:
            source.close()

    print(f"\nchecked {checked} invoice(s), {mismatches} mismatch(es)", file=sys.stderr)
    return 1 if mismatches else 0


def main() -> None:
    raise SystemExit(run())


if __name__ == "__main__":
    main()
