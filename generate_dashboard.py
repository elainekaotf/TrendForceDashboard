"""
Renders docs/index.html from the FR-01..06 analysis JSON files - the
missing L4 Presentation layer (SRS Section 3) that turns the JSON this
pipeline produces into something a person can actually look at.

Reads whichever of these are present (skips sections gracefully if a file
is missing, e.g. before the first pipeline run):
  analysis/topic_clusters_<range>.json     FR-01 gaps, one per time range
  analysis/fuzzy_trends_<range>.json       FR-02 rising topics/KOLs, one per range
  analysis/sentiment_dashboard_<range>.json FR-03 widgets, one per range
  analysis/daily_summaries.json            FR-06 executive summaries
  analysis/account_status.json             FR-05 account status
  analysis/reply_queue.json                FR-05 reply drafts
  analysis/review_queue.json               FR-04 review queue (summarized as
                                            counts only - too large to list in full)

Topic Gaps, Rising Trends, and Sentiment all support a client-side time-range
switch (4h/8h/1d/1w/1q, see time_ranges.py): every range's HTML is
pre-rendered at build time and embedded in the page, and a dropdown just
swaps which pre-rendered block is shown - no server or re-fetch needed.

Static HTML + inline CSS/JS, no build step - open docs/index.html directly
or serve docs/ (e.g. GitHub Pages, matching TrendforceTwitterScraper's setup).

Gap vs. spec: SRS Open Issue #7 (roles & permissions) is unresolved and,
as built, unresolvable without new infrastructure - this is a public
static site with no backend, so there's no login and no admin/analyst/
reviewer distinction. Everyone with the URL sees and can do everything
a visitor can do here (including submitting FR-05 account-tracking
requests). Real roles would need an auth provider and a backend, which
is a different architecture than "static site, no server."
"""
import json
import os
import urllib.parse
from datetime import datetime, timezone, timedelta

from time_ranges import RANGE_ORDER, RANGE_LABELS, RANGE_HOURS, MIN_WINDOW_POSTS, parse_ts, window_bounds, format_window
from cluster_topics import load_posts

BASE = os.path.dirname(__file__)
ANALYSIS_DIR = os.path.join(BASE, 'analysis')
DOCS_DIR = os.path.join(BASE, 'docs')
OUT_FILE = os.path.join(DOCS_DIR, 'index.html')
TAIWAN_TZ = timezone(timedelta(hours=8))
DEFAULT_DASHBOARD_RANGE = '1d'

_FAVICON_SVG_RAW = '''<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 32 32">
<rect width="32" height="32" rx="7" fill="#0d1117"/>
<rect x="6" y="18" width="4" height="9" rx="1" fill="#3b9eff"/>
<rect x="13" y="13" width="4" height="14" rx="1" fill="#3b9eff"/>
<rect x="20" y="6" width="4" height="21" rx="1" fill="#f0b429"/>
</svg>'''
FAVICON_SVG = urllib.parse.quote(_FAVICON_SVG_RAW)


def load(name):
    path = os.path.join(ANALYSIS_DIR, name)
    if not os.path.exists(path):
        return None
    with open(path, encoding='utf-8') as f:
        return json.load(f)


def load_vader_lexicon():
    """FR-07's self-service upload runs entirely client-side (static site,
    no backend) - embed VADER's actual word->score lexicon so its sentiment
    scoring matches the rest of the dashboard (nlp_sentiment.py) instead of
    approximating with a small ad hoc word list."""
    import vaderSentiment
    path = os.path.join(os.path.dirname(vaderSentiment.__file__), 'vader_lexicon.txt')
    lexicon = {}
    with open(path, encoding='utf-8') as f:
        for line in f:
            parts = line.split('\t')
            if len(parts) >= 2:
                lexicon[parts[0]] = float(parts[1])
    return lexicon


def load_chinese_sentiment_data():
    """FR-07's self-service upload runs entirely client-side and only ever
    had VADER (English-only) - Chinese text silently scored as neutral
    regardless of actual sentiment, every time, the same bug NFR-07 already
    fixed server-side (nlp_sentiment.py). Embed the same cnsenti word lists
    plus a Traditional->Simplified single-character map (cnsenti's dictionary
    is simplified-only) so the browser can approximate the server-side path.

    This is a substring scan over the raw text, not real word segmentation -
    porting jieba's segmenter to JS is out of scope, and cnsenti's own
    sentiment_count() is just word-list membership counting (no negation/
    intensity handling) once jieba has segmented, so scanning substrings
    directly is a reasonable client-side approximation of the same idea.
    Character-level T2S (not OpenCC's full phrase-aware conversion) covers
    the large majority of Traditional/Simplified differences, which are
    per-character far more often than per-phrase.
    """
    import pickle
    import cnsenti
    cnsenti_dict_dir = os.path.join(os.path.dirname(cnsenti.__file__), 'dictionary', 'hownet')
    with open(os.path.join(cnsenti_dict_dir, 'pos.pkl'), 'rb') as f:
        pos_words = pickle.load(f)
    with open(os.path.join(cnsenti_dict_dir, 'neg.pkl'), 'rb') as f:
        neg_words = pickle.load(f)
    # Same whitespace bug fixed server-side: some dictionary entries carry a
    # trailing space, and a stray literal space would otherwise "match".
    pos_words = sorted({w.strip() for w in pos_words if w.strip()})
    neg_words = sorted({w.strip() for w in neg_words if w.strip()})

    import opencc
    opencc_dict_dir = os.path.join(os.path.dirname(opencc.__file__), 'dictionary')
    t2s_map = {}
    # Taiwan-specific character variants first, then the general
    # Traditional->Simplified table - same override order as tw2sp's own
    # conversion_chain, so e.g. 啟 resolves through TWVariantsRev before
    # falling through to TSCharacters.
    for fname in ('TWVariantsRev.txt', 'TSCharacters.txt'):
        with open(os.path.join(opencc_dict_dir, fname), encoding='utf-8') as f:
            for line in f:
                parts = line.rstrip('\n').split('\t')
                if len(parts) == 2 and parts[0] not in t2s_map:
                    t2s_map[parts[0]] = parts[1].split(' ')[0]  # first candidate only
    return pos_words, neg_words, t2s_map


def esc(s):
    if s is None:
        return ''
    return (str(s).replace('&', '&amp;').replace('<', '&lt;').replace('>', '&gt;').replace('"', '&quot;'))


def fmt_int(n):
    return f"{n:,}" if isinstance(n, (int, float)) else esc(n)


def fmt_dt(iso_str):
    """Account status timestamps came through as raw ISO strings (some
    +08:00, some +00:00, some with microseconds - whatever the source data
    happened to have) instead of one consistent, readable format. Always
    show Taiwan time, plain "YYYY-MM-DD HH:MM"."""
    if not iso_str:
        return '—'
    try:
        return datetime.fromisoformat(iso_str).astimezone(TAIWAN_TZ).strftime('%Y-%m-%d %H:%M')
    except ValueError:
        return esc(iso_str)


def account_profile_url(platform, handle):
    """Handles are stored bare (no leading @, per accounts_config.json) -
    X and Facebook both resolve a bare handle path to the account's own
    profile page directly, no lookup needed."""
    if platform == 'X':
        return f'https://x.com/{handle}'
    if platform == 'Facebook':
        return f'https://www.facebook.com/{handle}'
    return None


def panel(body_html, title=None, eyebrow=None):
    """Consistent card wrapper for a titled block of content - every major
    piece of content (a table, a stat row, a chart) sits inside one of
    these instead of floating directly on the page background."""
    head = ''
    if title:
        eyebrow_html = f'<span class="panel-eyebrow">{esc(eyebrow)}</span>' if eyebrow else ''
        head = f'<div class="panel-head"><h3>{esc(title)}</h3>{eyebrow_html}</div>'
    return f'<div class="panel">{head}{body_html}</div>'


def table(headers, rows_html, empty_message=None):
    if not rows_html:
        return f'<p class="empty">{esc(empty_message or "No data.")}</p>'
    head_cells = ''.join(f'<th class="num">{esc(h[1:])}</th>' if h.startswith('#') else f'<th>{esc(h)}</th>' for h in headers)
    return f"""<div class="table-wrap"><table>
      <thead><tr>{head_cells}</tr></thead>
      <tbody>{rows_html}</tbody>
    </table></div>"""


# --- Section builders --------------------------------------------------
def render_topic_gaps(data):
    if not data:
        return '<p class="empty">No FR-01 data yet — run cluster_topics.py.</p>'
    gaps = sorted(data.get('gaps', []), key=lambda g: g['competitor_engagement'], reverse=True)[:10]
    rows = ''.join(f"""
      <tr>
        <td class="cell-primary">{esc(g['label'])}</td>
        <td class="num">{fmt_int(g['own_count'])}</td>
        <td class="num">{fmt_int(g['competitor_count'])}</td>
        <td class="num">{fmt_int(g['competitor_engagement'])}</td>
        <td>{esc(', '.join(g['competitors_covering'][:4]))}</td>
      </tr>""" for g in gaps)
    body = table(['Topic', '#Our posts', '#Competitor posts', '#Competitor engagement', 'Covered by'],
                 rows, 'No topic gaps detected — our coverage is keeping pace with competitors.')
    return panel(body, 'Where competitors are outpacing us', 'Top 10 by competitor engagement')


def render_rising_topics(data):
    if not data:
        return '<p class="empty">No FR-02 data yet — run fuzzy_trend.py.</p>'
    sections = []
    for platform, pdata in data.get('platforms', {}).items():
        topics = pdata.get('top_rising_topics', [])
        cards = ''.join(f"""
        <div class="rising-card">
          <div class="rising-card-head">
            <span class="badge score">{t['rising_score']}</span>
            <strong>{esc(t['label'])}</strong>
          </div>
          <div class="muted">{esc(t['rationale'])}</div>
          <div class="kols">{''.join(f'<span class="chip">{esc(k["handle"])} <b>{k["rising_score"]}</b></span>' for k in t['rising_kols'][:4])}</div>
        </div>""" for t in topics)
        sections.append(panel(f'<div class="card-grid">{cards}</div>', platform, f'{len(topics)} rising topic(s)'))
    return ''.join(sections)


def render_sentiment(data):
    if not data:
        return '<p class="empty">No FR-03 data yet — run nlp_sentiment.py.</p>'
    w = data['widgets']
    overview = w['sentiment_overview']
    share = overview.get('sentiment_share', {})
    stat_cards = f"""
    <div class="stat-grid">
      <div class="stat"><div class="stat-num">{fmt_int(overview['total_posts'])}</div><div class="stat-label">Posts</div></div>
      <div class="stat pos"><div class="stat-num">{round(share.get('positive', 0) * 100, 1)}%</div><div class="stat-label">Positive</div></div>
      <div class="stat neu"><div class="stat-num">{round(share.get('neutral', 0) * 100, 1)}%</div><div class="stat-label">Neutral</div></div>
      <div class="stat neg"><div class="stat-num">{round(share.get('negative', 0) * 100, 1)}%</div><div class="stat-label">Negative</div></div>
    </div>"""

    heat_rows = ''.join(f"""
      <tr><td class="cell-primary">{esc(b['label'])}</td><td class="num heat-{('hot' if b['heat']>=70 else 'warm' if b['heat']>=40 else 'cold')}">{b['heat']}</td>
      <td class="num">{fmt_int(b['volume'])}</td><td class="num">{fmt_int(b['engagement'])}</td>
      <td>{esc(', '.join(b.get('entities', [])[:4])) or '<span class="muted">—</span>'}</td></tr>"""
      for b in w['temperature_bar'][:10])

    entity_rows = ''.join(f"""
      <tr><td class="cell-primary">{esc(e['entity'])}</td><td class="num">{fmt_int(e['count'])}</td></tr>"""
      for e in w.get('named_entities', [])[:15])

    engagement_rows = ''.join(f"""
      <tr><td class="cell-primary">{esc(r['label'])}</td><td class="num">{fmt_int(r['total_engagement'])}</td><td class="num">{r['post_count']}</td></tr>"""
      for r in w['top_engagement_ranking'][:8])

    slots = w['posting_timeslot_analysis']['slots']
    peak = w['posting_timeslot_analysis']['peak_slot']
    slot_rows = ''.join(f"""
      <tr class="{'peak' if name == peak else ''}"><td class="cell-primary">{esc(name.replace('_', ' ').title())}{' <span class="badge score">peak</span>' if name == peak else ''}</td><td class="num">{s['post_count']}</td>
      <td class="num">{fmt_int(s['likes'])}</td><td class="num">{fmt_int(s['engagement'])}</td></tr>"""
      for name, s in slots.items())

    trend_html = render_trend_curve(w['sentiment_trend_curve'])

    focus_rows = ''.join(f"""
      <tr><td class="cell-primary">{esc(r['handle'])}</td><td>{esc(r['top_topic_label'])}</td>
      <td class="num">{round(r['focus_share'] * 100, 1)}%</td><td class="num">{fmt_int(r['post_count'])}</td></tr>"""
      for r in sorted(w['coverage_focus_ranking'], key=lambda r: r['focus_share'], reverse=True)[:10])

    keyword_search_html = panel(f"""
    <div class="keyword-search-bar">
      <input type="text" id="keyword-input" placeholder="Search a keyword, e.g. nvidia, tariff, dram..." autocomplete="off">
    </div>
    <div id="keyword-results"><p class="empty">Type a keyword to see mention counts by account and platform, for the currently selected time range.</p></div>
    """, 'Keyword search', 'FR-03-04 / 05 / 06')

    return f"""
    {stat_cards}
    {panel(trend_html, 'Sentiment trend curve', 'Positive / neutral / negative over time')}
    {keyword_search_html}
    <div class="col-2">
      {panel(table(['Topic', '#Heat', '#Volume', '#Engagement', 'Top entities'], heat_rows), 'Temperature bar')}
      {panel(table(['Topic', '#Engagement', '#Posts'], engagement_rows), 'Top engagement')}
    </div>
    {panel(table(['Entity', '#Mentions'], entity_rows), 'Named entities', 'NER — most-mentioned people/orgs/products')}
    {panel(table(['Account', 'Top topic', '#Focus share', '#Posts'], focus_rows), 'Coverage focus ranking', "Each account's dominant topic")}
    {panel(table(['Time slot', '#Posts', '#Likes', '#Engagement'], slot_rows), 'Posting time-slot analysis', 'Mon–Fri, peak highlighted')}
    """


