"""
FR-03 NLP Sentiment Dashboard.

Analyzes audience preferences via NLP (tokenization already done upstream by
the scrapers into `translated_text`/`keywords`) + sentiment analysis (VADER,
same engine used by TrendforceTwitterScraper/sentiment.py) + a fuzzy decision
layer that fuses volume/engagement into heat & focus scores.

Time range is selectable (hourly / 4h / daily / monthly / quarterly); all
widgets recompute over the selected range. Reuses FR-01's topic clusters
(cluster_topics.py) for topic-shaped widgets.

Widgets (FR-03-01..09):
  01 sentiment_overview        - real-time snapshot of volume/sentiment/topics
  02 temperature_bar           - heat score per topic (hot -> cold)
  03 sentiment_trend_curve     - positive/neutral/negative counts over time
  04 competitor_mentions       - mention counts of a keyword across accounts
  05 platform_share_bar        - share-of-voice of a keyword across platforms
  06 platform_keyword_ranking  - per-platform ranking for a keyword
  07 coverage_focus_ranking    - each account's top-covered topic
  08 top_engagement_ranking    - highest-engagement topics
  09 posting_timeslot_analysis - Mon-Fri by time slot: volume/likes/engagement

Platforms are derived from whatever FR-01's load_posts() returns (currently
X and Facebook; LinkedIn is not yet scraped, see SRS Open Issue #3).

Output: analysis/sentiment_dashboard.json
"""
import json
import os
from collections import defaultdict, Counter
from datetime import datetime, timedelta, timezone

from vaderSentiment.vaderSentiment import SentimentIntensityAnalyzer

from cluster_topics import ACCOUNTS, OWN_ACCOUNT, N_CLUSTERS, load_posts, label_cluster, cluster_posts

BASE = os.path.dirname(__file__)
OUT_FILE = os.path.join(BASE, 'analysis', 'sentiment_dashboard.json')

TIME_RANGES = {
    'hourly': timedelta(hours=1),
    '4h': timedelta(hours=4),
    'daily': timedelta(days=1),
    'monthly': timedelta(days=30),
    'quarterly': timedelta(days=90),
}
DEFAULT_RANGE = 'daily'

TIME_SLOTS = [
    ('morning', 6, 12),
    ('afternoon', 12, 18),
    ('evening', 18, 24),
    ('late_night', 0, 6),
]

_analyzer = SentimentIntensityAnalyzer()


def parse_ts(ts):
    if not ts:
        return None
    try:
        return datetime.fromisoformat(ts.replace('Z', '+00:00'))
    except ValueError:
        return None


def score_sentiment(text):
    compound = _analyzer.polarity_scores(text or '')['compound']
    if compound >= 0.05:
        label = 'positive'
    elif compound <= -0.05:
        label = 'negative'
    else:
        label = 'neutral'
    return label, compound


def load_dashboard_posts():
    """Like cluster_topics.load_posts, but keeps keywords/timestamp/sentiment
    separately since the dashboard widgets need them raw (not merged into a
    single TF-IDF document)."""
    posts = load_posts()
    for p in posts:
        p['ts'] = parse_ts(p['timestamp'])
        p['sentiment'], p['sentiment_score'] = score_sentiment(p['text'])
    return posts


def in_range(post, now, span):
    return post['ts'] is not None and now - span <= post['ts'] <= now


# --- Fuzzy decision layer: fuse volume + engagement into heat/focus. -------
def tri(x, a, b, c):
    if x <= a or x >= c:
        return 0.0
    if x == b:
        return 1.0
    return (x - a) / (b - a) if x < b else (c - x) / (c - b)


def fuzzy_fuse(volume_norm, engagement_norm):
    """Simple weighted-centroid fusion of two normalized [0,1] inputs into a
    0-100 heat score: both signals must fuzzily agree to reach the extremes."""
    bands = {'low': (-0.01, 0.0, 0.5), 'medium': (0.0, 0.5, 1.0), 'high': (0.5, 1.0, 1.01)}
    rank = {'low': 0, 'medium': 1, 'high': 2}
    out_score = {'low': 10, 'medium': 50, 'high': 90}

    v_mem = {k: tri(volume_norm, *b) for k, b in bands.items()}
    e_mem = {k: tri(engagement_norm, *b) for k, b in bands.items()}

    weighted_sum, weight_total = 0.0, 0.0
    for vl, vv in v_mem.items():
        for el, ev in e_mem.items():
            strength = min(vv, ev)
            if strength <= 0:
                continue
            out_rank = round((rank[vl] + rank[el]) / 2)
            label = ['low', 'medium', 'high'][out_rank]
            weighted_sum += strength * out_score[label]
            weight_total += strength
    return round(weighted_sum / weight_total, 1) if weight_total else 0.0


