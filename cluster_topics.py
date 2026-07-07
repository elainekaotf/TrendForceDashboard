"""
FR-01 Competitor Topic Clustering.

Clusters posts from our own accounts and competitor accounts, across
platforms, into topics using TF-IDF + K-Means over each post's normalized
text (X posts are pre-translated into `translated_text` by the existing
scraper; Facebook posts are native zh-TW and pass through as-is - the
Chinese-character range in NON_WORD_RE keeps them usable for clustering
alongside the translated English X text).

Platforms covered (PLATFORM_ACCOUNTS below):
  - X:        TrendForce (own) vs. dylan522p, SemiAnalysis_, jukan05,
              QQ_Timmy, technews_tw
  - Facebook: TrendForce.tw (own) vs. ctee.fans, yutinghaosfinance

Output: analysis/topic_clusters_<range>.json for each of time_ranges.RANGE_ORDER
(4h/8h/1d/1w/1q), plus analysis/topic_clusters.json for the broadest (1q)
range, kept for scripts that just want "the" topic tree without picking a
window.
  - clusters: [{id, label, top_terms, size, accounts: {handle: count}}]
  - gaps: topics with strong competitor presence but weak/no own-account coverage
  - suggested_entry_points: gap topics ranked by competitor engagement

The K-Means tree itself is fit ONCE on all available posts (clustering needs
enough documents to be stable - a 4h window rarely has enough). Each range
just filters which posts count toward cluster sizes/gaps using that same
shared tree, so "topic labels" are identical across ranges - only the
volume/engagement numbers (and which gaps clear the threshold) change.
Windows with fewer than time_ranges.MIN_WINDOW_POSTS matching posts are
skipped rather than reported as a misleadingly precise zero.

Scope note: LinkedIn competitor sources are not yet collected (see SRS Data
Requirements / Open Issue #3) and are out of scope until that crawler exists.
"""
import csv
import json
import os
import re
from collections import defaultdict
from datetime import datetime, timezone

from sklearn.feature_extraction.text import TfidfVectorizer
from sklearn.cluster import KMeans

from time_ranges import RANGE_HOURS, RANGE_ORDER, MIN_WINDOW_POSTS, parse_ts, window_bounds, window_dict, TAIWAN_TZ

BASE = os.path.dirname(__file__)
CSV_DIR = os.path.join(BASE, 'csv')
FACEBOOK_CSV_DIR = os.path.join(CSV_DIR, 'facebook')
OUT_FILE = os.path.join(BASE, 'analysis', 'topic_clusters.json')
LEGACY_RANGE = '1q'  # analysis/topic_clusters.json mirrors this range


def range_out_file(range_key):
    return os.path.join(BASE, 'analysis', f'topic_clusters_{range_key}.json')

PLATFORM_ACCOUNTS = {
    'X': {
        'dir': CSV_DIR,
        'own': 'TrendForce',
        'competitors': ['dylan522p', 'SemiAnalysis_', 'jukan05', 'QQ_Timmy', 'technews_tw'],
    },
    'Facebook': {
        'dir': FACEBOOK_CSV_DIR,
        'own': 'TrendForce.tw',
        'competitors': ['ctee.fans', 'yutinghaosfinance'],
    },
}
OWN_HANDLES = {p['own'] for p in PLATFORM_ACCOUNTS.values()}
ACCOUNTS = [h for p in PLATFORM_ACCOUNTS.values() for h in [p['own']] + p['competitors']]

# Backward-compat aliases (X was the only platform when these were introduced).
OWN_ACCOUNT = PLATFORM_ACCOUNTS['X']['own']
COMPETITOR_ACCOUNTS = PLATFORM_ACCOUNTS['X']['competitors']

N_CLUSTERS = 18
MIN_DOCS = N_CLUSTERS * 3  # need enough posts for stable clusters

URL_RE = re.compile(r'https?://\S+')
NON_WORD_RE = re.compile(r'[^a-zA-Z一-鿿\s#]')
# Shortlink-domain fragments (dlvr.it, buff.ly, t.co, etc.) that leak into
# TF-IDF terms as meaningless noise once URLs are stripped to bare tokens.
LINK_NOISE = {'dlvr', 'buff', 'ly', 'tt', 'http', 'https', 'www', 'com'}

# Generic financial/temporal boilerplate that shows up in nearly every
# Traditional Chinese finance-news post (億元, 年增, 今年, 月增, ...) - it's
# high-frequency but carries no topic identity, so it crowds out the actual
# technical/product terms a topic label should surface.
CHINESE_STOP_WORDS = {
    '億元', '萬元', '千元', '元', '億美元', '萬美元', '美元',
    '年增', '月增', '季增', '年減', '月減', '季減', '增加', '減少', '成長',
    '今年', '去年', '明年', '本季', '上季', '下季', '本月', '上月', '下月',
    '同期', '同比', '較去年同期', '較上季', '較上月', '目前', '近期', '日前',
    '報導', '指出', '表示', '預估', '預計', '據悉', '消息指出', '最新研究指出',
    '營收', '獲利', '毛利率', '目標價', '股價', '創新高', '新高', '新低',
}


