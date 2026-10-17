# Database change scripts

One file per change, named `NNNN_short_description.sql` (four digits, in order).
Run them with `python -m db.migrate`. Never edit a file that has already run
somewhere — add a new one instead.

A script runs inside one transaction: if any line fails, nothing from that
file is kept. `db.migrate` takes a backup first and refuses to start if the
backup fails.

By default, a script that leaves fewer rows in `propositions` than before it
ran is treated as a mistake and rolled back — that table is the 21,913
indexed facts, and no ordinary change should shrink it. A script that really
means to change it (for example a future cleanup job) says so on its own
first line: `-- allow-propositions-change`.
