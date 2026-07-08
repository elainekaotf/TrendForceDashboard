"""
FR-07 Self-service Data Analysis & Export.

Lets a user upload their own platform export, instantly analyzes it, and
produces downloadable results - no need to wait for the scheduled FR-01/02/03
pipeline.

Format scope (SRS Open Issue #5, still TBC): implemented for CSV, the only
concretely-specified format so far. Excel input/output is not implemented -
this environment has no openpyxl/pandas installed - and true binary PDF
export needs reportlab/fpdf, also not installed. Rather than hand-roll a
brittle XLSX/PDF writer, export defaults to CSV (zero dependencies) plus a
self-contained, print-to-PDF-ready HTML report; add openpyxl/reportlab and
extend export_excel/export_pdf below once the format is confirmed.

Processing (mandatory time-zone conversion, NFR-01):
  - Auto-detect the source timestamp's UTC offset when the timestamp string
    already carries one (e.g. "...+00:00", "...Z").
  - Otherwise assume the naive timestamp is in --source-tz (default
    America/Los_Angeles, since the SRS notes uploads are "typically US
    time"), using zoneinfo so US Daylight Saving Time is handled correctly.
  - Convert everything to Asia/Taipei (UTC+8) before analysis.

Instant analysis reuses FR-03's sentiment engine (VADER) over an
auto-detected text column, plus basic volume/keyword/engagement stats.

CLI:
  python3 self_service_analysis.py <uploaded.csv> [--source-tz TZ]
      [--text-column NAME] [--timestamp-column NAME] [--out-dir DIR]
"""
import argparse
import csv
import json
import os
from collections import Counter
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

from cluster_topics import clean_text, parse_count
from nlp_sentiment import score_sentiment

BASE = os.path.dirname(__file__)
DEFAULT_OUT_DIR = os.path.join(BASE, 'analysis', 'self_service')
TARGET_TZ = ZoneInfo('Asia/Taipei')  # UTC+8, no DST

# Column names only ever matched exact English headers - a Traditional
# Chinese export (e.g. 內容/時間 instead of content/timestamp) found none of
# them and failed with "No text/content/message column found", which read
# as broken rather than "wrong header language." Added the common zh-TW
# header names actual exports use alongside the English ones.
TIMESTAMP_COLUMN_CANDIDATES = ['timestamp', 'created_at', 'date', 'exactDate', 'scrapedAt',
                                '時間', '日期', '發布時間', '發文時間', '貼文時間', '建立時間', '時間戳記']
TEXT_COLUMN_CANDIDATES = ['translated_text', 'text', 'content', 'message', 'body',
                           '內容', '文字', '貼文', '貼文內容', '內文', '文章內容', '訊息', '標題']
ENGAGEMENT_COLUMN_CANDIDATES = ['likes', 'reactions', 'retweets', 'shares', 'comments', 'replies',
                                 '讚', '按讚數', '讚數', '留言', '留言數', '評論', '評論數', '分享', '分享數', '轉發']

# datetime.fromisoformat() only accepts ISO 8601 - every other common export
# format (US-style MM/DD/YYYY, or Facebook's own scraper's "Thursday, July 2,
# 2026 at 1:00 PM" exactDate format, produced by the sibling scraper THIS
# same codebase uses) silently failed to parse, leaving converted_ts=None
# for those rows with no error shown - "self-service doesn't always work"
# was this, not a crash. Try ISO first, then these, in order.
FALLBACK_TIMESTAMP_FORMATS = [
    '%m/%d/%Y %I:%M:%S %p',
    '%m/%d/%Y %I:%M %p',
    '%m/%d/%Y %H:%M:%S',
    '%m/%d/%Y %H:%M',
    '%m/%d/%y %H:%M',
    '%B %d, %Y %I:%M %p',
    '%B %d, %Y at %I:%M %p',
    '%A, %B %d, %Y at %I:%M %p',
]


