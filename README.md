# invoice-lines

A lot of invoicing systems export line items denormalized: one row per
line, with the invoice's stated total repeated on every row belonging
to that invoice. Rounding bugs, manual edits, and partial refunds mean
the lines don't always add up to that total. This tool answers one
question: for each invoice in a CSV export, do the line amounts sum to
the stated total, and if not, by how much are they off?

It's meant for reconciling exports before they get imported somewhere
that assumes they're already correct.

## input format

A CSV with at least these columns (extra columns are ignored):

```
invoice_id,invoice_total,line_no,description,amount
INV-1001,149.97,1,Widget A,49.99
INV-1001,149.97,2,Widget B,99.98
INV-1002,25.00,1,Consulting hour,24.50
INV-1003,300.00,1,Setup fee,150.00
INV-1003,300.00,2,Monthly plan,150.00
```

Rows for the same invoice must be contiguous - that's what lets the
reader stream the file instead of loading it into memory. Every export
I've worked with is already ordered that way (it's how the underlying
SQL query is usually written), but if yours isn't, sort it by
`invoice_id` first.

## usage

```
$ python -m invoice_lines sample.csv
MISMATCH	INV-1002	stated=25.00	computed=24.50	diff=-0.50	lines=1

checked 3 invoice(s), 1 mismatch(es)
```

Exit code is `0` if every invoice matched within tolerance, `1` if any
mismatched, `2` if the input itself was malformed (bad column, unsorted
invoice groups, unparseable amount).

Show every invoice, not just mismatches:

```
$ python -m invoice_lines sample.csv --all
ok	INV-1001	stated=149.97	computed=149.97	diff=0.00	lines=2
MISMATCH	INV-1002	stated=25.00	computed=24.50	diff=-0.50	lines=1
ok	INV-1003	stated=300.00	computed=300.00	diff=0.00	lines=2

checked 3 invoice(s), 1 mismatch(es)
```

Tighten or loosen the tolerance (default is 0.01):

```
$ python -m invoice_lines sample.csv --tolerance 0
```

Read from a pipe instead of a file:

```
$ cat sample.csv | python -m invoice_lines -
```

## why streaming matters here

Some of the exports this is meant for run into the millions of lines.
`iter_invoice_totals` in `reader.py` reads the CSV one row at a time
with `csv.DictReader` and keeps only the current invoice's running sum
in memory - never the whole file, and never a whole invoice's line
list (just its total and a count). Memory use is flat regardless of
input size. The tradeoff is the contiguity requirement above: this
is not a general groupby, it's a streaming one, so it can't reorder
rows for you.

## status

Early skeleton. The CLI and reader work end to end on well-formed
input; see the roadmap in the issue tracker for what's still missing
(tests, alternate column names, non-contiguous input).

## license

MIT, see LICENSE.
