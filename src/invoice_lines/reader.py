"""Streaming reader for invoice line-item exports.

iter_invoice_totals assumes rows belonging to the same invoice are
contiguous, which holds for every export I've dealt with so far
(QuickBooks, Xero, a plain SQL dump ordered by invoice_id). Under that
assumption we only ever need to hold one invoice's running total in
memory, no matter how large the file is. If rows for an invoice are
split apart, that means the export itself is unsorted, so we raise
instead of silently producing a wrong total for two half-groups.

iter_invoice_totals_unsorted drops that assumption for exports that
really do arrive out of order, at the cost of one running total per
distinct invoice_id instead of one for the whole file.
"""

from __future__ import annotations

import csv
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from typing import IO, Iterator, Optional, Set

# Canonical column name -> accepted header names, in preference order.
# QuickBooks and Xero both call the invoice's stated total something
# other than "invoice_total" depending on the report you export from,
# so we accept the aliases we've actually seen instead of making every
# user rename a column before this tool will read their file.
COLUMN_ALIASES = {
    "invoice_id": ("invoice_id",),
    "invoice_total": ("invoice_total", "total", "amount_due"),
    "amount": ("amount",),
}

# Symbols stripped from a money value before parsing. Position doesn't
# matter - exports put these before ("$10.00") or after ("10,00 €")
# the number, and sometimes with a space in between.
CURRENCY_SYMBOLS = "$€£¥₹"


class MalformedRow(ValueError):
    """A row is missing a required field or has a value we can't parse as money."""


class OutOfOrderInvoice(ValueError):
    """Rows for the same invoice were seen in two separate groups."""


@dataclass
class InvoiceTotal:
    invoice_id: str
    stated_total: Decimal
    computed_total: Decimal
    line_count: int

    @property
    def difference(self) -> Decimal:
        return self.computed_total - self.stated_total


def _normalize_number(raw: str) -> str:
    """Strip currency symbols and normalize a localized decimal separator.

    Handles the two grouping conventions seen in exports: US-style
    ("$1,234.56", comma thousands / dot decimal) and European-style
    ("1.234,56" or "10,50", dot thousands / comma decimal). When both
    a comma and a dot appear, whichever comes last is the decimal
    separator and the other is thousands grouping. When only a comma
    appears, it's read as a decimal separator if it's followed by one
    or two digits and nothing else (the shape of a fractional amount,
    "10,5") - otherwise it's thousands grouping ("1,234") and dropped.
    """
    text = raw.strip()
    for symbol in CURRENCY_SYMBOLS:
        text = text.replace(symbol, "")
    text = text.strip()

    has_comma = "," in text
    has_dot = "." in text
    if has_comma and has_dot:
        if text.rindex(",") > text.rindex("."):
            text = text.replace(".", "").replace(",", ".")
        else:
            text = text.replace(",", "")
    elif has_comma:
        head, _, tail = text.rpartition(",")
        if "," not in head and 1 <= len(tail) <= 2 and tail.isdigit():
            text = head + "." + tail
        else:
            text = text.replace(",", "")
    return text


def _to_decimal(raw: Optional[str], *, field: str, invoice_id: str) -> Decimal:
    try:
        return Decimal(_normalize_number(raw))
    except (InvalidOperation, AttributeError) as exc:
        raise MalformedRow(
            f"invoice {invoice_id!r}: could not parse {field!r} value {raw!r} as a number"
        ) from exc


def _resolve_columns(fieldnames: Optional[list]) -> dict:
    """Map each canonical column to the header name actually present.

    Raises MalformedRow naming the canonical column (not the alias) so
    the error message stays meaningful regardless of which alias the
    file used.
    """
    present = set(fieldnames or [])
    resolved = {}
    missing = []
    for canonical, aliases in COLUMN_ALIASES.items():
        found = next((a for a in aliases if a in present), None)
        if found is None:
            missing.append(canonical)
        else:
            resolved[canonical] = found
    if missing:
        raise MalformedRow(f"missing required column(s): {', '.join(missing)}")
    return resolved


def iter_invoice_totals(source: IO[str]) -> Iterator[InvoiceTotal]:
    """Stream invoice line items and yield one InvoiceTotal per invoice.

    `source` is any text-mode file-like object (an open file or stdin).
    Rows are consumed one at a time via csv.DictReader, and only the
    current invoice's running sum is kept in memory - the file is never
    read in full, so this scales to exports with millions of lines.
    """
    reader = csv.DictReader(source)
    columns = _resolve_columns(reader.fieldnames)

    closed_invoices: Set[str] = set()
    current_id: Optional[str] = None
    current_stated = Decimal("0")
    current_computed = Decimal("0")
    current_count = 0

    def flush() -> InvoiceTotal:
        return InvoiceTotal(
            invoice_id=current_id,
            stated_total=current_stated,
            computed_total=current_computed,
            line_count=current_count,
        )

    for row in reader:
        invoice_id = (row.get(columns["invoice_id"]) or "").strip()
        if not invoice_id:
            raise MalformedRow("row has an empty invoice_id")

        if invoice_id != current_id:
            if current_id is not None:
                yield flush()
                closed_invoices.add(current_id)
            if invoice_id in closed_invoices:
                raise OutOfOrderInvoice(
                    f"invoice {invoice_id!r} appeared again after another invoice "
                    "started - rows for one invoice must be contiguous"
                )
            current_id = invoice_id
            current_stated = _to_decimal(
                row.get(columns["invoice_total"]), field="invoice_total", invoice_id=invoice_id
            )
            current_computed = Decimal("0")
            current_count = 0

        current_computed += _to_decimal(
            row.get(columns["amount"]), field="amount", invoice_id=invoice_id
        )
        current_count += 1

    if current_id is not None:
        yield flush()


def iter_invoice_totals_unsorted(source: IO[str]) -> Iterator[InvoiceTotal]:
    """Same as iter_invoice_totals, but for exports where a given
    invoice's rows aren't necessarily contiguous.

    This gives up the O(1) memory guarantee - it keeps one running
    total per distinct invoice_id instead of one for the whole file -
    but it's still far cheaper than loading the file, since a line
    item is folded into its invoice's sum and discarded rather than
    kept around. Memory scales with the number of distinct invoices,
    not the number of rows.
    """
    reader = csv.DictReader(source)
    columns = _resolve_columns(reader.fieldnames)

    accumulators: dict = {}

    for row in reader:
        invoice_id = (row.get(columns["invoice_id"]) or "").strip()
        if not invoice_id:
            raise MalformedRow("row has an empty invoice_id")

        entry = accumulators.get(invoice_id)
        if entry is None:
            stated = _to_decimal(
                row.get(columns["invoice_total"]), field="invoice_total", invoice_id=invoice_id
            )
            entry = [stated, Decimal("0"), 0]
            accumulators[invoice_id] = entry

        entry[1] += _to_decimal(
            row.get(columns["amount"]), field="amount", invoice_id=invoice_id
        )
        entry[2] += 1

    for invoice_id, (stated, computed, count) in accumulators.items():
        yield InvoiceTotal(
            invoice_id=invoice_id,
            stated_total=stated,
            computed_total=computed,
            line_count=count,
        )