def detect_column(fieldnames, candidates, explicit=None):
    if explicit:
        if explicit not in fieldnames:
            raise ValueError(f"Column '{explicit}' not found in uploaded file. Columns: {fieldnames}")
        return explicit
    for c in candidates:
        if c in fieldnames:
            return c
    return None


def parse_naive_timestamp(raw):
    """Try ISO first, then the common non-ISO export formats. Returns a
    (possibly tz-aware) datetime, or None if nothing matched."""
    try:
        return datetime.fromisoformat(raw.replace('Z', '+00:00'))
    except ValueError:
        pass
    for fmt in FALLBACK_TIMESTAMP_FORMATS:
        try:
            return datetime.strptime(raw, fmt)
        except ValueError:
            continue
    return None


def parse_and_convert_timestamp(raw, source_tz):
    """Returns (utc8_iso_string, had_explicit_offset) or (None, False)."""
    if not raw:
        return None, False
    dt = parse_naive_timestamp(raw.strip())
    if dt is None:
        return None, False

    had_offset = dt.tzinfo is not None
    if not had_offset:
        dt = dt.replace(tzinfo=ZoneInfo(source_tz))
    return dt.astimezone(TARGET_TZ).isoformat(), had_offset


def load_uploaded_rows(path, source_tz, text_column=None, timestamp_column=None):
    with open(path, newline='', encoding='utf-8-sig') as f:
        reader = csv.DictReader(f)
        fieldnames = reader.fieldnames or []
        text_col = detect_column(fieldnames, TEXT_COLUMN_CANDIDATES, text_column)
        ts_col = detect_column(fieldnames, TIMESTAMP_COLUMN_CANDIDATES, timestamp_column)
        engagement_cols = [c for c in ENGAGEMENT_COLUMN_CANDIDATES if c in fieldnames]

        if not text_col:
            raise ValueError(f"Couldn't find a text column. Pass --text-column. Columns: {fieldnames}")

        rows = []
        offset_seen = 0
        for row in reader:
            raw_ts = row.get(ts_col) if ts_col else None
            converted_ts, had_offset = parse_and_convert_timestamp(raw_ts, source_tz)
            offset_seen += had_offset

            text = clean_text(row.get(text_col, ''))
            engagement = sum(parse_count(row.get(c)) for c in engagement_cols)
            sentiment, sentiment_score = score_sentiment(row.get(text_col, ''))

            enriched = dict(row)
            enriched['converted_timestamp_utc8'] = converted_ts
            enriched['sentiment'] = sentiment
            enriched['sentiment_score'] = round(sentiment_score, 4)
            rows.append({'raw': enriched, 'text': text, 'engagement': engagement,
                        'sentiment': sentiment, 'sentiment_score': sentiment_score,
                        'timestamp': converted_ts})

        return rows, fieldnames, text_col, ts_col, offset_seen


def analyze(rows):
    total = len(rows)
    sentiment_counts = Counter(r['sentiment'] for r in rows)
    total_engagement = sum(r['engagement'] for r in rows)

    term_counts = Counter()
    for r in rows:
        term_counts.update(w for w in r['text'].split() if len(w) > 2)

    by_day = Counter()
    for r in rows:
        if r['timestamp']:
            by_day[r['timestamp'][:10]] += 1

    return {
        'total_rows': total,
        'sentiment_counts': dict(sentiment_counts),
        'sentiment_share': {k: round(v / total, 3) for k, v in sentiment_counts.items()} if total else {},
        'total_engagement': total_engagement,
        'avg_engagement': round(total_engagement / total, 1) if total else 0,
        'top_terms': [t for t, _ in term_counts.most_common(15)],
        'posts_by_day_utc8': dict(sorted(by_day.items())),
    }


def export_csv(rows, fieldnames, out_path):
    out_fields = fieldnames + ['converted_timestamp_utc8', 'sentiment', 'sentiment_score']
    # Preserve column order, de-duplicating if the source already had these names.
    out_fields = list(dict.fromkeys(out_fields))
    with open(out_path, 'w', newline='', encoding='utf-8') as f:
        writer = csv.DictWriter(f, fieldnames=out_fields, extrasaction='ignore')
        writer.writeheader()
        for r in rows:
            writer.writerow(r['raw'])


