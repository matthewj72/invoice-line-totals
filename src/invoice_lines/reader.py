"""Streaming reader for invoice line-item exports.

The reader assumes rows belonging to the same invoice are contiguous,
which holds for every export I've dealt with so far (QuickBooks, Xero,
a plain SQL dump ordered by invoice_id). Under that assumption we only
ever need to hold one invoice's running total in memory, no matter how
large the file is. If rows for an invoice are split apart, that means
the export itself is unsorted, so we raise instead of silently
producing a wrong total for two half-groups.
"""

from __future__ import annotations

import csv
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from typing import IO, Iterator, Optional, Set

REQUIRED_COLUMNS = ("invoice_id", "invoice_total", "amount")


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


def _to_decimal(raw: str, *, field: str, invoice_id: str) -> Decimal:
    try:
        return Decimal(raw.strip())
    except (InvalidOperation, AttributeError) as exc:
        raise MalformedRow(
            f"invoice {invoice_id!r}: could not parse {field!r} value {raw!r} as a number"
        ) from exc


def iter_invoice_totals(source: IO[str]) -> Iterator[InvoiceTotal]:
    """Stream invoice line items and yield one InvoiceTotal per invoice.

    `source` is any text-mode file-like object (an open file or stdin).
    Rows are consumed one at a time via csv.DictReader, and only the
    current invoice's running sum is kept in memory - the file is never
    read in full, so this scales to exports with millions of lines.
    """
    reader = csv.DictReader(source)
    missing = [c for c in REQUIRED_COLUMNS if c not in (reader.fieldnames or [])]
    if missing:
        raise MalformedRow(f"missing required column(s): {', '.join(missing)}")

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
        invoice_id = (row.get("invoice_id") or "").strip()
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
            current_stated = _to_decimal(row["invoice_total"], field="invoice_total", invoice_id=invoice_id)
            current_computed = Decimal("0")
            current_count = 0

        current_computed += _to_decimal(row["amount"], field="amount", invoice_id=invoice_id)
        current_count += 1

    if current_id is not None:
        yield flush()