def parse_count(val):
    if not val:
        return 0
    val = str(val).strip().replace(',', '')
    try:
        if val.endswith('K'):
            return int(float(val[:-1]) * 1000)
        if val.endswith('M'):
            return int(float(val[:-1]) * 1_000_000)
        return int(float(val))
    except ValueError:
        return 0


def clean_text(text):
    text = URL_RE.sub('', text or '')
    text = NON_WORD_RE.sub(' ', text)
    return text.strip()


def parse_facebook_timestamp(row):
    """Facebook rows carry a human-readable exactDate ("Thursday, July 2,
    2026 at 1:00 PM") when the scraper resolved one, else fall back to the
    machine-readable scrapedAt (when the post was scraped, not posted).

    exactDate is in the viewer's local time - Taiwan (UTC+8), confirmed by
    comparing it against scrapedAt on real rows (e.g. exactDate "12:45 PM"
    lands ~8 minutes before a scrapedAt of 04:53 UTC, which is 12:53 in
    Taiwan - consistent; interpreting exactDate as UTC directly would put
    the post 8 hours in the future). Attach Taiwan tzinfo and convert to
    UTC rather than treating the naive parse as if it were already UTC."""
    exact = row.get('exactDate')
    if exact:
        try:
            naive = datetime.strptime(exact, '%A, %B %d, %Y at %I:%M %p')
            return naive.replace(tzinfo=TAIWAN_TZ).astimezone(timezone.utc).isoformat().replace('+00:00', 'Z')
        except ValueError:
            pass
    return row.get('scrapedAt')


def load_x_posts(handle, path):
    posts = []
    with open(path, newline='', encoding='utf-8') as f:
        for row in csv.DictReader(f):
            text = row.get('translated_text') or row.get('text') or ''
            keywords = row.get('keywords', '')
            doc = clean_text(text + ' ' + keywords.replace(';', ' '))
            if doc:
                posts.append({
                    'handle': handle,
                    'platform': 'X',
                    'text': doc,
                    'timestamp': row.get('timestamp'),
                    'url': row.get('tweetUrl') or '',
                    'likes': parse_count(row.get('likes')),
                    'replies': parse_count(row.get('replies')),
                    'interaction': parse_count(row.get('likes')) + parse_count(row.get('retweets')) + parse_count(row.get('replies')),
                })
    return posts


def load_facebook_posts(handle, path):
    posts = []
    with open(path, newline='', encoding='utf-8') as f:
        for row in csv.DictReader(f):
            doc = clean_text(row.get('text', ''))
            if doc:
                posts.append({
                    'handle': handle,
                    'platform': 'Facebook',
                    'text': doc,
                    'timestamp': parse_facebook_timestamp(row),
                    'url': row.get('postUrl') or '',
                    'likes': parse_count(row.get('reactions')),
                    'replies': parse_count(row.get('comments')),
                    'interaction': parse_count(row.get('reactions')) + parse_count(row.get('comments')) + parse_count(row.get('shares')),
                })
    return posts


def load_posts():
    """NFR-02 requires deduplication after collection. Nothing upstream
    guarantees it: sync_data.sh re-copies whole CSVs (a scraper re-run with
    an overlapping window would duplicate rows), so dedup on the way in
    rather than trusting every source file is already clean."""
    posts = []
    seen = set()
    for platform, cfg in PLATFORM_ACCOUNTS.items():
        loader = load_x_posts if platform == 'X' else load_facebook_posts
        for handle in [cfg['own']] + cfg['competitors']:
            path = os.path.join(cfg['dir'], f'{handle}.csv')
            if not os.path.exists(path):
                continue
            for post in loader(handle, path):
                key = (post['platform'], post['handle'], post['timestamp'], post['text'])
                if key in seen:
                    continue
                seen.add(key)
                posts.append(post)
    return posts


def label_cluster(vectorizer, centroid, top_n=4):
    terms = vectorizer.get_feature_names_out()
    top_idx = centroid.argsort()[::-1][:top_n]
    return [terms[i] for i in top_idx if centroid[i] > 0]


