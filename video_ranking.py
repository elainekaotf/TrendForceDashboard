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
import re

from cluster_topics import PLATFORM_ACCOUNTS, parse_count
from time_ranges import RANGE_HOURS, parse_ts, taiwan_str

BASE = os.path.dirname(__file__)
OUT_FILE = os.path.join(BASE, 'analysis', 'video_ranking.json')
RISING_TOPICS_FILE = os.path.join(BASE, 'analysis', 'fuzzy_trends_1d.json')

# Mirrors KEYWORD_BATCHES in TrendforceTwitterScraper/scrape_video_discovery.js -
# duplicated intentionally (the two repos can't share code) so posts that
# never got a topic from a search query in the first place - tracked-account
# posts (plain timeline scrape, not a search) and discovery rows written
# before topic-tagging existed - can still be assigned one by matching
# their own text against the same keyword sets.
INDUSTRY_KEYWORD_SETS = [
    ['TSMC', 'Nvidia', 'Samsung', 'SK hynix', 'Micron'],
    ['Intel', 'AMD', 'semiconductor', 'chip', 'foundry'],
    ['DRAM', 'NAND', 'HBM', 'EUV', 'AI chip'],
]

# Known industry acronyms that collide with ordinary words/names when
# matched case-insensitively (NAND vs the name "Nand", found 2026-07-23 -
# "Injured Inspector Nand Kishor Singh" case-insensitively matched "NAND").
# Checking the TERM's own stored case (term.isupper()) isn't enough:
# Rising Topic labels come from cluster_topics.py's TF-IDF tokenizer,
# which lowercases everything, so a genuinely-meant "NAND" arrives here as
# lowercase "nand" with no case signal left to check. This explicit
# allowlist means term_in_text() still knows to require the ALL-CAPS form
# in the actual post text regardless of how the term itself is cased.
ACRONYM_TERMS = {'NAND', 'DRAM', 'HBM', 'EUV', 'SK', 'AI', 'TSMC', 'GPU', 'CPU', 'RAM', 'IC', 'AMD'}


def load_rising_topic_keyword_sets():
    """Same file/label format scrape_video_discovery.js's getRisingTopicQueries()
    reads - reused here as fallback classification keywords, not as new
    search queries."""
    try:
        with open(RISING_TOPICS_FILE, encoding='utf-8') as f:
            data = json.load(f)
    except (OSError, ValueError):
        return []
    sets = []
    seen_labels = set()
    for platform_data in data.get('platforms', {}).values():
        for topic in platform_data.get('top_rising_topics', []):
            label = topic.get('label')
            if not label or label in seen_labels:
                continue
            seen_labels.add(label)
            terms = [t.strip() for t in label.split(' / ') if t.strip()]
            if terms:
                sets.append((label, terms))
    return sets


def term_in_text(term, text):
    """Plain substring matching false-positives on short ASCII terms found
    inside an unrelated word - found 2026-07-23: Rising Topic label
    "sk / hynix / samsung / hbm" has "sk" as its own term (cluster_topics.py's
    TF-IDF vectorizer tokenizes "SK hynix" into separate unigrams), and "sk"
    is a substring of "Haskell". Word-boundary match for pure ASCII
    alnum terms avoids that; multi-word terms and CJK terms (which carry no
    spaces to bound on) keep plain substring matching, same as before.

    Word-boundary alone still isn't enough for all-caps industry acronyms
    (NAND, DRAM, HBM, EUV, SK, AI, ...) - "Injured Inspector Nand Kishor
    Singh" case-insensitively matched "NAND" as a whole word, tagging an
    unrelated news video (found 2026-07-23). Checking the TERM's own
    stored case (term.isupper()) isn't enough either: Rising Topic labels
    are already lowercased by cluster_topics.py's TF-IDF tokenizer before
    they ever reach here, so a genuinely-meant "NAND" arrives as "nand"
    with no case signal left. ACRONYM_TERMS is the explicit fix - any term
    whose UPPERCASE form is a known acronym requires a case-SENSITIVE
    match against that uppercase form in the original text, regardless of
    how the term itself happens to be cased; ordinary words (chip,
    foundry, ...) keep case-insensitive matching since case can't
    disambiguate those anyway."""
    if re.fullmatch(r'[a-zA-Z0-9]+', term):
        upper = term.upper()
        if upper in ACRONYM_TERMS and len(term) > 1:
            return re.search(r'\b' + re.escape(upper) + r'\b', text) is not None
        return re.search(r'\b' + re.escape(term.lower()) + r'\b', text.lower()) is not None
    return term.lower() in text.lower()


def topic_matches_text(topic_label, text):
    """A stored topic came from scrape_video_discovery.js's search results,
    which can be wrong: X's search matches against the account's username
    too, not just tweet content - found 2026-07-23 (@ChipGotIt_'s account
    name contains "Chip", tagging an unrelated video "...chip / foundry"
    purely from that). The scraper itself was fixed to filter these out
    going forward, but rows written before that fix are already on disk -
    re-validate every stored topic against the post's own text here so a
    stale bad tag gets reclassified instead of trusted at face value."""
    terms = [t.strip() for t in topic_label.split(' / ') if t.strip()]
    if not terms:
        return False
    return any(term_in_text(t, text) for t in terms)


def assign_fallback_topic(text, keyword_sets):
    """Picks whichever keyword set has the most terms appearing in the
    post's own text (word-boundary match for ASCII terms, case-sensitive
    for all-caps acronym terms, substring for CJK - see term_in_text).
    Falls back to 'General' rather than leaving a post with no topic at
    all, since every post should show something in the Topics column."""
    best_label, best_score = None, 0
    for label, terms in keyword_sets:
        score = sum(1 for t in terms if term_in_text(t, text))
        if score > best_score:
            best_label, best_score = label, score
    return best_label or 'General'

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
                    # Tracked-account posts come from a plain timeline
                    # scrape, not a topic-keyword search - there's no
                    # topic to attribute them to (unlike discovery posts
                    # below, which are inherently tied to whichever query
                    # found them).
                    'topic': '',
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
                    'topic': (row.get('topic') or '').strip(),
                })
    return posts


def main():
    os.makedirs(os.path.join(BASE, 'analysis'), exist_ok=True)
    posts = load_video_posts()

    if not posts:
        print('No video posts found across any tracked X account, skipping.')
        return

    keyword_sets = [(' / '.join(terms), terms) for terms in INDUSTRY_KEYWORD_SETS]
    keyword_sets += load_rising_topic_keyword_sets()
    backfilled = 0
    corrected = 0
    for p in posts:
        if not p['topic']:
            p['topic'] = assign_fallback_topic(p['text'], keyword_sets)
            backfilled += 1
        elif not topic_matches_text(p['topic'], p['text']):
            p['topic'] = assign_fallback_topic(p['text'], keyword_sets)
            corrected += 1
    if backfilled:
        print(f"Assigned a fallback topic (content match) to {backfilled} post(s) with no search-derived topic.")
    if corrected:
        print(f"Corrected {corrected} stale/mismatched topic tag(s) that didn't actually appear in their post's text.")

    # "General" means nothing in our own keyword/topic vocabulary matched
    # the post at all - excluded rather than shown, since the ranking is
    # meant to surface videos relevant to TrendForce's actual coverage,
    # not just whatever's popular on X regardless of subject.
    before_count = len(posts)
    posts = [p for p in posts if p['topic'] != 'General']
    dropped = before_count - len(posts)
    if dropped:
        print(f"Excluded {dropped} post(s) with no matching topic (General).")

    if not posts:
        print('No video posts with a matching topic found, skipping.')
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
