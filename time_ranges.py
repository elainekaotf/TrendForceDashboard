"""
Shared time-range vocabulary for FR-01/02/03, so "4h / 8h / 1 day / 1 week /
1 quarter" means the same thing everywhere instead of each script inventing
its own labels (FR-03 previously had hourly/4h/daily/monthly/quarterly,
which didn't line up with FR-01/02 at all).
"""
from datetime import datetime, timedelta

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
