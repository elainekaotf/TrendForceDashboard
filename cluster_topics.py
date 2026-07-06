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

Output: analysis/topic_clusters.json
  - clusters: [{id, label, top_terms, size, accounts: {handle: count}}]
  - gaps: topics with strong competitor presence but weak/no own-account coverage
  - suggested_entry_points: gap topics ranked by competitor engagement

Scope note: LinkedIn competitor sources are not yet collected (see SRS Data
Requirements / Open Issue #3) and are out of scope until that crawler exists.
"""
import csv
import json
import os
import re
from collections import defaultdict
from datetime import datetime

from sklearn.feature_extraction.text import TfidfVectorizer
from sklearn.cluster import KMeans

BASE = os.path.dirname(__file__)
CSV_DIR = os.path.join(BASE, 'csv')
FACEBOOK_CSV_DIR = os.path.join(CSV_DIR, 'facebook')
OUT_FILE = os.path.join(BASE, 'analysis', 'topic_clusters.json')

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
    machine-readable scrapedAt (when the post was scraped, not posted)."""
    exact = row.get('exactDate')
    if exact:
        try:
            return datetime.strptime(exact, '%A, %B %d, %Y at %I:%M %p').isoformat() + 'Z'
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
                    'likes': parse_count(row.get('likes')),
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
                    'likes': parse_count(row.get('reactions')),
                    'interaction': parse_count(row.get('reactions')) + parse_count(row.get('comments')) + parse_count(row.get('shares')),
                })
    return posts


def load_posts():
    posts = []
    for platform, cfg in PLATFORM_ACCOUNTS.items():
        loader = load_x_posts if platform == 'X' else load_facebook_posts
        for handle in [cfg['own']] + cfg['competitors']:
            path = os.path.join(cfg['dir'], f'{handle}.csv')
            if not os.path.exists(path):
                continue
            posts.extend(loader(handle, path))
    return posts


def label_cluster(vectorizer, centroid, top_n=4):
    terms = vectorizer.get_feature_names_out()
    top_idx = centroid.argsort()[::-1][:top_n]
    return [terms[i] for i in top_idx if centroid[i] > 0]


def cluster_posts(posts, n_clusters=N_CLUSTERS, min_docs_per_cluster=5):
    """Shared clustering step so FR-02's topic hierarchy matches FR-01's tree."""
    docs = [p['text'] for p in posts]
    stop_words = list(TfidfVectorizer(stop_words='english').get_stop_words()) + list(LINK_NOISE)
    vectorizer = TfidfVectorizer(max_features=3000, stop_words=stop_words, min_df=2)
    X = vectorizer.fit_transform(docs)

    k = max(1, min(n_clusters, len(posts) // min_docs_per_cluster))
    km = KMeans(n_clusters=k, n_init=10, random_state=42)
    labels = km.fit_predict(X)
    return vectorizer, X, km, labels


def main():
    posts = load_posts()
    if len(posts) < MIN_DOCS:
        print(f"Not enough posts ({len(posts)}) for {N_CLUSTERS} clusters, skipping.")
        return

    vectorizer, X, km, labels = cluster_posts(posts, N_CLUSTERS)
    k = km.n_clusters

    clusters = []
    for cid in range(k):
        idxs = [i for i, l in enumerate(labels) if l == cid]
        if not idxs:
            continue
        by_account = defaultdict(int)
        engagement_by_account = defaultdict(int)
        for i in idxs:
            by_account[posts[i]['handle']] += 1
            engagement_by_account[posts[i]['handle']] += posts[i]['interaction']
        top_terms = label_cluster(vectorizer, km.cluster_centers_[cid])
        clusters.append({
            'id': cid,
            'label': ' / '.join(top_terms) if top_terms else f'cluster-{cid}',
            'top_terms': top_terms,
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

    result = {
        'clusters': sorted(clusters, key=lambda c: c['size'], reverse=True),
        'gaps': gaps,
        'suggested_entry_points': gaps[:5],
    }

    os.makedirs(os.path.dirname(OUT_FILE), exist_ok=True)
    with open(OUT_FILE, 'w', encoding='utf-8') as f:
        json.dump(result, f, ensure_ascii=False, indent=2)

    print(f"Wrote {len(clusters)} clusters, {len(gaps)} topic gaps to {OUT_FILE}")


if __name__ == '__main__':
    main()