def cluster_posts(posts, n_clusters=N_CLUSTERS, min_docs_per_cluster=5):
    """Shared clustering step so FR-02's topic hierarchy matches FR-01's tree.

    On a small/sparse subset (a short time window, or FR-02's sub-topic
    drill-down), min_df=2 can prune every term if nothing repeats across
    documents, which raises instead of degrading gracefully. Retry with a
    looser min_df, then without stop-word filtering, before giving up."""
    docs = [p['text'] for p in posts]
    stop_words = list(TfidfVectorizer(stop_words='english').get_stop_words()) + list(LINK_NOISE) + list(CHINESE_STOP_WORDS)

    X = vectorizer = None
    for kwargs in ({'min_df': 2, 'stop_words': stop_words},
                   {'min_df': 1, 'stop_words': stop_words},
                   {'min_df': 1}):
        vectorizer = TfidfVectorizer(max_features=3000, **kwargs)
        try:
            X = vectorizer.fit_transform(docs)
            break
        except ValueError:
            continue
    if X is None:
        raise ValueError(f"Could not vectorize {len(docs)} document(s) even without stop-word filtering.")

    k = max(1, min(n_clusters, len(posts) // min_docs_per_cluster))
    km = KMeans(n_clusters=k, n_init=10, random_state=42)
    labels = km.fit_predict(X)
    return vectorizer, X, km, labels


def summarize_clusters(posts, labels, topic_labels):
    """Build clusters/gaps for whatever subset of (posts, labels) is passed
    in - the caller decides the time window, if any."""
    clusters = []
    for cid in sorted(set(labels)):
        idxs = [i for i, l in enumerate(labels) if l == cid]
        if not idxs:
            continue
        by_account = defaultdict(int)
        engagement_by_account = defaultdict(int)
        for i in idxs:
            by_account[posts[i]['handle']] += 1
            engagement_by_account[posts[i]['handle']] += posts[i]['interaction']
        clusters.append({
            'id': int(cid),
            'label': topic_labels[cid],
            'size': len(idxs),
            'accounts': dict(by_account),
            'engagement_by_account': dict(engagement_by_account),
        })

    # Topic gaps: clusters where competitors post a lot but our own accounts post little/none.
    gaps = []
    for c in clusters:
        own_count = sum(v for k, v in c['accounts'].items() if k in OWN_HANDLES)
        competitor_count = sum(v for k, v in c['accounts'].items() if k not in OWN_HANDLES)
        competitor_engagement = sum(v for k, v in c['engagement_by_account'].items() if k not in OWN_HANDLES)
        if competitor_count >= 3 and own_count <= max(1, competitor_count // 4):
            gaps.append({
                'cluster_id': c['id'],
                'label': c['label'],
                'own_count': own_count,
                'competitor_count': competitor_count,
                'competitor_engagement': competitor_engagement,
                'competitors_covering': [k for k in c['accounts'] if k not in OWN_HANDLES],
            })
    gaps.sort(key=lambda g: g['competitor_engagement'], reverse=True)

    return {
        'clusters': sorted(clusters, key=lambda c: c['size'], reverse=True),
        'gaps': gaps,
        'suggested_entry_points': gaps[:5],
    }


def write_json(path, result):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, 'w', encoding='utf-8') as f:
        json.dump(result, f, ensure_ascii=False, indent=2)


def main():
    posts = load_posts()
    if len(posts) < MIN_DOCS:
        print(f"Not enough posts ({len(posts)}) for {N_CLUSTERS} clusters, skipping.")
        return

    # Fit the tree once on everything - short windows rarely have enough
    # posts to cluster on their own, so every range reuses this same tree
    # and just filters which posts count.
    vectorizer, X, km, labels = cluster_posts(posts, N_CLUSTERS)
    topic_labels = {int(cid): (' / '.join(label_cluster(vectorizer, km.cluster_centers_[cid])) or f'cluster-{cid}')
                    for cid in set(labels)}

    timestamps = [parse_ts(p['timestamp']) for p in posts]
    now = max((t for t in timestamps if t), default=None)

    written = 0
    for range_key in RANGE_ORDER:
        if now is None:
            break
        start, end = window_bounds(range_key, now)
        idxs = [i for i, t in enumerate(timestamps) if t and start <= t <= end]
        if len(idxs) < MIN_WINDOW_POSTS:
            print(f"Skipping {range_key}: only {len(idxs)} posts in window (need {MIN_WINDOW_POSTS}).")
            continue

        window_posts = [posts[i] for i in idxs]
        window_labels = [labels[i] for i in idxs]
        result = summarize_clusters(window_posts, window_labels, topic_labels)
        result['window'] = window_dict(start, end)
        write_json(range_out_file(range_key), result)
        if range_key == LEGACY_RANGE:
            write_json(OUT_FILE, result)
        print(f"[{range_key}] Wrote {len(result['clusters'])} clusters, {len(result['gaps'])} topic gaps "
              f"({len(idxs)} posts) to {range_out_file(range_key)}")
        written += 1

    if written == 0:
        print("No range had enough posts to summarize.")


if __name__ == '__main__':
    main()