def normalize(values):
    if not values:
        return {}
    lo, hi = min(values.values()), max(values.values())
    if hi - lo < 1e-9:
        return {k: 0.5 for k in values}
    return {k: (v - lo) / (hi - lo) for k, v in values.items()}


# --- Widgets ----------------------------------------------------------------
def widget_sentiment_overview(posts):
    counts = Counter(p['sentiment'] for p in posts)
    total = len(posts)
    top_keywords = Counter()
    for p in posts:
        top_keywords.update(k.strip() for k in p.get('text', '').split() if len(k.strip()) > 2)
    return {
        'total_posts': total,
        'sentiment_counts': dict(counts),
        'sentiment_share': {k: round(v / total, 3) for k, v in counts.items()} if total else {},
        'top_terms': [t for t, _ in top_keywords.most_common(10)],
    }


def widget_temperature_bar(posts_by_topic, topic_labels):
    raw_volume = {cid: len(ps) for cid, ps in posts_by_topic.items()}
    raw_engagement = {cid: sum(p['interaction'] for p in ps) for cid, ps in posts_by_topic.items()}
    norm_v, norm_e = normalize(raw_volume), normalize(raw_engagement)
    bars = []
    for cid in posts_by_topic:
        heat = fuzzy_fuse(norm_v.get(cid, 0), norm_e.get(cid, 0))
        bars.append({'topic_id': cid, 'label': topic_labels[cid], 'heat': heat,
                     'volume': raw_volume[cid], 'engagement': raw_engagement[cid]})
    bars.sort(key=lambda b: b['heat'], reverse=True)
    return bars


def widget_sentiment_trend_curve(posts, now, span, buckets=14):
    bucket_span = span / buckets
    curve = []
    for i in range(buckets, 0, -1):
        b_end = now - bucket_span * (i - 1)
        b_start = b_end - bucket_span
        bucket_posts = [p for p in posts if p['ts'] and b_start <= p['ts'] < b_end]
        counts = Counter(p['sentiment'] for p in bucket_posts)
        curve.append({
            'bucket_start': b_start.isoformat(),
            'bucket_end': b_end.isoformat(),
            'positive': counts.get('positive', 0),
            'neutral': counts.get('neutral', 0),
            'negative': counts.get('negative', 0),
        })
    return curve


def widget_competitor_mentions(posts, keyword):
    """Returns (mentions_by_handle, mentions_by_platform) for posts matching keyword."""
    by_handle = Counter()
    by_platform = Counter()
    kw = keyword.lower()
    for p in posts:
        if kw in p['text'].lower():
            by_handle[p['handle']] += 1
            by_platform[p['platform']] += 1
    return dict(by_handle), dict(by_platform)


def widget_platform_share_bar(mentions_by_platform):
    total = sum(mentions_by_platform.values())
    return {plat: round(v / total, 3) for plat, v in mentions_by_platform.items()} if total else {}


def widget_platform_keyword_ranking(posts, keyword):
    """Per-platform ranking of handles for a keyword (FR-03-06)."""
    kw = keyword.lower()
    counts = defaultdict(Counter)
    for p in posts:
        if kw in p['text'].lower():
            counts[p['platform']][p['handle']] += 1
    return {
        platform: sorted(({'handle': h, 'mentions': c} for h, c in handle_counts.items()),
                          key=lambda r: r['mentions'], reverse=True)
        for platform, handle_counts in counts.items()
    }


def widget_coverage_focus_ranking(posts_by_topic, topic_labels):
    """Each account's dominant (most-posted) topic."""
    by_account_topic = defaultdict(Counter)
    for cid, ps in posts_by_topic.items():
        for p in ps:
            by_account_topic[p['handle']][cid] += 1

    ranking = []
    for handle, topic_counts in by_account_topic.items():
        top_cid, top_count = topic_counts.most_common(1)[0]
        total = sum(topic_counts.values())
        ranking.append({
            'handle': handle,
            'top_topic_id': top_cid,
            'top_topic_label': topic_labels[top_cid],
            'focus_share': round(top_count / total, 3),
            'post_count': total,
        })
    ranking.sort(key=lambda r: r['focus_share'], reverse=True)
    return ranking


