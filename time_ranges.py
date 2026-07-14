"""
Shared time-range vocabulary for FR-01/02/03, so "hourly / 4h / 8h / 1 day /
1 week / 1 month / 1 quarter" means the same thing everywhere instead of
each script inventing its own labels (FR-03 previously had its own
hourly/4h/daily/monthly/quarterly set that didn't line up with FR-01/02
at all).

FR-03's Time Range row asks for hourly / 4h / daily / monthly / quarterly;
FR-01/02 only need 4h/8h/1d/1w/1q. Rather than substitute FR-03's ask down
to the smaller shared set (the previous approach here), this set is the
union of both: '1h' and '1mo' added specifically to satisfy FR-03, with
'8h'/'1w' kept for FR-01/02's own windows and general dashboard usefulness.
Every range applies uniformly across FR-01/02/03's tabs (one shared
dropdown) - '1h'/'1mo' being available on the Topic Gaps/Rising Trends
tabs too is a harmless side effect of one shared vocabulary, not a new
requirement for those.
"""
from datetime import datetime, timedelta, timezone

RANGE_HOURS = {
    '1h': 1,
    '4h': 4,
    '8h': 8,
    '1d': 24,
    '1w': 24 * 7,
    '1mo': 24 * 30,
    '1q': 24 * 90,
}
RANGE_LABELS = {
    '1h': 'Last hour',
    '4h': 'Last 4 hours',
    '8h': 'Last 8 hours',
    '1d': 'Last day',
    '1w': 'Last week',
    '1mo': 'Last month',
    '1q': 'Last quarter',
}
RANGE_ORDER = ['1h', '4h', '8h', '1d', '1w', '1mo', '1q']

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