LOW_SAMPLE_THRESHOLD = 10  # below this many posts, a solid-color bar is noise, not signal
MIN_BAR_OPACITY = 0.35
TREND_ARM_PX = 70  # height of one arm (above or below the zero line) at 100% share


TREND_TRACK_PX = TREND_ARM_PX * 2


def render_trend_curve(curve):
    """Sentiment is ordered/polarized data (negative < neutral < positive), which
    calls for a diverging stacked bar centered on a zero baseline rather than a
    bottom-anchored 100%-stack: neutral sits on the baseline, positive extends up,
    negative extends down, so "is this net positive or negative" reads from the
    bar's silhouette alone instead of requiring three-way mental subtraction.

    Neutral renders as ONE absolutely-positioned block straddling the baseline
    (not two separate halves with a gap) - positive/negative are each anchored
    flush against its far edge via their own top offset, computed here rather
    than with flexbox, since three segments with independent, data-dependent
    sizes all needing to meet at one shared, cross-bar-consistent baseline
    pixel isn't expressible with stacking alone.

    Each bar is also directly labeled with its post count and date, and
    low-sample bars are faded - per the dataviz skill (diverging color =
    polarity; direct labels over hover-only; never gate a value behind a
    tooltip)."""
    if not curve:
        return '<p class="empty">Not enough data to plot a trend curve.</p>'
    baseline = TREND_ARM_PX  # px from the track's top edge
    bars = []
    for b in curve:
        total = b['positive'] + b['neutral'] + b['negative']
        pos_share = b['positive'] / total if total else 0
        neu_share = b['neutral'] / total if total else 0
        neg_share = b['negative'] / total if total else 0
        neu_px = neu_share * TREND_ARM_PX
        pos_px = pos_share * TREND_ARM_PX
        neg_px = neg_share * TREND_ARM_PX

        neu_top = baseline - neu_px / 2
        pos_top = neu_top - pos_px
        neg_top = neu_top + neu_px

        # A bar built from 1-2 posts looks visually identical to one built
        # from hundreds (both can render fully one color) - fade low-sample
        # bars so it's obvious at a glance which ones are weak signal.
        opacity = MIN_BAR_OPACITY + (1 - MIN_BAR_OPACITY) * min(total, LOW_SAMPLE_THRESHOLD) / LOW_SAMPLE_THRESHOLD
        bucket_end_tw = datetime.fromisoformat(b['bucket_end']).astimezone(TAIWAN_TZ)
        sample_note = ' (low sample size)' if total < LOW_SAMPLE_THRESHOLD else ''
        tooltip = (f"{bucket_end_tw.strftime('%b %d, %H:%M')} TW — {total} post(s){sample_note}\n"
                   f"{b['positive']} positive · {b['neutral']} neutral · {b['negative']} negative")

        bars.append(f"""
          <div class="trend-bar" data-tooltip="{esc(tooltip)}" style="opacity:{round(opacity, 2)}" tabindex="0">
            <div class="trend-count">{fmt_int(total)}</div>
            <div class="trend-track">
              <div class="seg seg-pos" style="height:{pos_px}px; top:{pos_top}px"></div>
              <div class="seg seg-neu" style="height:{neu_px}px; top:{neu_top}px"></div>
              <div class="seg seg-neg" style="height:{neg_px}px; top:{neg_top}px"></div>
              <div class="trend-baseline"></div>
            </div>
            <div class="trend-date">{bucket_end_tw.strftime('%-m/%-d')}<br>{bucket_end_tw.strftime('%H:%M')}</div>
          </div>""")

    return f"""
    <div class="trend-legend">
      <span><span class="legend-dot" style="background:var(--status-good)"></span>Positive</span>
      <span><span class="legend-dot" style="background:var(--muted-dim)"></span>Neutral</span>
      <span><span class="legend-dot" style="background:var(--status-critical)"></span>Negative</span>
      <span class="muted">Bar height = share of posts (not volume) &middot; faded = fewer than {LOW_SAMPLE_THRESHOLD} posts, weak signal</span>
    </div>
    <div class="trend-chart">{''.join(bars)}</div>"""


def render_summaries(data):
    if not data:
        return '<p class="empty">No FR-06 data yet — run generate_summaries.py.</p>'
    cards = ''.join(f"""
      <div class="summary-card">
        <div class="summary-card-head"><span class="badge cat">{esc(s['category'].replace('_', ' '))}</span><span class="char-count">{s['char_count']} chars</span></div>
        <p>{esc(s['text'])}</p>
      </div>""" for s in data.get('summaries', []))
    generated_tw = datetime.fromisoformat(data['generated_at']).astimezone(TAIWAN_TZ)
    generated_label = generated_tw.strftime('%-I:%M %p')
    return panel(f"<div class='summary-grid'>{cards}</div>", 'Today’s summaries', f"Generated {generated_label}")


def render_accounts(data):
    if not data:
        return '<p class="empty">No FR-05 data yet — run account_comment_management.py build.</p>'
    rows = ''.join(f"""
      <tr>
        <td class="cell-primary"><a href="{esc(account_profile_url(a['platform'], a['handle']))}" target="_blank" rel="noopener noreferrer">{esc(a['handle'])}</a>{' <span class="badge own">own</span>' if a['is_own'] else ''}</td>
        <td>{esc(a['platform'])}</td>
        <td><span class="badge status-{esc(a['status'])}">{esc(a['status'])}</span></td>
        <td class="num">{fmt_int(a['post_count'])}</td>
        <td>{fmt_dt(a['last_post_at'])}</td>
        <td><button class="remove-account-btn" data-platform="{esc(a['platform'])}" data-handle="{esc(a['handle'])}">Remove</button></td>
      </tr>""" for a in data.get('accounts', []))
    body = table(['Handle', 'Platform', 'Status', '#Posts', 'Last post (TWN time)', ''], rows)
    body += '<p class="muted add-account-hint">"Remove" opens a GitHub issue for review - tracking stops once it\'s approved and run locally.</p>'
    accounts_panel = panel(body, 'Tracked accounts', f"{len(data.get('accounts', []))} accounts")

    # This is a static site with no backend to add an account and start
    # scraping on the spot - the request form instead opens a pre-filled
    # GitHub issue (no credentials needed client-side, just a normal issue
    # creation link) that elainekao reviews and approves locally by running
    # add_account.py, which registers the account and kicks off a one-off
    # scrape + pipeline run for it.
    request_panel = panel(f"""
    <div class="add-account-form">
      <label>Platform
        <select id="add-account-platform">
          <option value="X">X (Twitter)</option>
          <option value="Facebook">Facebook</option>
        </select>
      </label>
      <label>Handle
        <input type="text" id="add-account-handle" placeholder="e.g. some_competitor" autocomplete="off">
      </label>
      <button class="btn" id="add-account-btn">Request tracking</button>
    </div>
    <p class="muted add-account-hint">Opens a GitHub issue for review - tracking starts once it's approved and run locally.</p>
    """, 'Request a new account to track', 'FR-05')

    return accounts_panel + request_panel


def render_reply_queue(data):
    if not data:
        return '<p class="empty">No FR-05 reply drafts yet.</p>'
    records = sorted(data.values(), key=lambda r: r['reply_count'], reverse=True)

    def comments_cell(r):
        comments = r.get('comments') or []
        if not comments:
            # scrape_own_comments.js hasn't been run for this post yet (it's
            # a separate, on-demand scrape, not part of the regular
            # schedule) - reply_count is still real, just not broken down
            # into actual comment text yet.
            return '<span class="muted">not scraped</span>'
        payload = json.dumps(comments, ensure_ascii=False)
        return (f'<span class="reply-comments-row" tabindex="0" data-comments="{esc(payload)}">'
                f'{len(comments)} comment{"s" if len(comments) != 1 else ""}</span>')

    rows = ''.join(f"""
      <tr>
        <td><span class="badge status-{esc(r['status'])}">{esc(r['status'])}</span></td>
        <td class="cell-primary">{esc(r['handle'])}</td>
        <td class="num">{r['reply_count']}</td>
        <td>{comments_cell(r)}</td>
        <td>{esc(r['topic_label'])}</td>
        <td>{esc(r['draft_reply'])}</td>
        <td>{f'<a href="{esc(r["url"])}" target="_blank" rel="noopener noreferrer">Open post</a>' if r.get('url') else '—'}</td>
      </tr>""" for r in records)
    body = table(['Status', 'Account', '#Replies', 'Comments', 'Topic', 'Draft reply', 'Post'], rows,
                 'No own-account posts currently need a response.')
    return panel(body, 'Own-account posts needing a reply', 'Never touches competitor accounts')


def render_review_queue(data):
    if not data:
        return '<p class="empty">No FR-04 review queue yet.</p>'
    records = list(data.values())
    by_status, by_type = {}, {}
    for r in records:
        by_status[r['status']] = by_status.get(r['status'], 0) + 1
        by_type[r['type']] = by_type.get(r['type'], 0) + 1

    status_cards = ''.join(f'<div class="stat"><div class="stat-num">{fmt_int(c)}</div><div class="stat-label">{esc(s)}</div></div>'
                            for s, c in sorted(by_status.items()))
    type_chips = ''.join(f'<span class="chip">{esc(t)} <b>{fmt_int(c)}</b></span>' for t, c in sorted(by_type.items()))

    pending = [r for r in records if r['status'] == 'pending'][:10]
    rows = ''.join(f"""
      <tr><td>{esc(r['type'])}</td><td>{esc(r.get('platform', ''))}</td><td class="cell-primary">{esc(r.get('handle', ''))}</td>
      <td>{esc((r['automated'].get('topic_label') or r['automated'].get('rationale') or r['automated'].get('text', ''))[:80])}</td></tr>"""
      for r in pending)

    overview = panel(f'<div class="stat-grid">{status_cards}</div><div class="chip-row">{type_chips}</div>',
                      'Queue overview', f"{fmt_int(len(records))} total records")
    sample = panel(table(['Type', 'Platform', 'Handle', 'Automated label'], rows), 'Sample of pending items')
    return overview + sample


