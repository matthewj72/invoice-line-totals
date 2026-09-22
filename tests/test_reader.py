import io
import sys
import unittest
from decimal import Decimal
from pathlib import Path

# src layout isn't on sys.path unless the package is installed; add it so
# these tests run with plain `python -m unittest discover` too.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from invoice_lines.reader import (
    MalformedRow,
    OutOfOrderInvoice,
    _normalize_number,
    iter_invoice_totals,
    iter_invoice_totals_unsorted,
)


def run(csv_text: str):
    return list(iter_invoice_totals(io.StringIO(csv_text)))


def run_unsorted(csv_text: str):
    return list(iter_invoice_totals_unsorted(io.StringIO(csv_text)))


class BasicGrouping(unittest.TestCase):
    def test_single_invoice_matches(self):
        totals = run(
            "invoice_id,invoice_total,amount\n"
            "INV-1,10.00,4.00\n"
            "INV-1,10.00,6.00\n"
        )
        self.assertEqual(len(totals), 1)
        total = totals[0]
        self.assertEqual(total.invoice_id, "INV-1")
        self.assertEqual(total.stated_total, Decimal("10.00"))
        self.assertEqual(total.computed_total, Decimal("10.00"))
        self.assertEqual(total.difference, Decimal("0"))
        self.assertEqual(total.line_count, 2)

    def test_mismatch_reports_difference(self):
        total = run(
            "invoice_id,invoice_total,amount\n"
            "INV-1,10.00,4.00\n"
        )[0]
        self.assertEqual(total.difference, Decimal("-6.00"))

    def test_multiple_contiguous_invoices(self):
        totals = run(
            "invoice_id,invoice_total,amount\n"
            "INV-1,5.00,5.00\n"
            "INV-2,7.00,3.00\n"
            "INV-2,7.00,4.00\n"
        )
        self.assertEqual([t.invoice_id for t in totals], ["INV-1", "INV-2"])
        self.assertEqual(totals[1].computed_total, Decimal("7.00"))

    def test_single_line_invoices_are_flushed(self):
        # regression case: a one-row invoice followed by end of file must
        # still be yielded, not dropped because there was no "next" row
        # to trigger the boundary check.
        totals = run("invoice_id,invoice_total,amount\nINV-1,1.00,1.00\n")
        self.assertEqual(len(totals), 1)

    def test_empty_file_yields_nothing(self):
        totals = run("invoice_id,invoice_total,amount\n")
        self.assertEqual(totals, [])


class ColumnAliases(unittest.TestCase):
    def test_total_alias_for_invoice_total(self):
        total = run("invoice_id,total,amount\nINV-1,10.00,10.00\n")[0]
        self.assertEqual(total.stated_total, Decimal("10.00"))

    def test_amount_due_alias_for_invoice_total(self):
        total = run("invoice_id,amount_due,amount\nINV-1,10.00,4.00\n")[0]
        self.assertEqual(total.stated_total, Decimal("10.00"))
        self.assertEqual(total.difference, Decimal("-6.00"))

    def test_invoice_total_preferred_over_alias(self):
        # if a file somehow has both, the canonical name wins
        total = run(
            "invoice_id,invoice_total,total,amount\nINV-1,10.00,999.00,10.00\n"
        )[0]
        self.assertEqual(total.stated_total, Decimal("10.00"))

    def test_missing_invoice_total_and_aliases_raises(self):
        with self.assertRaises(MalformedRow):
            run("invoice_id,amount\nINV-1,10.00\n")


class MalformedInput(unittest.TestCase):
    def test_missing_required_column(self):
        with self.assertRaises(MalformedRow):
            run("invoice_id,amount\nINV-1,1.00\n")

    def test_empty_invoice_id(self):
        with self.assertRaises(MalformedRow):
            run("invoice_id,invoice_total,amount\n,10.00,10.00\n")

    def test_unparseable_amount(self):
        with self.assertRaises(MalformedRow):
            run("invoice_id,invoice_total,amount\nINV-1,10.00,nope\n")

    def test_unparseable_invoice_total(self):
        with self.assertRaises(MalformedRow):
            run("invoice_id,invoice_total,amount\nINV-1,nope,10.00\n")

    def test_missing_amount_field_on_row(self):
        with self.assertRaises(MalformedRow):
            run("invoice_id,invoice_total,amount\nINV-1,10.00\n")