def export_html_report(summary, meta, out_path):
    """Self-contained, print-to-PDF-ready report - stands in for a binary
    PDF export until a PDF library is added (see module docstring)."""
    rows_html = ''.join(
        f'<tr><td>{k}</td><td>{v}</td></tr>' for k, v in summary.items() if k != 'posts_by_day_utc8'
    )
    by_day_html = ''.join(
        f'<tr><td>{day}</td><td>{count}</td></tr>' for day, count in summary['posts_by_day_utc8'].items()
    )
    html = f"""<!doctype html>
<html><head><meta charset="utf-8"><title>Self-service Analysis Report</title>
<style>
body {{ font-family: sans-serif; margin: 2em; }}
table {{ border-collapse: collapse; margin-bottom: 2em; }}
td, th {{ border: 1px solid #ccc; padding: 6px 12px; text-align: left; }}
</style></head><body>
<h1>Self-service Analysis Report</h1>
<p>Source file: {meta['source_file']}<br>
Generated: {meta['generated_at']}<br>
Source timezone assumed: {meta['source_tz']} ({meta['rows_with_explicit_offset']} of {meta['total_rows']} rows had an explicit offset in their timestamp)<br>
All timestamps converted to Asia/Taipei (UTC+8).</p>
<h2>Summary</h2>
<table>{rows_html}</table>
<h2>Posts by day (UTC+8)</h2>
<table><tr><th>Day</th><th>Count</th></tr>{by_day_html}</table>
</body></html>"""
    with open(out_path, 'w', encoding='utf-8') as f:
        f.write(html)


def main():
    parser = argparse.ArgumentParser(description='FR-07 Self-service Data Analysis & Export')
    parser.add_argument('upload_path')
    parser.add_argument('--source-tz', default='America/Los_Angeles',
                        help="IANA timezone to assume for timestamps with no explicit offset (default: America/Los_Angeles, per the SRS's 'typically US time' note)")
    parser.add_argument('--text-column')
    parser.add_argument('--timestamp-column')
    parser.add_argument('--out-dir', default=DEFAULT_OUT_DIR)
    args = parser.parse_args()

    if not args.upload_path.lower().endswith('.csv'):
        raise SystemExit("Only CSV uploads are supported today (Excel needs openpyxl - see module docstring / SRS Open Issue #5).")

    rows, fieldnames, text_col, ts_col, offset_seen = load_uploaded_rows(
        args.upload_path, args.source_tz, args.text_column, args.timestamp_column)

    summary = analyze(rows)
    now = datetime.now(timezone.utc)
    meta = {
        'source_file': os.path.basename(args.upload_path),
        'generated_at': now.isoformat(),
        'text_column': text_col,
        'timestamp_column': ts_col,
        'source_tz': args.source_tz,
        'rows_with_explicit_offset': offset_seen,
        'total_rows': len(rows),
    }

    # On-screen results.
    print(json.dumps({'meta': meta, 'summary': summary}, ensure_ascii=False, indent=2))

    os.makedirs(args.out_dir, exist_ok=True)
    base_name = os.path.splitext(os.path.basename(args.upload_path))[0]
    csv_path = os.path.join(args.out_dir, f'{base_name}_analyzed.csv')
    html_path = os.path.join(args.out_dir, f'{base_name}_report.html')
    json_path = os.path.join(args.out_dir, f'{base_name}_summary.json')

    export_csv(rows, fieldnames, csv_path)
    export_html_report(summary, meta, html_path)
    with open(json_path, 'w', encoding='utf-8') as f:
        json.dump({'meta': meta, 'summary': summary}, f, ensure_ascii=False, indent=2)

    print(f"\nDownloads ready:\n  {csv_path}\n  {html_path} (print to PDF from a browser)\n  {json_path}")


if __name__ == '__main__':
    main()