def widget_top_engagement_ranking(posts_by_topic, topic_labels):
    ranking = []
    for cid, ps in posts_by_topic.items():
        total_engagement = sum(p['interaction'] for p in ps)
        ranking.append({'topic_id': cid, 'label': topic_labels[cid],
                        'total_engagement': total_engagement, 'post_count': len(ps)})
    ranking.sort(key=lambda r: r['total_engagement'], reverse=True)
    return ranking


def widget_posting_timeslot_analysis(posts):
    """Mon-Fri only, per SRS FR-03-09."""
    slots = {name: {'post_count': 0, 'likes': 0, 'engagement': 0} for name, _, _ in TIME_SLOTS}
    for p in posts:
        if not p['ts'] or p['ts'].weekday() >= 5:  # Sat=5, Sun=6
            continue
        hour = p['ts'].hour
        for name, start, end in TIME_SLOTS:
            if start <= hour < end:
                slots[name]['post_count'] += 1
                slots[name]['likes'] += p['likes']
                slots[name]['engagement'] += p['interaction']
                break
    for s in slots.values():
        s['avg_engagement'] = round(s['engagement'] / s['post_count'], 1) if s['post_count'] else 0.0
    peak_slot = max(slots, key=lambda k: slots[k]['post_count']) if any(s['post_count'] for s in slots.values()) else None
    return {'slots': slots, 'peak_slot': peak_slot}


def main(time_range=DEFAULT_RANGE, keyword=None, now=None):
    if time_range not in TIME_RANGES:
        raise ValueError(f"time_range must be one of {list(TIME_RANGES)}")
    span = TIME_RANGES[time_range]

    all_posts = load_dashboard_posts()
    if not all_posts:
        print("No posts available, skipping.")
        return

    if now is None:
        timestamps = [p['ts'] for p in all_posts if p['ts']]
        now = max(timestamps) if timestamps else datetime.now(timezone.utc)

    posts = [p for p in all_posts if in_range(p, now, span)]
    if not posts:
        print(f"No posts in the last {time_range} window, falling back to all posts for widget shape.")
        posts = all_posts

    # Shared topic tree with FR-01/FR-02.
    vectorizer, X, km, labels = cluster_posts(posts, N_CLUSTERS)
    for p, label in zip(posts, labels):
        p['cluster_id'] = int(label)
    posts_by_topic = defaultdict(list)
    for p in posts:
        posts_by_topic[p['cluster_id']].append(p)
    topic_labels = {cid: ' / '.join(label_cluster(vectorizer, km.cluster_centers_[cid])) or f'cluster-{cid}'
                    for cid in posts_by_topic}

    result = {
        'generated_at': now.isoformat(),
        'time_range': time_range,
        'keyword': keyword,
        'widgets': {
            'sentiment_overview': widget_sentiment_overview(posts),
            'temperature_bar': widget_temperature_bar(posts_by_topic, topic_labels),
            'sentiment_trend_curve': widget_sentiment_trend_curve(posts, now, span),
            'coverage_focus_ranking': widget_coverage_focus_ranking(posts_by_topic, topic_labels),
            'top_engagement_ranking': widget_top_engagement_ranking(posts_by_topic, topic_labels),
            'posting_timeslot_analysis': widget_posting_timeslot_analysis(posts),
        },
    }

    if keyword:
        mentions_by_handle, mentions_by_platform = widget_competitor_mentions(posts, keyword)
        result['widgets']['competitor_mentions'] = mentions_by_handle
        result['widgets']['platform_share_bar'] = widget_platform_share_bar(mentions_by_platform)
        result['widgets']['platform_keyword_ranking'] = widget_platform_keyword_ranking(posts, keyword)
    else:
        result['widgets']['competitor_mentions'] = None
        result['widgets']['platform_share_bar'] = None
        result['widgets']['platform_keyword_ranking'] = None
        result['note'] = 'competitor_mentions/platform_share_bar/platform_keyword_ranking require a keyword argument'

    os.makedirs(os.path.dirname(OUT_FILE), exist_ok=True)
    with open(OUT_FILE, 'w', encoding='utf-8') as f:
        json.dump(result, f, ensure_ascii=False, indent=2)

    print(f"Wrote sentiment dashboard ({time_range}, {len(posts)} posts) to {OUT_FILE}")


if __name__ == '__main__':
    import sys
    kw = sys.argv[2] if len(sys.argv) > 2 else None
    rng = sys.argv[1] if len(sys.argv) > 1 else DEFAULT_RANGE
    main(time_range=rng, keyword=kw)
