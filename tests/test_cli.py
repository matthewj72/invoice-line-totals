import contextlib
import io
import json
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from invoice_lines.cli import run

SAMPLE = (
    "invoice_id,invoice_total,amount\n"
    "INV-1,10.00,10.00\n"
    "INV-2,25.00,24.50\n"
)


def run_cli(args, stdin_text=None):
    stdin = io.StringIO(stdin_text) if stdin_text is not None else None
    out, err = io.StringIO(), io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        if stdin is not None:
            old_stdin, sys.stdin = sys.stdin, stdin
            try:
                exit_code = run(args)
            finally:
                sys.stdin = old_stdin
        else:
            exit_code = run(args)
    return exit_code, out.getvalue(), err.getvalue()


class JsonOutput(unittest.TestCase):
    def test_mismatch_only_by_default(self):
        exit_code, out, _ = run_cli(["-", "--json"], SAMPLE)
        lines = out.strip().splitlines()
        self.assertEqual(len(lines), 1)
        record = json.loads(lines[0])
        self.assertEqual(record["invoice_id"], "INV-2")
        self.assertFalse(record["match"])
        self.assertEqual(record["stated_total"], "25.00")
        self.assertEqual(record["computed_total"], "24.50")
        self.assertEqual(record["difference"], "-0.50")
        self.assertEqual(record["line_count"], 1)
        self.assertEqual(exit_code, 1)

    def test_all_emits_every_invoice_as_valid_json_lines(self):
        _, out, _ = run_cli(["-", "--json", "--all"], SAMPLE)
        lines = out.strip().splitlines()
        self.assertEqual(len(lines), 2)
        records = [json.loads(line) for line in lines]
        self.assertEqual([r["invoice_id"] for r in records], ["INV-1", "INV-2"])
        self.assertTrue(records[0]["match"])
        self.assertFalse(records[1]["match"])

    def test_all_matched_exits_zero(self):
        exit_code, out, _ = run_cli(
            ["-", "--json"], "invoice_id,invoice_total,amount\nINV-1,10.00,10.00\n"
        )
        self.assertEqual(exit_code, 0)
        self.assertEqual(out, "")


if __name__ == "__main__":
    unittest.main()
