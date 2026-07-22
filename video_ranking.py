"""
X Video Ranking - replaces FR-04's Manual Review Queue on the dashboard.

Ranks video posts by views/likes/retweets, for each of the 5 shortest
shared time ranges (1h/4h/8h/1d/1w - see time_ranges.py). Two sources feed
into one pool: tracked accounts' own CSVs (hasVideo == 'yes', own +
competitors from accounts_config.json) plus platform-wide results from
scrape_video_discovery.js's keyword search (csv/video_discovery.csv, any
account) - this is intentionally NOT limited to accounts we track, since
the point is "what's the best video on X right now," not "how are our
tracked accounts doing on video." Metric selection happens client-side in
generate_dashboard.py: this script embeds all three metrics per post so no
per-metric file split is needed.

"now" is anchored to the freshest post timestamp across the video posts
themselves (same reasoning as fuzzy_trend.py/cluster_topics.py: scraping
lags wall-clock time, so "last hour" from real now would usually be empty).
"""
import csv
import json
import os

from cluster_topics import PLATFORM_ACCOUNTS, parse_count
from time_ranges import RANGE_HOURS, parse_ts, taiwan_str

BASE = os.path.dirname(__file__)
OUT_FILE = os.path.join(BASE, 'analysis', 'video_ranking.json')

# Only the 5 shortest ranges - 1mo/1q don't add anything for a ranking
# meant to surface what's currently taking off, and every extra range is
# another full pass over every account's CSV.
RANGES = ['1h', '4h', '8h', '1d', '1w']

# A rolling top-N cap per range, generous enough that whichever metric the
# dashboard's selector switches to still has a full top-10 to show without
# needing to refetch - client-side re-sorting only works if the metric that
# ends up "on top" was actually included in what got shipped to the page.
MAX_PER_RANGE = 50


def load_video_posts():
    posts = []
    seen_urls = set()
    cfg = PLATFORM_ACCOUNTS.get('X')
    if not cfg:
        return posts
    for handle in cfg['own'] + cfg['competitors']:
        path = os.path.join(cfg['dir'], f'{handle}.csv')
        if not os.path.exists(path):
            continue
        with open(path, newline='', encoding='utf-8') as f:
            for row in csv.DictReader(f):
                if (row.get('hasVideo') or '').strip().lower() != 'yes':
                    continue
                ts = parse_ts(row.get('timestamp'))
                if not ts:
                    continue
                url = row.get('tweetUrl') or ''
                if url:
                    seen_urls.add(url)
                posts.append({
                    'handle': handle,
                    'text': (row.get('text') or '').strip(),
                    'url': url,
                    'timestamp': row.get('timestamp'),
                    '_ts': ts,
                    'views': parse_count(row.get('views')),
                    'likes': parse_count(row.get('likes')),
                    'retweets': parse_count(row.get('retweets')),
                })

    # Platform-wide discovery (scrape_video_discovery.js, run on demand,
    # not yet on a fixed schedule) - any account's video post, not just
    # tracked own/competitor accounts. Dedup against tracked-account posts
    # by URL: a tracked competitor's video can legitimately also match a
    # discovery keyword search, and should only count once.
    discovery_path = os.path.join(cfg['dir'], 'video_discovery.csv')
    if os.path.exists(discovery_path):
        with open(discovery_path, newline='', encoding='utf-8') as f:
            for row in csv.DictReader(f):
                url = row.get('tweetUrl') or ''
                if url and url in seen_urls:
                    continue
                ts = parse_ts(row.get('timestamp'))
                if not ts:
                    continue
                if url:
                    seen_urls.add(url)
                posts.append({
                    'handle': (row.get('handle') or '').lstrip('@'),
                    'text': (row.get('text') or '').strip(),
                    'url': url,
                    'timestamp': row.get('timestamp'),
                    '_ts': ts,
                    'views': parse_count(row.get('views')),
                    'likes': parse_count(row.get('likes')),
                    'retweets': parse_count(row.get('retweets')),
                })
    return posts


def main():
    os.makedirs(os.path.join(BASE, 'analysis'), exist_ok=True)
    posts = load_video_posts()

    if not posts:
        print('No video posts found across any tracked X account, skipping.')
        return

    now = max(p['_ts'] for p in posts)
    result = {}
    for range_key in RANGES:
        hours = RANGE_HOURS[range_key]
        cutoff = now.timestamp() - hours * 3600
        in_window = [p for p in posts if p['_ts'].timestamp() >= cutoff]
        # Ship the top MAX_PER_RANGE by views (the highest-ceiling metric,
        # so whichever of the 3 metrics the dashboard switches to still has
        # a deep enough pool to rank from) rather than an arbitrary slice.
        top = sorted(in_window, key=lambda p: p['views'], reverse=True)[:MAX_PER_RANGE]
        result[range_key] = [
            {k: v for k, v in p.items() if k != '_ts'} for p in top
        ]
        print(f"[{range_key}] {len(in_window)} video post(s) in window, shipping top {len(top)}.")

    result['_window_end'] = now.isoformat()
    result['_window_end_tw'] = taiwan_str(now)

    with open(OUT_FILE, 'w', encoding='utf-8') as f:
        json.dump(result, f, ensure_ascii=False, indent=2)
    print(f"Wrote {OUT_FILE}")


if __name__ == '__main__':
    main()