def main():
    os.makedirs(DOCS_DIR, exist_ok=True)

    gaps_html_by_range = {}
    rising_html_by_range = {}
    sentiment_html_by_range = {}
    window_caption_by_range = {}
    window_bounds_by_range = {}
    window_by_range = {}
    available_ranges = []
    for range_key in RANGE_ORDER:
        topic_clusters = load(f'topic_clusters_{range_key}.json')
        fuzzy_trends = load(f'fuzzy_trends_{range_key}.json')
        sentiment_dashboard = load(f'sentiment_dashboard_{range_key}.json')
        if topic_clusters or fuzzy_trends or sentiment_dashboard:
            available_ranges.append(range_key)
        gaps_html_by_range[range_key] = render_topic_gaps(topic_clusters)
        rising_html_by_range[range_key] = render_rising_topics(fuzzy_trends)
        sentiment_html_by_range[range_key] = render_sentiment(sentiment_dashboard)

        # All three scripts anchor "now" to the latest *scraped post*, not
        # wall-clock time, so the window is spelled out explicitly here -
        # "last 4 hours" without a stated end time reads as "as of right
        # now," which it usually isn't.
        window = next((d.get('window') for d in (topic_clusters, fuzzy_trends, sentiment_dashboard)
                       if d and d.get('window')), None)
        window_by_range[range_key] = window
        window_bounds_by_range[range_key] = (
            {'start': window['start_utc'], 'end': window['end_utc']} if window else None
        )

    # A range's window can lag behind the others: each of the three source
    # scripts skips writing its file for a range when that window doesn't
    # clear MIN_WINDOW_POSTS, leaving the last successful (older) result on
    # disk rather than an empty/misleading one. That's the right call for
    # the *data* (a stale-but-real result beats no result), but left silent
    # it reads as a time-math bug when a shorter range's caption shows an
    # earlier end time than a longer range's. Flag it explicitly instead,
    # with the actual post count so it's clear why.
    parsed_ends = [datetime.fromisoformat(w['end_utc']) for w in window_by_range.values() if w]
    freshest_end = max(parsed_ends) if parsed_ends else None
    posts_for_staleness_check = None

    for range_key in RANGE_ORDER:
        window = window_by_range[range_key]
        if not window:
            window_caption_by_range[range_key] = 'No window data available for this range.'
            continue
        caption = f"Data window: {esc(window['start_tw'])} – {esc(window['end_tw'])} (Taiwan time)"
        window_end = datetime.fromisoformat(window['end_utc'])
        if freshest_end and (freshest_end - window_end).total_seconds() > 60:
            if posts_for_staleness_check is None:
                posts_for_staleness_check = load_posts()
                for p in posts_for_staleness_check:
                    p['_ts'] = parse_ts(p['timestamp'])
            current_start, current_end = window_bounds(range_key, freshest_end)
            current_count = sum(1 for p in posts_for_staleness_check
                                 if p['_ts'] and current_start <= p['_ts'] <= current_end)
            caption += (
                f" — showing the last window with enough data; the most recent "
                f"{format_window(RANGE_HOURS[range_key])} only has {current_count} "
                f"post{'s' if current_count != 1 else ''} (needs {MIN_WINDOW_POSTS})."
            )
        window_caption_by_range[range_key] = caption

    keyword_index = load('keyword_index.json') or []

    default_range = DEFAULT_DASHBOARD_RANGE if DEFAULT_DASHBOARD_RANGE in available_ranges else (
        available_ranges[0] if available_ranges else RANGE_ORDER[0])

    daily_summaries = load('daily_summaries.json')
    account_status = load('account_status.json')
    reply_queue = load('reply_queue.json')
    review_queue = load('review_queue.json')

    now_tw = datetime.now(TAIWAN_TZ).strftime('%B %d, %Y %H:%M Taiwan Time')

    range_options = ''.join(
        f'<option value="{r}"{" selected" if r == default_range else ""}>{esc(RANGE_LABELS[r])}</option>'
        for r in RANGE_ORDER)
    range_data_json = json.dumps({
        'gaps': gaps_html_by_range,
        'rising': rising_html_by_range,
        'sentiment': sentiment_html_by_range,
    }, ensure_ascii=False)
    window_caption_json = json.dumps(window_caption_by_range, ensure_ascii=False)
    window_bounds_json = json.dumps(window_bounds_by_range, ensure_ascii=False)
    keyword_index_json = json.dumps(keyword_index, ensure_ascii=False)
    vader_lexicon_json = json.dumps(load_vader_lexicon())
    zh_pos_words, zh_neg_words, zh_t2s_map = load_chinese_sentiment_data()
    zh_pos_words_json = json.dumps(zh_pos_words, ensure_ascii=False)
    zh_neg_words_json = json.dumps(zh_neg_words, ensure_ascii=False)
    zh_t2s_map_json = json.dumps(zh_t2s_map, ensure_ascii=False)

    html = f"""<!doctype html>
<html lang="en"><head>
<meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>TrendForceDash</title>
<link rel="icon" href="data:image/svg+xml,{FAVICON_SVG}">
<style>
  :root {{
    --bg: #0a0e14; --bg-grad: radial-gradient(ellipse 1200px 600px at 50% -10%, rgba(59,158,255,0.08), transparent);
    --surface: #131a24; --surface-2: #1a2331; --border: #262f3d; --border-soft: #1d2530;
    --text: #eef2f7; --muted: #8593a6; --muted-dim: #5c6b80;
    --blue: #4da3ff; --blue-dim: rgba(77,163,255,0.12);
    --gold: #f0b429; --green: #3fb968; --red: #f85149; --yellow: #d29922;
    --status-good: #0ca30c; --status-critical: #d03b3b;
    --radius: 10px; --radius-sm: 7px;
    --shadow: 0 1px 2px rgba(0,0,0,0.4), 0 8px 24px -8px rgba(0,0,0,0.5);
  }}
  * {{ box-sizing: border-box; }}
  a {{ color: var(--blue); }}
  a:visited {{ color: var(--blue); }}
  body {{
    background: var(--bg-grad), var(--bg); background-attachment: fixed;
    color: var(--text); font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
    margin: 0; -webkit-font-smoothing: antialiased;
  }}
  header {{ padding: 28px 32px 22px; }}
  header h1 {{ margin: 0; font-size: 24px; font-weight: 700; letter-spacing: -0.01em; }}
  header .muted {{ margin-top: 5px; }}
  .muted {{ color: var(--muted); font-size: 13px; }}
  nav {{
    display: flex; gap: 2px; padding: 0 28px; border-bottom: 1px solid var(--border);
    overflow-x: auto; position: sticky; top: 0; background: rgba(10,14,20,0.92);
    backdrop-filter: blur(10px); z-index: 10;
  }}
  nav button {{
    background: none; border: none; color: var(--muted); padding: 13px 16px; font-size: 13.5px;
    font-weight: 500; cursor: pointer; border-bottom: 2px solid transparent; white-space: nowrap;
    transition: color 0.15s ease;
  }}
  nav button:hover {{ color: var(--text); }}
  nav button.active {{ color: var(--text); border-bottom-color: var(--blue); font-weight: 600; }}
  main {{ padding: 28px 32px 64px; max-width: 1180px; margin: 0 auto; }}
  .range-bar {{
    display: flex; align-items: center; gap: 12px; margin-bottom: 24px; flex-wrap: wrap;
    background: var(--surface); border: 1px solid var(--border); border-radius: var(--radius-sm);
    padding: 10px 14px;
  }}
  .range-bar label {{ color: var(--muted); font-size: 12.5px; font-weight: 600; text-transform: uppercase; letter-spacing: 0.03em; }}
  .range-bar select {{
    background: var(--surface-2); color: var(--text); border: 1px solid var(--border);
    border-radius: 6px; padding: 6px 10px; font-size: 13px; cursor: pointer;
  }}
  .keyword-search-bar input {{
    width: 100%; max-width: 460px; background: var(--surface-2); color: var(--text);
    border: 1px solid var(--border); border-radius: 8px; padding: 10px 14px; font-size: 13.5px;
    transition: border-color 0.15s ease;
  }}
  .keyword-search-bar input::placeholder {{ color: var(--muted-dim); }}
  .keyword-search-bar input:focus {{ outline: none; border-color: var(--blue); box-shadow: 0 0 0 3px var(--blue-dim); }}
  .upload-row {{ display: flex; align-items: center; gap: 10px; flex-wrap: wrap; margin-bottom: 16px; }}
  .upload-row label {{ color: var(--muted); font-size: 12.5px; }}
  .upload-row input[type="file"] {{
    color: var(--text); font-size: 13px; background: var(--surface-2); border: 1px solid var(--border);
    border-radius: 7px; padding: 8px 10px;
  }}
  .upload-row select {{
    background: var(--surface-2); color: var(--text); border: 1px solid var(--border);
    border-radius: 6px; padding: 6px 10px; font-size: 13px;
  }}
  .download-row {{ display: flex; gap: 10px; margin-top: 14px; }}
  .upload-posts-toolbar {{ display: flex; flex-wrap: wrap; justify-content: space-between; align-items: center; gap: 14px; margin: 16px 0 10px; }}
  .upload-posts-toolbar label {{
    display: flex; align-items: center; gap: 8px; font-size: 11.5px; color: var(--muted);
    text-transform: uppercase; letter-spacing: 0.04em;
  }}
  .upload-posts-toolbar select, .upload-posts-toolbar input[type="text"] {{
    background: var(--surface-2); border: 1px solid var(--border); border-radius: 8px;
    color: var(--text); padding: 7px 10px; font-size: 13px;
  }}
  .upload-posts-toolbar input[type="text"]:focus, .upload-posts-toolbar select:focus {{
    outline: none; border-color: var(--blue); box-shadow: 0 0 0 3px var(--blue-dim);
  }}
  .upload-search-label {{ flex: 1; min-width: 220px; }}
  .upload-search-label input {{ flex: 1; min-width: 180px; }}
  .pager {{ display: flex; flex-wrap: wrap; gap: 6px; margin-top: 12px; }}
  .page-btn {{
    background: var(--surface-2); border: 1px solid var(--border); border-radius: 6px;
    color: var(--text); font-size: 12.5px; padding: 5px 11px; cursor: pointer;
    font-variant-numeric: tabular-nums; transition: background 0.1s ease, border-color 0.1s ease;
  }}
  .page-btn:hover {{ border-color: var(--blue); }}
  .page-btn.active {{ background: var(--blue); border-color: var(--blue); color: var(--surface); font-weight: 600; }}
  .btn {{
    background: var(--blue-dim); color: var(--blue); border: 1px solid transparent; border-radius: 7px;
    padding: 8px 14px; font-size: 13px; font-weight: 600; cursor: pointer;
  }}
  .btn:hover {{ filter: brightness(1.15); }}
  section {{ display: none; }}
  section.active {{ display: block; animation: fadein 0.2s ease; }}
  @keyframes fadein {{ from {{ opacity: 0; transform: translateY(2px); }} to {{ opacity: 1; transform: translateY(0); }} }}
  h2 {{ font-size: 20px; font-weight: 700; margin: 0 0 22px; text-align: center; letter-spacing: -0.01em; }}
  h3 {{ font-size: 13.5px; font-weight: 600; color: var(--text); margin: 22px 0 12px; }}
  h3:first-child {{ margin-top: 0; }}
  .panel {{
    background: var(--surface); border: 1px solid var(--border); border-radius: var(--radius);
    padding: 18px 20px; margin-bottom: 18px; box-shadow: var(--shadow);
  }}
  .panel-head {{ display: flex; align-items: baseline; justify-content: space-between; gap: 12px; margin-bottom: 14px; flex-wrap: wrap; }}
  .panel-head h3 {{ margin: 0; }}
  .panel-eyebrow {{ color: var(--muted); font-size: 12px; }}
  .table-wrap {{ overflow-x: auto; margin: -4px -4px -2px; }}
  table {{ width: 100%; min-width: 480px; border-collapse: collapse; font-size: 13.5px; }}
  th, td {{ text-align: left; padding: 9px 10px; }}
  th {{
    color: var(--muted); font-weight: 600; font-size: 11.5px; text-transform: uppercase;
    letter-spacing: 0.04em; border-bottom: 1px solid var(--border); padding-bottom: 10px;
  }}
  td {{ border-bottom: 1px solid var(--border-soft); }}
  tbody tr:last-child td {{ border-bottom: none; }}
  tbody tr {{ transition: background 0.1s ease; }}
  tbody tr:hover {{ background: var(--surface-2); }}
  td.num, th.num {{ text-align: center; font-variant-numeric: tabular-nums; }}
  td.cell-primary {{ font-weight: 600; }}
  tr.peak {{ background: var(--blue-dim); }}
  tr.peak:hover {{ background: var(--blue-dim); }}
  tr.kw-link-row {{ cursor: pointer; }}
  tr.kw-link-row:hover, tr.kw-link-row:focus {{ background: var(--blue-dim); outline: none; }}
  .kw-link-popover {{
    position: absolute; z-index: 30; background: var(--surface-2); border: 1px solid var(--border);
    border-radius: 8px; padding: 10px 14px; box-shadow: var(--shadow); max-width: 420px;
    max-height: 260px; overflow-y: auto; display: flex; flex-direction: column; gap: 6px;
  }}
  .kw-link-popover a {{
    color: var(--blue); font-size: 12px; line-height: 1.5; text-decoration: none;
    word-break: break-all; white-space: normal;
  }}
  .kw-link-popover a:hover {{ text-decoration: underline; }}
  .kw-link-popover .empty {{ font-size: 12px; color: var(--muted); margin: 0; }}
  .reply-comments-row {{ cursor: pointer; color: var(--blue); border-bottom: 1px dotted var(--blue); }}
  .reply-comment-item {{
    display: flex; flex-direction: column; gap: 2px; padding-bottom: 6px;
    border-bottom: 1px solid var(--border-soft); font-size: 12px; line-height: 1.5;
  }}
  .reply-comment-item:last-child {{ border-bottom: none; padding-bottom: 0; }}
  .reply-comment-item strong {{ color: var(--text); font-size: 11.5px; }}
  .reply-comment-item span {{ color: var(--muted); }}
  .add-account-form {{ display: flex; flex-wrap: wrap; align-items: end; gap: 14px; }}
  .add-account-form label {{
    display: flex; flex-direction: column; gap: 6px; font-size: 11.5px;
    color: var(--muted); text-transform: uppercase; letter-spacing: 0.04em;
  }}
  .add-account-form select, .add-account-form input {{
    background: var(--surface-2); border: 1px solid var(--border); border-radius: 8px;
    color: var(--text); padding: 9px 12px; font-size: 13px; min-width: 220px;
  }}
  .add-account-form input:focus, .add-account-form select:focus {{
    outline: none; border-color: var(--blue); box-shadow: 0 0 0 3px var(--blue-dim);
  }}
  .add-account-hint {{ margin-top: 10px; font-size: 12px; }}
  .remove-account-btn {{
    background: transparent; border: 1px solid var(--border); border-radius: 6px;
    color: var(--red); font-size: 11.5px; padding: 5px 10px; cursor: pointer;
    transition: background 0.1s ease, border-color 0.1s ease;
  }}
  .remove-account-btn:hover {{ background: rgba(248,81,73,0.16); border-color: var(--red); }}
  .remove-account-btn.confirming {{
    background: var(--red); border-color: var(--red); color: var(--surface); font-weight: 600;
  }}
  .remove-account-btn.confirming:hover {{ background: var(--red); }}
  .empty {{ color: var(--muted); font-style: italic; font-size: 13.5px; padding: 8px 2px; }}
  .col-2 {{ display: grid; grid-template-columns: 1fr 1fr; gap: 18px; align-items: start; }}
  .col-2 > .panel {{ margin-bottom: 0; }}
  .card-grid {{ display: grid; grid-template-columns: repeat(auto-fill, minmax(250px, 1fr)); gap: 10px; }}
  .rising-card {{
    background: var(--surface-2); border: 1px solid var(--border-soft); border-radius: var(--radius-sm);
    padding: 12px 14px; transition: border-color 0.15s ease;
  }}
  .rising-card:hover {{ border-color: var(--border); }}
  .rising-card-head {{ display: flex; align-items: center; gap: 8px; margin-bottom: 6px; }}
  .rising-card-head strong {{ font-size: 13.5px; line-height: 1.35; }}
  .kols {{ margin-top: 10px; display: flex; flex-wrap: wrap; gap: 5px; }}
  .chip {{
    background: var(--surface); border: 1px solid var(--border); border-radius: 999px;
    padding: 3px 9px; font-size: 11px; color: var(--muted);
  }}
  .chip b {{ color: var(--text); font-weight: 600; }}
  .chip-row {{ display: flex; flex-wrap: wrap; gap: 6px; margin-top: 14px; }}
  .badge {{ display: inline-block; padding: 3px 9px; border-radius: 999px; font-size: 10.5px; font-weight: 700; letter-spacing: 0.02em; }}
  .badge.score {{ background: rgba(240,180,41,0.16); color: var(--gold); }}
  .badge.cat {{ background: var(--blue-dim); color: var(--blue); text-transform: capitalize; }}
  .badge.own {{ background: rgba(63,185,104,0.16); color: var(--green); }}
  .badge.status-active, .badge.status-sent, .badge.status-approved {{ background: rgba(63,185,104,0.16); color: var(--green); }}
  .badge.status-stale, .badge.status-drafted, .badge.status-pending {{ background: rgba(210,153,34,0.18); color: var(--yellow); }}
  .badge.status-inactive, .badge.status-dismissed {{ background: rgba(248,81,73,0.16); color: var(--red); }}
  .heat-hot {{ color: var(--red); font-weight: 700; }}
  .heat-warm {{ color: var(--yellow); font-weight: 600; }}
  .heat-cold {{ color: var(--muted); }}
  .stat-grid {{ display: grid; grid-template-columns: repeat(auto-fit, minmax(110px, 1fr)); gap: 10px; }}
  .stat {{
    background: var(--surface-2); border: 1px solid var(--border-soft); border-radius: var(--radius-sm);
    padding: 14px 12px; text-align: center;
  }}
  .stat-num {{ font-size: 24px; font-weight: 700; font-variant-numeric: tabular-nums; letter-spacing: -0.02em; }}
  .stat-label {{ color: var(--muted); font-size: 11.5px; text-transform: uppercase; letter-spacing: 0.03em; margin-top: 3px; }}
  .stat.pos .stat-num {{ color: var(--green); }} .stat.neg .stat-num {{ color: var(--red); }} .stat.neu .stat-num {{ color: var(--muted); }}
  .summary-grid {{ display: grid; grid-template-columns: repeat(auto-fill, minmax(270px, 1fr)); gap: 12px; }}
  .summary-card {{
    background: var(--surface-2); border: 1px solid var(--border-soft); border-radius: var(--radius-sm); padding: 14px 16px;
  }}
  .summary-card-head {{ display: flex; align-items: center; justify-content: space-between; }}
  .summary-card p {{ margin: 10px 0 0; font-size: 13.5px; line-height: 1.55; }}
  .char-count {{ color: var(--muted-dim); font-size: 11px; }}
  .trend-legend {{ display: flex; flex-wrap: wrap; gap: 16px; font-size: 11.5px; color: var(--muted); margin-bottom: 14px; align-items: center; }}
  .legend-dot {{ display: inline-block; width: 8px; height: 8px; border-radius: 2px; margin-right: 5px; vertical-align: middle; }}
  .trend-chart {{
    display: flex; align-items: stretch; gap: 6px; min-width: 640px;
    padding-top: 92px; /* clears both the count label and the hover tooltip above each bar,
                           with room to spare so the tooltip never touches the panel above */
  }}
  .trend-bar {{
    position: relative; flex: 1; display: flex; flex-direction: column; align-items: center;
    cursor: default; min-width: 32px; border-radius: 4px; transition: background 0.1s ease;
  }}
  .trend-bar:hover, .trend-bar:focus {{ background: var(--surface-2); outline: none; }}
  .trend-count {{ font-size: 10.5px; color: var(--muted); font-variant-numeric: tabular-nums; margin-bottom: 3px; }}
  .trend-track {{ position: relative; width: 100%; height: {TREND_TRACK_PX}px; }}
  .seg {{ position: absolute; left: 0; right: 0; }}
  .seg-pos {{ background: var(--status-good); border-radius: 3px 3px 0 0; }}
  .seg-neg {{ background: var(--status-critical); border-radius: 0 0 3px 3px; }}
  .seg-neu {{ background: var(--muted-dim); }}
  .trend-baseline {{ position: absolute; left: 0; right: 0; top: {TREND_ARM_PX}px; height: 1px; background: var(--border); }}
  .trend-date {{
    margin-top: 6px; font-size: 10px; line-height: 1.3; color: var(--muted);
    text-align: center; white-space: nowrap;
  }}
  /* Visible on-hover/focus tooltip - replaces the native title attribute,
     which is slow to appear and easy to miss. */
  .trend-bar[data-tooltip]:hover::after, .trend-bar[data-tooltip]:focus::after {{
    content: attr(data-tooltip); position: absolute; bottom: 100%; left: 50%;
    transform: translateX(-50%); margin-bottom: 12px; padding: 10px 14px;
    background: var(--surface-2); border: 1px solid var(--border); border-radius: 8px;
    font-size: 12px; line-height: 1.7; color: var(--text); white-space: pre-line;
    width: max-content; max-width: 220px;
    text-align: left; box-shadow: var(--shadow); z-index: 20; pointer-events: none;
  }}
  @media (max-width: 800px) {{
    header, nav, main {{ padding-left: 18px; padding-right: 18px; }}
    .col-2 {{ grid-template-columns: 1fr; }}
    .panel {{ padding: 14px 16px; }}
  }}
  @media print {{
    nav, .range-bar, .upload-row, .download-row {{ display: none !important; }}
    body {{ background: #fff; color: #111; }}
    .panel {{ background: #fff; border: 1px solid #ccc; box-shadow: none; break-inside: avoid; }}
    th, .badge, .stat-label {{ color: #444 !important; }}
    .badge {{ background: #eee !important; }}
  }}
</style>
<!-- SheetJS Community Edition (Apache-2.0), vendored locally - see docs/vendor/README.md.
     Powers FR-07's Excel upload/export entirely client-side (no backend on this static site). -->
<script src="vendor/xlsx.full.min.js"></script>
</head>
<body>
<header>
  <h1>TrendForceDash</h1>
  <div class="muted">Generated {esc(now_tw)} &middot; FR-01 through FR-06</div>
</header>
<nav>
  <button class="tab-btn active" data-tab="gaps">Topic Gaps</button>
  <button class="tab-btn" data-tab="rising">Rising Trends</button>
  <button class="tab-btn" data-tab="sentiment">Sentiment</button>
  <button class="tab-btn" data-tab="competitor">Competitor Watch</button>
  <button class="tab-btn" data-tab="review">Review Queue</button>
  <button class="tab-btn" data-tab="accounts">Accounts</button>
  <button class="tab-btn" data-tab="replies">Reply Queue</button>
  <button class="tab-btn" data-tab="summaries">Daily Summaries</button>
  <button class="tab-btn" data-tab="selfservice">Self-service Upload</button>
</nav>
<main>
  <div id="range-bar" class="range-bar">
    <label for="range-select">Time range</label>
    <select id="range-select">{range_options}</select>
    <span id="range-window" class="muted"></span>
  </div>
  <section id="gaps" class="active" data-ranged="true"><h2>FR-01 &middot; Topic Gaps</h2><div id="gaps-content"></div></section>
  <section id="rising" data-ranged="true"><h2>FR-02 &middot; Rising Topics &amp; KOLs</h2><div id="rising-content"></div></section>
  <section id="sentiment" data-ranged="true"><h2>FR-03 &middot; Sentiment Dashboard</h2><div id="sentiment-content"></div></section>
  <section id="competitor" data-ranged="true"><h2>Competitor Watch</h2>{panel(f'''
    <div class="keyword-search-bar">
      <input type="text" id="competitor-keyword-input" placeholder="Search a topic or keyword, e.g. nvidia, tariff, dram..." autocomplete="off">
    </div>
    <div id="competitor-results"><p class="empty">Type a topic or keyword to see every non-TrendForce account's post mentioning it, for the currently selected time range.</p></div>
    ''', 'Search competitor posts', 'Every account that is not ours')}</section>
  <section id="review"><h2>FR-04 &middot; Manual Review Queue</h2>{render_review_queue(review_queue)}</section>
  <section id="accounts"><h2>FR-05 &middot; Account Status</h2>{render_accounts(account_status)}</section>
  <section id="replies"><h2>FR-05 &middot; Reply Queue</h2>{render_reply_queue(reply_queue)}</section>
  <section id="summaries"><h2>FR-06 &middot; Daily Executive Summaries</h2>{render_summaries(daily_summaries)}</section>
  <section id="selfservice">
    <h2>FR-07 &middot; Self-service Data Analysis &amp; Export</h2>
    {panel(f'''
      <p class="muted" style="margin:0 0 14px">
        Upload your own CSV or Excel (.xlsx) export, analyzed entirely in your browser (this is a static
        site - nothing is uploaded anywhere). Timestamps without a timezone are assumed to be in the source
        timezone below and converted to Asia/Taipei (UTC+8), matching self_service_analysis.py's CLI behavior.
      </p>
      <div class="upload-row">
        <input type="file" id="upload-file" accept=".csv,.xlsx">
        <label for="upload-tz">Source timezone (for naive timestamps)</label>
        <select id="upload-tz">
          <option value="America/Los_Angeles" selected>America/Los_Angeles (PT)</option>
          <option value="America/New_York">America/New_York (ET)</option>
          <option value="America/Chicago">America/Chicago (CT)</option>
          <option value="UTC">UTC</option>
          <option value="Asia/Taipei">Asia/Taipei (UTC+8)</option>
        </select>
      </div>
      <div id="upload-results"><p class="empty">Choose a CSV or Excel file to analyze. Expected columns: a
        text column (text/content/message) and a timestamp column (timestamp/date/created_at) - extra
        columns are kept and passed through untouched.</p></div>
    ''', 'Upload a file', 'CSV or Excel, analyzed client-side, nothing leaves your browser')}
  </section>
</main>
<script>
  const RANGE_HTML = {range_data_json};
  const RANGE_WINDOW = {window_caption_json};
  const RANGE_BOUNDS = {window_bounds_json};
  const KEYWORD_POSTS = {keyword_index_json};

  function escapeHtml(s) {{ const d = document.createElement('div'); d.textContent = s == null ? '' : String(s); return d.innerHTML; }}
  function escapeAttr(s) {{ return escapeHtml(s).replace(/"/g, '&quot;'); }}

  document.querySelectorAll('.tab-btn').forEach(btn => {{
    btn.addEventListener('click', () => {{
      document.querySelectorAll('.tab-btn').forEach(b => b.classList.remove('active'));
      document.querySelectorAll('main section').forEach(s => s.classList.remove('active'));
      btn.classList.add('active');
      const target = document.getElementById(btn.dataset.tab);
      target.classList.add('active');
      document.getElementById('range-bar').style.display =
        target.dataset.ranged === 'true' ? 'flex' : 'none';
    }});
  }});

  // FR-03-04/05/06: no backend to query on demand (static site), so
  // mention counts / platform share / platform ranking are computed live
  // in the browser over the embedded KEYWORD_POSTS index, filtered to
  // whichever range window is currently selected.
  let currentKeyword = '';

  function renderKeywordResults(range) {{
    const container = document.getElementById('keyword-results');
    if (!container) return; // sentiment tab's DOM not present right now
    const kw = currentKeyword.trim().toLowerCase();
    if (!kw) {{
      container.innerHTML = '<p class="empty">Type a keyword to see mention counts by account and platform, for the currently selected time range.</p>';
      return;
    }}
    const bounds = RANGE_BOUNDS[range];
    if (!bounds) {{
      container.innerHTML = '<p class="empty">No data window available for this range.</p>';
      return;
    }}
    const start = new Date(bounds.start), end = new Date(bounds.end);
    const matches = KEYWORD_POSTS.filter(p => {{
      const t = new Date(p.ts);
      return t >= start && t <= end && p.text.toLowerCase().includes(kw);
    }});

    if (matches.length === 0) {{
      container.innerHTML = `<p class="empty">No mentions of "${{kw}}" in this time range.</p>`;
      return;
    }}

    const byHandle = {{}}, byPlatform = {{}}, byPlatformHandle = {{}};
    const urlsByHandle = {{}}, urlsByPlatformHandle = {{}};
    for (const p of matches) {{
      byHandle[p.handle] = (byHandle[p.handle] || 0) + 1;
      byPlatform[p.platform] = (byPlatform[p.platform] || 0) + 1;
      byPlatformHandle[p.platform] = byPlatformHandle[p.platform] || {{}};
      byPlatformHandle[p.platform][p.handle] = (byPlatformHandle[p.platform][p.handle] || 0) + 1;
      if (p.url) {{
        (urlsByHandle[p.handle] = urlsByHandle[p.handle] || []).push(p.url);
        urlsByPlatformHandle[p.platform] = urlsByPlatformHandle[p.platform] || {{}};
        (urlsByPlatformHandle[p.platform][p.handle] = urlsByPlatformHandle[p.platform][p.handle] || []).push(p.url);
      }}
    }}

    // Source-link hover box (FR-03-04/06): each account row's mention count
    // is a hit target - hovering/focusing it shows every matching post's
    // URL so the reader can jump straight to the source instead of just
    // seeing a number. Encoded as a data attribute (not inline onclick) so
    // the URLs go through textContent/href, never innerHTML string-built.
    const linkRow = (h, c, urls) =>
      `<tr class="kw-link-row" tabindex="0" data-urls="${{escapeAttr(JSON.stringify(urls || []))}}"><td>${{escapeHtml(h)}}</td><td class="num">${{c}}</td></tr>`;

    const mentionRows = Object.entries(byHandle).sort((a, b) => b[1] - a[1])
      .map(([h, c]) => linkRow(h, c, urlsByHandle[h])).join('');

    const total = matches.length;
    const shareRows = Object.entries(byPlatform).sort((a, b) => b[1] - a[1])
      .map(([plat, c]) => `<tr><td>${{plat}}</td><td class="num">${{Math.round(c / total * 1000) / 10}}%</td><td class="num">${{c}}</td></tr>`).join('');

    const rankingBlocks = Object.entries(byPlatformHandle).map(([plat, handles]) => {{
      const urls = urlsByPlatformHandle[plat] || {{}};
      const rows = Object.entries(handles).sort((a, b) => b[1] - a[1])
        .map(([h, c]) => linkRow(h, c, urls[h])).join('');
      return `<div><h3>${{escapeHtml(plat)}}</h3><div class="table-wrap"><table><thead><tr><th>Account</th><th class="num">Mentions</th></tr></thead><tbody>${{rows}}</tbody></table></div></div>`;
    }}).join('');

    container.innerHTML = `
      <p class="muted">${{total}} post(s) mention "${{kw}}" in this window.</p>
      <div class="col-2">
        <div>
          <h3>Competitor mentions (FR-03-04)</h3>
          <div class="table-wrap"><table><thead><tr><th>Account</th><th class="num">Mentions</th></tr></thead><tbody>${{mentionRows}}</tbody></table></div>
        </div>
        <div>
          <h3>Platform share of voice (FR-03-05)</h3>
          <div class="table-wrap"><table><thead><tr><th>Platform</th><th class="num">Share</th><th class="num">Mentions</th></tr></thead><tbody>${{shareRows}}</tbody></table></div>
        </div>
      </div>
      <h3>Platform keyword ranking (FR-03-06)</h3>
      <div class="col-2">${{rankingBlocks}}</div>
    `;
  }}

  // Source-link hover/focus box: shows every matching post's URL for the
  // row under the pointer/focus. A single shared popover element (not one
  // per row) so it can be positioned near whichever row is active and torn
  // down cleanly on mouseleave/blur.
  let kwLinkPopover = null;
  function showKwLinkPopover(row) {{
    hideKwLinkPopover();
    let urls = [];
    try {{ urls = JSON.parse(row.dataset.urls || '[]'); }} catch (e) {{ urls = []; }}
    const pop = document.createElement('div');
    pop.className = 'kw-link-popover';
    if (urls.length === 0) {{
      const p = document.createElement('p');
      p.className = 'empty';
      p.textContent = 'No source link recorded for these post(s).';
      pop.appendChild(p);
    }} else {{
      urls.forEach((url, i) => {{
        const a = document.createElement('a');
        a.href = url;
        a.target = '_blank';
        a.rel = 'noopener noreferrer';
        a.textContent = `${{i + 1}}. ${{url}}`;
        pop.appendChild(a);
      }});
    }}
    document.body.appendChild(pop);
    // Opens beside the row, not below it - a popover below means the mouse
    // has to cross the next row (and its own hover trigger) to reach it,
    // which flips the popover to that row before the pointer arrives.
    // Sitting to the side keeps a straight, uninterrupted path from the
    // row to the popover.
    const r = row.getBoundingClientRect();
    const spaceRight = document.documentElement.clientWidth - r.right;
    const openLeft = spaceRight < pop.offsetWidth + 20 && r.left > pop.offsetWidth + 20;
    const left = window.scrollX + (openLeft ? r.left - pop.offsetWidth - 10 : r.right + 10);
    let top = window.scrollY + r.top - 4;
    const maxTop = window.scrollY + document.documentElement.clientHeight - pop.offsetHeight - 12;
    if (top > maxTop) top = Math.max(window.scrollY + 12, maxTop);
    pop.style.top = `${{top}}px`;
    pop.style.left = `${{left}}px`;
    kwLinkPopover = pop;
  }}
  function hideKwLinkPopover() {{
    if (kwLinkPopover) {{ kwLinkPopover.remove(); kwLinkPopover = null; }}
  }}
  document.addEventListener('mouseover', e => {{
    const row = e.target.closest('.kw-link-row');
    if (row) showKwLinkPopover(row);
  }});
  document.addEventListener('focusin', e => {{
    const row = e.target.closest('.kw-link-row');
    if (row) showKwLinkPopover(row);
  }});
  document.addEventListener('mouseout', e => {{
    if (e.target.closest('.kw-link-row') && !e.relatedTarget?.closest('.kw-link-popover, .kw-link-row')) hideKwLinkPopover();
  }});
  document.addEventListener('focusout', e => {{
    if (e.target.closest('.kw-link-row') && !e.relatedTarget?.closest('.kw-link-popover, .kw-link-row')) hideKwLinkPopover();
  }});

  // Reply Queue's actual-comment-text popover - same hover/focus pattern as
  // the keyword search's link popover above, showing each comment's author
  // and text instead of a bare URL list.
  let replyCommentsPopover = null;
  function showReplyCommentsPopover(row) {{
    hideReplyCommentsPopover();
    let comments = [];
    try {{ comments = JSON.parse(row.dataset.comments || '[]'); }} catch (e) {{ comments = []; }}
    const pop = document.createElement('div');
    pop.className = 'kw-link-popover';
    if (comments.length === 0) {{
      const p = document.createElement('p');
      p.className = 'empty';
      p.textContent = 'No comments scraped for this post yet.';
      pop.appendChild(p);
    }} else {{
      comments.forEach(c => {{
        const div = document.createElement('div');
        div.className = 'reply-comment-item';
        const author = document.createElement('strong');
        author.textContent = c.author || '(unknown)';
        const text = document.createElement('span');
        text.textContent = c.text || '';
        div.appendChild(author);
        div.appendChild(text);
        pop.appendChild(div);
      }});
    }}
    document.body.appendChild(pop);
    const r = row.getBoundingClientRect();
    const spaceRight = document.documentElement.clientWidth - r.right;
    const openLeft = spaceRight < pop.offsetWidth + 20 && r.left > pop.offsetWidth + 20;
    const left = window.scrollX + (openLeft ? r.left - pop.offsetWidth - 10 : r.right + 10);
    let top = window.scrollY + r.top - 4;
    const maxTop = window.scrollY + document.documentElement.clientHeight - pop.offsetHeight - 12;
    if (top > maxTop) top = Math.max(window.scrollY + 12, maxTop);
    pop.style.top = `${{top}}px`;
    pop.style.left = `${{left}}px`;
    replyCommentsPopover = pop;
  }}
  function hideReplyCommentsPopover() {{
    if (replyCommentsPopover) {{ replyCommentsPopover.remove(); replyCommentsPopover = null; }}
  }}
  document.addEventListener('mouseover', e => {{
    const row = e.target.closest('.reply-comments-row');
    if (row) showReplyCommentsPopover(row);
  }});
  document.addEventListener('focusin', e => {{
    const row = e.target.closest('.reply-comments-row');
    if (row) showReplyCommentsPopover(row);
  }});
  document.addEventListener('mouseout', e => {{
    if (e.target.closest('.reply-comments-row') && !e.relatedTarget?.closest('.kw-link-popover, .reply-comments-row')) hideReplyCommentsPopover();
  }});
  document.addEventListener('focusout', e => {{
    if (e.target.closest('.reply-comments-row') && !e.relatedTarget?.closest('.kw-link-popover, .reply-comments-row')) hideReplyCommentsPopover();
  }});

  // FR-05: no backend on a static site to add an account and start
  // crawling immediately, so the request opens a pre-filled GitHub issue
  // instead (no credentials needed client-side) for elainekao to review
  // and approve locally with add_account.py.
  // Accepts a bare handle or a pasted profile URL (people paste URLs -
  // one already came through as "https://x.com/tphuang" and needed manual
  // cleanup) and normalizes to the bare handle either way.
  function normalizeHandle(raw) {{
    let h = raw.trim();
    h = h.replace(/^https?:\/\/(www\.)?(x\.com|twitter\.com|facebook\.com)\//i, '');
    h = h.replace(/^@/, '').replace(/\/+$/, '');
    h = h.split(/[/?#]/)[0];
    return h;
  }}

  document.getElementById('add-account-btn')?.addEventListener('click', () => {{
    const platform = document.getElementById('add-account-platform').value;
    const handle = normalizeHandle(document.getElementById('add-account-handle').value);
    if (!handle) {{
      document.getElementById('add-account-handle').focus();
      return;
    }}
    const title = `Add account: ${{platform}}/${{handle}}`;
    const body = `Please start tracking this account:\n\n- Platform: ${{platform}}\n- Handle: ${{handle}}\n\nRequested from the dashboard's Account Status tab.`;
    const url = `https://github.com/elainekaotf/TrendForceDashboard/issues/new?title=${{encodeURIComponent(title)}}&body=${{encodeURIComponent(body)}}&labels=add-account`;
    window.open(url, '_blank', 'noopener,noreferrer');
  }});

  // Same static-site constraint as adding: no backend to remove an
  // account on the spot, so this opens a pre-filled GitHub issue too -
  // elainekao reviews and approves it locally with remove_account.py.
  // Two-click inline confirm (Remove -> Confirm remove?) instead of a
  // native confirm() dialog, so it matches the rest of the dashboard's
  // look instead of a jarring OS-styled popup; reverts on its own after
  // a few seconds if the second click never comes.
  document.querySelectorAll('.remove-account-btn').forEach(btn => {{
    let confirmTimer = null;
    btn.addEventListener('click', () => {{
      const {{ platform, handle }} = btn.dataset;
      if (!btn.classList.contains('confirming')) {{
        btn.classList.add('confirming');
        btn.textContent = 'Confirm remove?';
        confirmTimer = setTimeout(() => {{
          btn.classList.remove('confirming');
          btn.textContent = 'Remove';
        }}, 4000);
        return;
      }}
      clearTimeout(confirmTimer);
      btn.classList.remove('confirming');
      btn.textContent = 'Remove';
      const title = `Remove account: ${{platform}}/${{handle}}`;
      const body = `Please stop tracking this account:\n\n- Platform: ${{platform}}\n- Handle: ${{handle}}\n\nRequested from the dashboard's Account Status tab.`;
      const url = `https://github.com/elainekaotf/TrendForceDashboard/issues/new?title=${{encodeURIComponent(title)}}&body=${{encodeURIComponent(body)}}&labels=remove-account`;
      window.open(url, '_blank', 'noopener,noreferrer');
    }});
  }});

  // Competitor Watch: same substring-match-over-KEYWORD_POSTS approach as
  // the Sentiment tab's keyword search, but scoped to `!p.is_own` and
  // surfacing the actual matching posts (not aggregated counts) - "show me
  // every non-TrendForce account's post about X in this window."
  let currentCompetitorKeyword = '';
  const MAX_COMPETITOR_RESULTS = 200;

  function renderCompetitorResults(range) {{
    const container = document.getElementById('competitor-results');
    if (!container) return;
    const kw = currentCompetitorKeyword.trim().toLowerCase();
    if (!kw) {{
      container.innerHTML = '<p class="empty">Type a topic or keyword to see every non-TrendForce account\\'s post mentioning it, for the currently selected time range.</p>';
      return;
    }}
    const bounds = RANGE_BOUNDS[range];
    if (!bounds) {{
      container.innerHTML = '<p class="empty">No data window available for this range.</p>';
      return;
    }}
    const start = new Date(bounds.start), end = new Date(bounds.end);
    const matches = KEYWORD_POSTS.filter(p => {{
      const t = new Date(p.ts);
      return !p.is_own && t >= start && t <= end && p.text.toLowerCase().includes(kw);
    }}).sort((a, b) => new Date(b.ts) - new Date(a.ts));

    if (matches.length === 0) {{
      container.innerHTML = `<p class="empty">No non-TrendForce posts mention "${{escapeHtml(kw)}}" in this time range.</p>`;
      return;
    }}

    const shown = matches.slice(0, MAX_COMPETITOR_RESULTS);
    const rows = shown.map(p => `
      <tr>
        <td class="cell-primary">${{escapeHtml(p.handle)}}</td>
        <td>${{escapeHtml(p.platform)}}</td>
        <td>${{escapeHtml(new Date(p.ts).toLocaleString('en-US', {{timeZone: 'Asia/Taipei', month: 'short', day: '2-digit', hour: '2-digit', minute: '2-digit'}}))}}</td>
        <td>${{escapeHtml(p.text.slice(0, 200))}}</td>
        <td>${{p.url ? `<a href="${{escapeAttr(p.url)}}" target="_blank" rel="noopener noreferrer">Open post</a>` : '—'}}</td>
      </tr>`).join('');

    const truncatedNote = matches.length > MAX_COMPETITOR_RESULTS
      ? `<p class="muted">Showing the ${{MAX_COMPETITOR_RESULTS}} most recent of ${{matches.length}} matching posts.</p>` : '';

    container.innerHTML = `
      <p class="muted">${{matches.length}} non-TrendForce post(s) mention "${{escapeHtml(kw)}}" in this window (Taiwan time).</p>
      ${{truncatedNote}}
      <div class="table-wrap"><table><thead><tr><th>Account</th><th>Platform</th><th>Time</th><th>Post</th><th>Link</th></tr></thead><tbody>${{rows}}</tbody></table></div>
    `;
  }}

  document.addEventListener('input', e => {{
    if (e.target.id === 'keyword-input') {{
      currentKeyword = e.target.value;
      renderKeywordResults(document.getElementById('range-select').value);
    }}
    if (e.target.id === 'competitor-keyword-input') {{
      currentCompetitorKeyword = e.target.value;
      renderCompetitorResults(document.getElementById('range-select').value);
    }}
  }});

  function applyRange(range) {{
    document.getElementById('gaps-content').innerHTML = RANGE_HTML.gaps[range] || '';
    document.getElementById('rising-content').innerHTML = RANGE_HTML.rising[range] || '';
    document.getElementById('sentiment-content').innerHTML = RANGE_HTML.sentiment[range] || '';
    document.getElementById('range-window').textContent = RANGE_WINDOW[range] || '';
    const input = document.getElementById('keyword-input');
    if (input) input.value = currentKeyword;
    renderKeywordResults(range);
    const competitorInput = document.getElementById('competitor-keyword-input');
    if (competitorInput) competitorInput.value = currentCompetitorKeyword;
    renderCompetitorResults(range);
  }}

  document.getElementById('range-select').addEventListener('change', e => applyRange(e.target.value));
  applyRange(document.getElementById('range-select').value);

  // --- FR-07: self-service upload, analyzed entirely client-side (no
  // backend on a static site). Sentiment uses a JS port of VADER's core
  // algorithm (negation, boosters, ALLCAPS/punctuation emphasis,
  // "but"-contrast, compound normalization) over the same lexicon
  // nlp_sentiment.py uses server-side, so scores are consistent with the
  // rest of the dashboard rather than an ad hoc approximation.
  const VADER_LEXICON = {vader_lexicon_json};

  // Chinese sentiment (same NFR-07 gap fixed server-side in nlp_sentiment.py:
  // VADER is English-only and silently scored 100% of Chinese text as
  // neutral). Substring scan over cnsenti's word lists after a character-
  // level Traditional->Simplified pass - see load_chinese_sentiment_data()'s
  // docstring in generate_dashboard.py for why this isn't full jieba+OpenCC.
  const ZH_POS_WORDS = new Set({zh_pos_words_json});
  const ZH_NEG_WORDS = new Set({zh_neg_words_json});
  const ZH_T2S_MAP = {zh_t2s_map_json};
  const ZH_MAX_WORD_LEN = Math.max(...[...ZH_POS_WORDS, ...ZH_NEG_WORDS].map(w => w.length));
  const CJK_RE = /[一-鿿]/g;
  const LATIN_RE = /[A-Za-z]/g;

  function toSimplified(text) {{
    let out = '';
    for (const ch of text) out += ZH_T2S_MAP[ch] || ch;
    return out;
  }}

  function scoreChineseSentiment(text) {{
    const simplified = toSimplified(text).replace(/\s+/g, '');
    let pos = 0, neg = 0;
    for (let i = 0; i < simplified.length; i++) {{
      for (let len = 2; len <= ZH_MAX_WORD_LEN && i + len <= simplified.length; len++) {{
        const w = simplified.slice(i, i + len);
        if (ZH_POS_WORDS.has(w)) pos++;
        if (ZH_NEG_WORDS.has(w)) neg++;
      }}
    }}
    return (pos + neg) ? (pos - neg) / (pos + neg) : 0.0;
  }}
  const VADER_NEGATE = ["aint","arent","cannot","cant","couldnt","darent","didnt","doesnt",
    "ain't","aren't","can't","couldn't","daren't","didn't","doesn't",
    "dont","hadnt","hasnt","havent","isnt","mightnt","mustnt","neither",
    "don't","hadn't","hasn't","haven't","isn't","mightn't","mustn't",
    "neednt","needn't","never","none","nope","nor","not","nothing","nowhere",
    "oughtnt","shant","shouldnt","uhuh","wasnt","werent",
    "oughtn't","shan't","shouldn't","uh-uh","wasn't","weren't",
    "without","wont","wouldnt","won't","wouldn't","rarely","seldom","despite"];
  const B_INCR = 0.293, B_DECR = -0.293, C_INCR = 0.733, N_SCALAR = -0.74;
  const VADER_BOOSTER = {{
    absolutely:B_INCR,amazingly:B_INCR,awfully:B_INCR,completely:B_INCR,considerable:B_INCR,considerably:B_INCR,
    decidedly:B_INCR,deeply:B_INCR,effing:B_INCR,enormous:B_INCR,enormously:B_INCR,entirely:B_INCR,especially:B_INCR,
    exceptional:B_INCR,exceptionally:B_INCR,extreme:B_INCR,extremely:B_INCR,fabulously:B_INCR,flipping:B_INCR,
    flippin:B_INCR,frackin:B_INCR,fracking:B_INCR,fricking:B_INCR,frickin:B_INCR,frigging:B_INCR,friggin:B_INCR,
    fully:B_INCR,greatly:B_INCR,hella:B_INCR,highly:B_INCR,hugely:B_INCR,incredible:B_INCR,incredibly:B_INCR,
    intensely:B_INCR,major:B_INCR,majorly:B_INCR,more:B_INCR,most:B_INCR,particularly:B_INCR,purely:B_INCR,
    quite:B_INCR,really:B_INCR,remarkably:B_INCR,so:B_INCR,substantially:B_INCR,thoroughly:B_INCR,total:B_INCR,
    totally:B_INCR,tremendous:B_INCR,tremendously:B_INCR,uber:B_INCR,unbelievably:B_INCR,unusually:B_INCR,
    utter:B_INCR,utterly:B_INCR,very:B_INCR,
    almost:B_DECR,barely:B_DECR,hardly:B_DECR,less:B_DECR,little:B_DECR,marginal:B_DECR,marginally:B_DECR,
    occasional:B_DECR,occasionally:B_DECR,partly:B_DECR,scarce:B_DECR,scarcely:B_DECR,slight:B_DECR,
    slightly:B_DECR,somewhat:B_DECR,
  }};

  function vaderNegated(word) {{
    const w = word.toLowerCase();
    return VADER_NEGATE.includes(w) || w.includes("n't");
  }}
  function vaderScalar(word, valence, isCapDiff) {{
    const wl = word.toLowerCase();
    if (!(wl in VADER_BOOSTER)) return 0;
    let scalar = VADER_BOOSTER[wl];
    if (valence < 0) scalar *= -1;
    if (word === word.toUpperCase() && isCapDiff) scalar += (valence > 0 ? C_INCR : -C_INCR);
    return scalar;
  }}
  function stripPunc(token) {{
    const stripped = token.replace(/^[.,!?;:'"()\\[\\]{{}}\\-]+|[.,!?;:'"()\\[\\]{{}}\\-]+$/g, '');
    return stripped.length <= 2 ? token : stripped;
  }}
  function vaderScore(text) {{
    const words = (text || '').split(/\s+/).filter(Boolean).map(stripPunc);
    if (!words.length) return 0;
    const upperCount = words.filter(w => w === w.toUpperCase() && /[A-Z]/.test(w)).length;
    const isCapDiff = upperCount > 0 && upperCount < words.length;
    const sentiments = [];
    for (let i = 0; i < words.length; i++) {{
      const wl = words[i].toLowerCase();
      let valence = 0;
      if (wl in VADER_LEXICON) {{
        valence = VADER_LEXICON[wl];
        if (words[i] === words[i].toUpperCase() && isCapDiff) valence += (valence > 0 ? C_INCR : -C_INCR);
        for (let startI = 0; startI < 3; startI++) {{
          if (i > startI && !(words[i - (startI + 1)].toLowerCase() in VADER_LEXICON)) {{
            let s = vaderScalar(words[i - (startI + 1)], valence, isCapDiff);
            if (startI === 1 && s !== 0) s *= 0.95;
            if (startI === 2 && s !== 0) s *= 0.9;
            valence += s;
            if (vaderNegated(words[i - (startI + 1)])) valence *= N_SCALAR;
          }}
        }}
      }}
      sentiments.push(valence);
    }}
    // contrastive "but": halve sentiment before it, boost 1.5x after
    const lower = words.map(w => w.toLowerCase());
    const butIdx = lower.indexOf('but');
    if (butIdx !== -1) {{
      for (let i = 0; i < sentiments.length; i++) sentiments[i] *= (i < butIdx ? 0.5 : (i > butIdx ? 1.5 : 1));
    }}
    let sum = sentiments.reduce((a, b) => a + b, 0);
    const epCount = Math.min((text.match(/!/g) || []).length, 4);
    sum += (sum > 0 ? 1 : sum < 0 ? -1 : 0) * epCount * 0.292;
    return sum / Math.sqrt(sum * sum + 15);
  }}
  function classifySentiment(text) {{
    const cjkCount = (text.match(CJK_RE) || []).length;
    const latinCount = (text.match(LATIN_RE) || []).length;
    const c = cjkCount > latinCount ? scoreChineseSentiment(text) : vaderScore(text);
    return c >= 0.05 ? 'positive' : c <= -0.05 ? 'negative' : 'neutral';
  }}

  // Minimal CSV parser: handles quoted fields with embedded commas/newlines.
  function parseCsv(text) {{
    const rows = [];
    let row = [], field = '', inQuotes = false;
    for (let i = 0; i < text.length; i++) {{
      const c = text[i];
      if (inQuotes) {{
        if (c === '"' && text[i + 1] === '"') {{ field += '"'; i++; }}
        else if (c === '"') {{ inQuotes = false; }}
        else {{ field += c; }}
      }} else {{
        if (c === '"') inQuotes = true;
        else if (c === ',') {{ row.push(field); field = ''; }}
        else if (c === '\\n' || c === '\\r') {{
          if (c === '\\r' && text[i + 1] === '\\n') i++;
          row.push(field); field = '';
          if (row.length > 1 || row[0] !== '') rows.push(row);
          row = [];
        }} else {{ field += c; }}
      }}
    }}
    if (field !== '' || row.length) {{ row.push(field); rows.push(row); }}
    if (!rows.length) return [];
    const headers = rows[0];
    return rows.slice(1).map(r => Object.fromEntries(headers.map((h, i) => [h, r[i] ?? ''])));
  }}

  // Column names only ever matched exact English headers - a Traditional
  // Chinese export (e.g. 內容/時間 instead of content/timestamp) found none
  // of them and failed with "No text/content/message column found", which
  // read as broken rather than "wrong header language." Added the common
  // zh-TW header names actual exports use alongside the English ones.
  const TEXT_COLUMN_CANDIDATES = ['translated_text', 'text', 'content', 'message', 'body',
    '內容', '文字', '貼文', '貼文內容', '內文', '文章內容', '訊息', '標題'];
  const TIMESTAMP_COLUMN_CANDIDATES = ['timestamp', 'created_at', 'date', 'exactDate', 'scrapedAt',
    '時間', '日期', '發布時間', '發文時間', '貼文時間', '建立時間', '時間戳記'];
  // Interaction = every matching engagement column summed per row (likes +
  // comments + shares + ...); views is kept separate since it's a reach
  // metric, not an engagement one, and the two shouldn't be added together.
  const ENGAGEMENT_COLUMN_CANDIDATES = ['likes', 'reactions', 'retweets', 'shares', 'comments', 'replies',
    '讚', '按讚數', '讚數', '留言', '留言數', '評論', '評論數', '分享', '分享數', '轉發'];
  const VIEWS_COLUMN_CANDIDATES = ['views', 'view_count', 'impressions', '觀看數', '瀏覽數', '觀看次數', '瀏覽次數'];

  function detectColumn(fieldnames, candidates) {{
    return candidates.find(c => fieldnames.includes(c)) || null;
  }}

  function detectColumns(fieldnames, candidates) {{
    return candidates.filter(c => fieldnames.includes(c));
  }}

  // Mirrors cluster_topics.py's parse_count: strips commas, handles a
  // trailing K/M shorthand some exports use (e.g. "1.2K", "3M").
  function parseCount(v) {{
    if (v == null || v === '') return 0;
    const s = String(v).trim().replace(/,/g, '');
    const m = s.match(/^([\d.]+)\s*(K|M)?$/i);
    if (!m) return 0;
    const n = parseFloat(m[1]);
    if (isNaN(n)) return 0;
    if (/k/i.test(m[2] || '')) return Math.round(n * 1000);
    if (/m/i.test(m[2] || '')) return Math.round(n * 1000000);
    return Math.round(n);
  }}

  // Convert a wall-clock time assumed to be in `timeZone` to a real UTC
  // Date, using Intl to get that zone's offset at that instant (handles
  // DST automatically, same guarantee zoneinfo gave the Python version).
  function zonedTimeToUtc(y, mo, d, h, mi, s, timeZone) {{
    let guess = new Date(Date.UTC(y, mo - 1, d, h, mi, s));
    for (let i = 0; i < 2; i++) {{
      const dtf = new Intl.DateTimeFormat('en-US', {{
        timeZone, hourCycle: 'h23', year: 'numeric', month: '2-digit', day: '2-digit',
        hour: '2-digit', minute: '2-digit', second: '2-digit',
      }});
      const parts = Object.fromEntries(dtf.formatToParts(guess).map(p => [p.type, p.value]));
      const asIfUtc = Date.UTC(+parts.year, +parts.month - 1, +parts.day, +parts.hour, +parts.minute, +parts.second);
      const offsetMs = asIfUtc - guess.getTime();
      guess = new Date(Date.UTC(y, mo - 1, d, h, mi, s) - offsetMs);
    }}
    return guess;
  }}

  const MONTH_NAMES = ['january','february','march','april','may','june','july','august','september','october','november','december'];

  function to24Hour(h, ampm) {{
    h = Number(h);
    if (!ampm) return h;
    const isPM = /pm/i.test(ampm);
    if (isPM && h !== 12) h += 12;
    if (!isPM && h === 12) h = 0;
    return h;
  }}

  // datetime.fromisoformat()'s JS equivalent (the regex below) only ever
  // matched ISO 8601 - every other common export format (US-style
  // MM/DD/YYYY, or Facebook's own scraper's "Thursday, July 2, 2026 at
  // 1:00 PM" exactDate format, produced by the sibling scraper this same
  // codebase uses) silently failed, leaving converted_timestamp_utc8=null
  // for those rows with no error shown anywhere - "self-service doesn't
  // always work" was this, not a crash. Try ISO first, then these.
  function parseUploadTimestamp(raw, sourceTz) {{
    if (!raw) return {{ date: null, hadOffset: false }};
    const trimmed = raw.trim();
    // Explicit offset/Z present -> parse directly, no zone assumption needed.
    if (/[zZ]$|[+-]\d{{2}}:?\d{{2}}$/.test(trimmed)) {{
      const d = new Date(trimmed);
      return isNaN(d) ? {{ date: null, hadOffset: false }} : {{ date: d, hadOffset: true }};
    }}

    let m = trimmed.match(/^(\d{{4}})-(\d{{2}})-(\d{{2}})[ T](\d{{2}}):(\d{{2}})(?::(\d{{2}}))?/);
    if (m) {{
      const [, y, mo, d, h, mi, s] = m.map(Number);
      return {{ date: zonedTimeToUtc(y, mo, d, h, mi, s || 0, sourceTz), hadOffset: false }};
    }}

    // US-style MM/DD/YYYY[ T]HH:MM[:SS] [AM/PM]
    m = trimmed.match(/^(\d{{1,2}})\/(\d{{1,2}})\/(\d{{2,4}})[ T](\d{{1,2}}):(\d{{2}})(?::(\d{{2}}))?\s*(AM|PM)?/i);
    if (m) {{
      let [, mo, d, y, h, mi, s, ampm] = m;
      y = Number(y); if (y < 100) y += 2000;
      return {{ date: zonedTimeToUtc(y, Number(mo), Number(d), to24Hour(h, ampm), Number(mi), Number(s || 0), sourceTz), hadOffset: false }};
    }}

    // "Month DD, YYYY [at] HH:MM [AM/PM]", optionally prefixed with a
    // weekday name (Facebook's own exactDate format).
    m = trimmed.match(/^(?:[A-Za-z]+,\s*)?([A-Za-z]+)\s+(\d{{1,2}}),?\s+(\d{{4}})(?:\s+at)?\s+(\d{{1,2}}):(\d{{2}})\s*(AM|PM)?/i);
    if (m) {{
      const [, monthName, d, y, h, mi, ampm] = m;
      const mo = MONTH_NAMES.indexOf(monthName.toLowerCase()) + 1;
      if (mo > 0) {{
        return {{ date: zonedTimeToUtc(Number(y), mo, Number(d), to24Hour(h, ampm), Number(mi), 0, sourceTz), hadOffset: false }};
      }}
    }}

    return {{ date: null, hadOffset: false }};
  }}

  function csvCell(v) {{
    const s = String(v ?? '');
    return /[",\\n]/.test(s) ? '"' + s.replace(/"/g, '""') + '"' : s;
  }}

  function downloadBlob(filename, content, type) {{
    const blob = new Blob([content], {{ type }});
    const url = URL.createObjectURL(blob);
    const a = document.createElement('a');
    a.href = url; a.download = filename; a.click();
    URL.revokeObjectURL(url);
  }}

  let uploadedRows = null, uploadedFields = null, uploadedBaseName = 'upload';

  const UPLOAD_PAGE_SIZE = 25;
  let uploadSortKey = 'time_desc';
  let uploadPage = 1;
  let uploadSearchTerm = '';

  // English stopwords (reused from a small fixed list - full server-side
  // clustering pulls sklearn's, out of reach client-side) plus a length
  // floor, so "the/and/for" don't dominate every upload's topic list.
  const EN_STOPWORDS = new Set(['the','and','for','are','but','not','you','all','can','her','was','one','our','out',
    'day','get','has','him','his','how','man','new','now','old','see','two','way','who','boy','did','its','let',
    'put','say','she','too','use','with','this','that','from','have','more','will','your','about','which','their',
    'said','there','been','would','could','should','into','than','them','then','when','what','were','are',
    // Generic filler/sentiment-adjacent words - not stopwords in the
    // traditional sense, but redundant with the Sentiment column and
    // common enough to otherwise crowd out actual entities/topics in a
    // frequency-only extraction with no TF-IDF corpus to weight against.
    'just','today','regarding','really','very','also','some','amazing','great','good','bad','terrible',
    'awesome','disappointing','news','update','report','story','article',
    // Geography and generic non-technical business words - "topics" here
    // are meant to read as industry/technical subject matter (chip names,
    // companies, products), not country names or generic corporate filler
    // that says nothing about what's actually being discussed.
    'china','chinese','usa','america','american','united','states','taiwan','taiwanese','japan','japanese',
    'korea','korean','europe','european','global','world','worldwide','international','domestic','local',
    'company','companies','business','businesses','market','markets','industry','industries','economy',
    'economic','government','country','countries','nation','national','region','regional','sector',
    'firm','firms','corporate','corporation','group','groups']);

  function esc(s) {{ const d = document.createElement('div'); d.textContent = s == null ? '' : String(s); return d.innerHTML; }}

  // Lightweight client-side topic extraction - not the server-side pipeline's
  // TF-IDF + K-Means (that needs a Python ML stack this static page doesn't
  // have), just word/phrase frequency: English words (3+ letters, stopwords
  // dropped) and, since there's no jieba segmenter in the browser either,
  // frequent 2-4 character substrings within each contiguous CJK run as an
  // approximation of Chinese compound words. Good enough to surface "what
  // does this upload mostly talk about," not a claim of real clustering.
  // Same reasoning as EN_STOPWORDS - geography and generic business/
  // economy words read as noise in an "industry topics" list, not signal.
  const ZH_TOPIC_STOPWORDS = new Set(['中國','中國大陸','大陸','美國','台灣','日本','韓國','歐洲','全球','世界',
    '國際','國內','本地','地區','區域','公司','企業','商業','市場','產業','行業','經濟','政府','國家','國家隊',
    '集團','廠商','業者','今天','今年','昨天','明天','目前','最近','報導','新聞','指出','表示']);

  // Ported from cluster_topics.py's CHINESE_NOISE_SUBSTRINGS/CHINESE_UNIT_TOKEN_RE
  // (kept in sync by hand - no shared module between Python and this
  // embedded page's JS). An exact-match stopword set alone can't catch a
  // combinatorial family like [億|萬|千|兆][元|日圓|美元|韓元] - same gap
  // that motivated the server-side pattern version in the first place, and
  // this client-side extractor had the same fixed-list-only version of the
  // problem until now.
  const ZH_NOISE_RE = new RegExp([
    '年增','年減','月增','月減','季增','季減','去年','今年','明年',
    '本季','上季','下季','本月','上月','下月','同期','同比',
    '目前','近期','日前','日起','報導','指出','表示','預估','預計','據悉',
    '營收','獲利','毛利率','目標價','股價','創新高','創下','新高','新低',
    '百分點','歷史新高','央行','因此','經濟日報','導讀','reurl',
    '年的','年至','年間','年以來','過去',
  ].join('|'));
  const ZH_UNIT_TOKEN_RE = /^[0-9億萬千兆]+[元日圓韓美歐]{0,2}$/;

  function isChineseNoiseTerm(term) {{
    return ZH_UNIT_TOKEN_RE.test(term) || ZH_NOISE_RE.test(term);
  }}

  function extractTopics(rows, textCol, topN) {{
    const counts = new Map();
    const bump = (term) => counts.set(term, (counts.get(term) || 0) + 1);
    for (const r of rows) {{
      const text = String(r[textCol] || '');
      const seenInRow = new Set();
      for (const w of text.toLowerCase().match(/[a-z]{{3,}}/g) || []) {{
        if (EN_STOPWORDS.has(w) || seenInRow.has(w)) continue;
        seenInRow.add(w);
        bump(w);
      }}
      for (const run of text.match(/[一-鿿]{{2,}}/g) || []) {{
        for (let len = 2; len <= 4 && len <= run.length; len++) {{
          for (let i = 0; i + len <= run.length; i++) {{
            const term = run.slice(i, i + len);
            if (seenInRow.has(term) || ZH_TOPIC_STOPWORDS.has(term) || isChineseNoiseTerm(term)) continue;
            seenInRow.add(term);
            bump(term);
          }}
        }}
      }}
    }}
    // A term in nearly every row is boilerplate (a shared connector word,
    // a recurring sign-off), not a topic - a real topic differentiates
    // SOME posts from others. Cap at 60% of rows so frequency alone can't
    // let a common word crowd out the actual distinguishing terms.
    const maxAllowed = Math.max(2, Math.floor(rows.length * 0.6));
    return [...counts.entries()]
      .filter(([, c]) => c >= 2 && c <= maxAllowed)
      .sort((a, b) => b[1] - a[1])
      .slice(0, topN)
      .map(([term, count]) => ({{ term, count }}));
  }}

  function assignTopic(text, topics) {{
    const lower = String(text || '').toLowerCase();
    for (const {{ term }} of topics) {{
      if (lower.includes(term)) return term;
    }}
    return '(other)';
  }}

  function sortUploadRows(rows, key) {{
    const sorted = rows.slice();
    const sentimentRank = {{ positive: 2, neutral: 1, negative: 0 }};
    switch (key) {{
      case 'time_desc': return sorted.sort((a, b) => (b.converted_timestamp_utc8 || '').localeCompare(a.converted_timestamp_utc8 || ''));
      case 'time_asc': return sorted.sort((a, b) => (a.converted_timestamp_utc8 || '').localeCompare(b.converted_timestamp_utc8 || ''));
      case 'sentiment_desc': return sorted.sort((a, b) => sentimentRank[b.sentiment] - sentimentRank[a.sentiment]);
      case 'sentiment_asc': return sorted.sort((a, b) => sentimentRank[a.sentiment] - sentimentRank[b.sentiment]);
      case 'topic': return sorted.sort((a, b) => a.topic.localeCompare(b.topic));
      case 'interaction_desc': return sorted.sort((a, b) => b._interaction - a._interaction);
      case 'views_desc': return sorted.sort((a, b) => (b._views || 0) - (a._views || 0));
      default: return sorted;
    }}
  }}

  function renderUploadPostsPage(rows, meta) {{
    const container = document.getElementById('upload-posts-table');
    const pagerEl = document.getElementById('upload-pager');
    const term = uploadSearchTerm.trim().toLowerCase();
    const filtered = term
      ? rows.filter(r => String(r[meta.textCol] || '').toLowerCase().includes(term) || r.topic.toLowerCase().includes(term))
      : rows;
    const sorted = sortUploadRows(filtered, uploadSortKey);
    const totalPages = Math.max(1, Math.ceil(sorted.length / UPLOAD_PAGE_SIZE));
    uploadPage = Math.min(Math.max(1, uploadPage), totalPages);
    const start = (uploadPage - 1) * UPLOAD_PAGE_SIZE;
    const pageRows = sorted.slice(start, start + UPLOAD_PAGE_SIZE);

    const extraCols = (meta.hasEngagement ? 1 : 0) + (meta.hasViews ? 1 : 0);
    const bodyRows = pageRows.map(r => `
      <tr><td>${{esc(r[meta.textCol] || '').slice(0, 90)}}</td>
      <td>${{esc(r.topic)}}</td>
      <td>${{esc(r.converted_timestamp_utc8 || '—')}}</td>
      <td><span class="badge status-${{r.sentiment === 'positive' ? 'active' : r.sentiment === 'negative' ? 'inactive' : 'stale'}}">${{esc(r.sentiment)}}</span></td>
      ${{meta.hasEngagement ? `<td class="num">${{r._interaction.toLocaleString()}}</td>` : ''}}
      ${{meta.hasViews ? `<td class="num">${{(r._views || 0).toLocaleString()}}</td>` : ''}}</tr>`).join('')
      || `<tr><td colspan="${{4 + extraCols}}" class="empty">No posts match "${{esc(term)}}".</td></tr>`;
    container.innerHTML = `<div class="table-wrap"><table><thead><tr><th>Text</th><th>Topic</th><th>Converted (UTC+8)</th><th>Sentiment</th>
      ${{meta.hasEngagement ? '<th class="num">Interaction</th>' : ''}}
      ${{meta.hasViews ? '<th class="num">Views</th>' : ''}}
      </tr></thead><tbody>${{bodyRows}}</tbody></table></div>`;

    const pageBtns = [];
    for (let p = 1; p <= totalPages; p++) {{
      pageBtns.push(`<button class="page-btn${{p === uploadPage ? ' active' : ''}}" data-page="${{p}}">${{p}}</button>`);
    }}
    pagerEl.innerHTML = totalPages > 1
      ? `<div class="pager">${{pageBtns.join('')}}</div><p class="muted">Showing ${{start + 1}}-${{Math.min(start + UPLOAD_PAGE_SIZE, sorted.length)}} of ${{sorted.length}}</p>`
      : `<p class="muted">${{sorted.length}} post(s)</p>`;
    pagerEl.querySelectorAll('.page-btn').forEach(btn => {{
      btn.addEventListener('click', () => {{
        uploadPage = Number(btn.dataset.page);
        renderUploadPostsPage(rows, meta);
      }});
    }});
  }}

  function renderUploadResults(rows, fields, meta) {{
    const container = document.getElementById('upload-results');
    if (!rows.length) {{
      container.innerHTML = '<p class="empty">No rows found (or no recognizable text column).</p>';
      return;
    }}
    const counts = {{ positive: 0, neutral: 0, negative: 0 }};
    let withOffset = 0;
    for (const r of rows) {{
      counts[r.sentiment]++;
      if (r._hadOffset) withOffset++;
    }}
    const total = rows.length;
    const rankRows = Object.entries(counts).map(([k, v]) =>
      `<tr><td class="cell-primary">${{k}}</td><td class="num">${{v}}</td><td class="num">${{Math.round(v/total*1000)/10}}%</td></tr>`).join('');

    const topics = extractTopics(rows, meta.textCol, 12);
    for (const r of rows) r.topic = assignTopic(r[meta.textCol], topics);
    const topicRows = topics.map(({{ term, count }}) =>
      `<tr><td class="cell-primary">${{esc(term)}}</td><td class="num">${{count}}</td></tr>`).join('');

    uploadSortKey = 'time_desc'; uploadPage = 1; uploadSearchTerm = '';

    container.innerHTML = `
      <div class="stat-grid" style="margin-bottom:16px">
        <div class="stat"><div class="stat-num">${{total}}</div><div class="stat-label">Rows</div></div>
        <div class="stat"><div class="stat-num">${{withOffset}}/${{total}}</div><div class="stat-label">Had explicit offset</div></div>
      </div>
      <div class="col-2">
        <div>${{table_(['Sentiment', '#Count', '#Share'], rankRows)}}</div>
        <div>
          ${{table_(['Topic', '#Mentions'], topicRows || '<tr><td colspan="2" class="empty">Not enough repeated words to surface a topic.</td></tr>')}}
        </div>
      </div>
      <div class="upload-posts-toolbar">
        <label class="upload-search-label">Search a topic or keyword
          <input type="text" id="upload-search-input" placeholder="e.g. nvidia, tariff, dram..." autocomplete="off">
        </label>
        <label>Sort by
          <select id="upload-sort-select">
            <option value="time_desc">Newest first</option>
            <option value="time_asc">Oldest first</option>
            <option value="sentiment_desc">Sentiment: positive first</option>
            <option value="sentiment_asc">Sentiment: negative first</option>
            <option value="topic">Topic (A-Z)</option>
            ${{meta.hasEngagement ? '<option value="interaction_desc">Interaction: most first</option>' : ''}}
            ${{meta.hasViews ? '<option value="views_desc">Views: most first</option>' : ''}}
          </select>
        </label>
      </div>
      <div id="upload-posts-table"></div>
      <div id="upload-pager"></div>
      <div class="download-row">
        <button class="btn" id="download-csv-btn">Download CSV</button>
        <button class="btn" id="download-xlsx-btn">Download Excel</button>
        <button class="btn" id="download-print-btn">Print / Save as PDF</button>
      </div>
    `;
    function table_(headers, rowsHtml) {{
      const head = headers.map(h => h.startsWith('#') ? `<th class="num">${{h.slice(1)}}</th>` : `<th>${{h}}</th>`).join('');
      return `<div class="table-wrap"><table><thead><tr>${{head}}</tr></thead><tbody>${{rowsHtml}}</tbody></table></div>`;
    }}

    renderUploadPostsPage(rows, meta);
    document.getElementById('upload-sort-select').addEventListener('change', e => {{
      uploadSortKey = e.target.value;
      uploadPage = 1;
      renderUploadPostsPage(rows, meta);
    }});
    document.getElementById('upload-search-input').addEventListener('input', e => {{
      uploadSearchTerm = e.target.value;
      uploadPage = 1;
      renderUploadPostsPage(rows, meta);
    }});

    const outFields = [...fields, 'converted_timestamp_utc8', 'sentiment', 'topic'];
    document.getElementById('download-csv-btn').onclick = () => {{
      const lines = [outFields.map(csvCell).join(',')];
      for (const r of rows) lines.push(outFields.map(f => csvCell(r[f])).join(','));
      downloadBlob(uploadedBaseName + '_analyzed.csv', lines.join('\\n'), 'text/csv');
    }};
    document.getElementById('download-xlsx-btn').onclick = () => {{
      const sheetRows = rows.map(r => Object.fromEntries(outFields.map(f => [f, r[f] ?? ''])));
      const sheet = XLSX.utils.json_to_sheet(sheetRows, {{ header: outFields }});
      const wb = XLSX.utils.book_new();
      XLSX.utils.book_append_sheet(wb, sheet, 'Analyzed');
      XLSX.writeFile(wb, uploadedBaseName + '_analyzed.xlsx');
    }};
    document.getElementById('download-print-btn').onclick = () => window.print();
  }}

  function analyzeUploadedRows(parsed) {{
    if (!parsed.length) {{
      document.getElementById('upload-results').innerHTML = '<p class="empty">Could not parse any rows from this file.</p>';
      return;
    }}
    const fields = Object.keys(parsed[0]);
    const textCol = detectColumn(fields, TEXT_COLUMN_CANDIDATES);
    const tsCol = detectColumn(fields, TIMESTAMP_COLUMN_CANDIDATES);
    if (!textCol) {{
      document.getElementById('upload-results').innerHTML = '<p class="empty">No text/content/message column found in this file.</p>';
      return;
    }}
    const engagementCols = detectColumns(fields, ENGAGEMENT_COLUMN_CANDIDATES);
    const viewsCol = detectColumn(fields, VIEWS_COLUMN_CANDIDATES);
    const sourceTz = document.getElementById('upload-tz').value;
    const rows = parsed.map(r => {{
      const {{ date, hadOffset }} = tsCol ? parseUploadTimestamp(String(r[tsCol] ?? ''), sourceTz) : {{ date: null, hadOffset: false }};
      return {{
        ...r,
        sentiment: classifySentiment(r[textCol]),
        converted_timestamp_utc8: date ? new Intl.DateTimeFormat('sv-SE', {{
          timeZone: 'Asia/Taipei', year: 'numeric', month: '2-digit', day: '2-digit',
          hour: '2-digit', minute: '2-digit', second: '2-digit', hourCycle: 'h23',
        }}).format(date).replace(' ', 'T') + '+08:00' : null,
        _hadOffset: hadOffset,
        _interaction: engagementCols.reduce((sum, c) => sum + parseCount(r[c]), 0),
        _views: viewsCol ? parseCount(r[viewsCol]) : null,
      }};
    }});
    uploadedRows = rows; uploadedFields = fields;
    renderUploadResults(rows, fields, {{ textCol, tsCol, hasEngagement: engagementCols.length > 0, hasViews: !!viewsCol }});
  }}

  document.getElementById('upload-file').addEventListener('change', e => {{
    const file = e.target.files[0];
    if (!file) return;
    const isExcel = /\.xlsx$/i.test(file.name);
    uploadedBaseName = file.name.replace(/\.(csv|xlsx)$/i, '');
    const reader = new FileReader();
    reader.onload = () => {{
      if (isExcel) {{
        const wb = XLSX.read(reader.result, {{ type: 'array' }});
        const sheet = wb.Sheets[wb.SheetNames[0]];
        analyzeUploadedRows(XLSX.utils.sheet_to_json(sheet, {{ defval: '' }}));
      }} else {{
        analyzeUploadedRows(parseCsv(reader.result));
      }}
    }};
    if (isExcel) reader.readAsArrayBuffer(file);
    else reader.readAsText(file);
  }});
</script>
</body></html>"""

    with open(OUT_FILE, 'w', encoding='utf-8') as f:
        f.write(html)
    print(f"Wrote dashboard to {OUT_FILE}")


if __name__ == '__main__':
    main()