class LocalizedNumbers(unittest.TestCase):
    def test_dollar_sign_prefix(self):
        self.assertEqual(_normalize_number("$10.00"), "10.00")

    def test_euro_sign_suffix_with_space(self):
        self.assertEqual(_normalize_number("10,00 €"), "10.00")

    def test_us_thousands_separator(self):
        self.assertEqual(_normalize_number("$1,234.56"), "1234.56")

    def test_european_thousands_separator(self):
        self.assertEqual(_normalize_number("1.234,56"), "1234.56")

    def test_comma_decimal_separator(self):
        self.assertEqual(_normalize_number("10,5"), "10.5")

    def test_bare_comma_thousands_no_decimal(self):
        self.assertEqual(_normalize_number("1,234"), "1234")

    def test_negative_amount_with_symbol(self):
        self.assertEqual(_normalize_number("-$5.00"), "-5.00")

    def test_full_row_with_european_format(self):
        # the amounts contain a comma, so they must be quoted to survive
        # as single CSV fields
        total = run(
            "invoice_id,invoice_total,amount\n"
            'INV-1,"€1.234,56","€1.234,56"\n'
        )[0]
        self.assertEqual(total.stated_total, Decimal("1234.56"))
        self.assertEqual(total.difference, Decimal("0"))


class OutOfOrderInput(unittest.TestCase):
    def test_reopened_invoice_raises(self):
        with self.assertRaises(OutOfOrderInvoice):
            run(
                "invoice_id,invoice_total,amount\n"
                "INV-1,10.00,5.00\n"
                "INV-2,3.00,3.00\n"
                "INV-1,10.00,5.00\n"
            )


class UnsortedGrouping(unittest.TestCase):
    def test_split_invoice_rows_are_reunited(self):
        totals = run_unsorted(
            "invoice_id,invoice_total,amount\n"
            "INV-1,10.00,5.00\n"
            "INV-2,3.00,3.00\n"
            "INV-1,10.00,5.00\n"
        )
        by_id = {t.invoice_id: t for t in totals}
        self.assertEqual(set(by_id), {"INV-1", "INV-2"})
        self.assertEqual(by_id["INV-1"].computed_total, Decimal("10.00"))
        self.assertEqual(by_id["INV-1"].line_count, 2)
        self.assertEqual(by_id["INV-2"].computed_total, Decimal("3.00"))

    def test_result_order_matches_first_appearance(self):
        totals = run_unsorted(
            "invoice_id,invoice_total,amount\n"
            "INV-2,3.00,3.00\n"
            "INV-1,10.00,5.00\n"
            "INV-2,3.00,0.00\n"
            "INV-1,10.00,5.00\n"
        )
        self.assertEqual([t.invoice_id for t in totals], ["INV-2", "INV-1"])

    def test_stated_total_taken_from_first_row_seen(self):
        # matches iter_invoice_totals: only the first occurrence's
        # invoice_total is authoritative, later repeats are ignored.
        total = run_unsorted(
            "invoice_id,invoice_total,amount\n"
            "INV-1,10.00,5.00\n"
            "INV-1,999.00,5.00\n"
        )[0]
        self.assertEqual(total.stated_total, Decimal("10.00"))

    def test_contiguous_input_still_works(self):
        totals = run_unsorted(
            "invoice_id,invoice_total,amount\n"
            "INV-1,10.00,4.00\n"
            "INV-1,10.00,6.00\n"
        )
        self.assertEqual(len(totals), 1)
        self.assertEqual(totals[0].difference, Decimal("0"))

    def test_empty_invoice_id_raises(self):
        with self.assertRaises(MalformedRow):
            run_unsorted("invoice_id,invoice_total,amount\n,10.00,10.00\n")


if __name__ == "__main__":
    unittest.main()
