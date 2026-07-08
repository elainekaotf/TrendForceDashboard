"""
Shared time-range vocabulary for FR-01/02/03, so "4h / 8h / 1 day / 1 week /
1 quarter" means the same thing everywhere instead of each script inventing
its own labels (FR-03 previously had hourly/4h/daily/monthly/quarterly,
which didn't line up with FR-01/02 at all).

Gap vs. spec: the SRS's FR-03 Time Range row literally asks for hourly /
4h / daily / monthly / quarterly - this set (no hourly, weekly instead
of monthly) is a deliberate substitution for one shared vocabulary
across FR-01/02/03, not what FR-03 alone specifies.
"""
from datetime import datetime, timedelta, timezone

RANGE_HOURS = {
    '4h': 4,
    '8h': 8,
    '1d': 24,
    '1w': 24 * 7,
    '1q': 24 * 90,
}
RANGE_LABELS = {
    '4h': 'Last 4 hours',
    '8h': 'Last 8 hours',
    '1d': 'Last day',
    '1w': 'Last week',
    '1q': 'Last quarter',
}
RANGE_ORDER = ['4h', '8h', '1d', '1w', '1q']

# Below this many posts in a window, clustering/ranking gets noisy - callers
# should skip or fall back rather than report an unstable result.
MIN_WINDOW_POSTS = 5


def parse_ts(ts):
    if not ts:
        return None
    try:
        return datetime.fromisoformat(ts.replace('Z', '+00:00'))
    except ValueError:
        return None


def window_bounds(range_key, now):
    hours = RANGE_HOURS[range_key]
    return now - timedelta(hours=hours), now


def format_window(hours):
    """Human-readable window length for rationale strings ("4h" vs "7d")."""
    if hours < 24:
        return f"{hours}h"
    return f"{hours // 24}d"


TAIWAN_TZ = timezone(timedelta(hours=8))


def taiwan_str(dt):
    return dt.astimezone(TAIWAN_TZ).strftime('%b %d, %H:%M')


def window_dict(start, end):
    """JSON-friendly window bounds, in UTC ISO and pre-formatted Taiwan time -
    every range script's "now" is the latest scraped post, not wall-clock
    time, so callers must show this explicitly (see the dashboard's range
    caption) rather than implying "now" means "right now"."""
    return {
        'start_utc': start.isoformat(),
        'end_utc': end.isoformat(),
        'start_tw': taiwan_str(start),
        'end_tw': taiwan_str(end),
    }
