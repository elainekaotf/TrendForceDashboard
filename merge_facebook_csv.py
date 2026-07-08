#!/usr/bin/env python3
"""
Merges a Facebook handle's dated CSVs (facebook_<handle>_<date>.csv, one
per day the scraper ran, each holding only that day's new posts) into a
single csv/facebook/<handle>.csv - schema-aware, unlike a plain `cat`.

Why this exists: sync_data.sh used to concatenate dated files with
`cat`/`tail -n +2` under the *first* file's header. That's fine only as
long as every dated file has identical columns - it broke the day the
scraper added a `reactionsBreakdown` column: newer rows got read under
the old, shorter header, shifting every field one column over and
leaving `text` empty (the real text ended up stranded under an
unnamed overflow key). Reading each dated file with its OWN header via
DictReader, then writing the union of all fieldnames (missing values
just blank), makes this immune to the source schema changing over time.

Usage: python3 merge_facebook_csv.py <output.csv> <dated1.csv> [dated2.csv ...]
"""
import csv
import sys


def main():
    if len(sys.argv) < 3:
        print(__doc__)
        sys.exit(1)
    out_path, dated_paths = sys.argv[1], sys.argv[2:]

    fieldnames = []
    seen = set()
    all_rows = []
    for path in dated_paths:
        with open(path, newline='', encoding='utf-8') as f:
            reader = csv.DictReader(f)
            for name in reader.fieldnames or []:
                if name not in seen:
                    seen.add(name)
                    fieldnames.append(name)
            for row in reader:
                row.pop(None, None)  # DictReader's overflow key for ragged rows
                all_rows.append(row)

    with open(out_path, 'w', newline='', encoding='utf-8') as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames, restval='')
        writer.writeheader()
        writer.writerows(all_rows)

    print(f"Merged {len(dated_paths)} file(s), {len(all_rows)} row(s), {len(fieldnames)} column(s) -> {out_path}")


if __name__ == '__main__':
    main()
