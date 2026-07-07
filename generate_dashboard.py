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
"""
import json
import os
import urllib.parse
from datetime import datetime, timezone, timedelta

from time_ranges import RANGE_ORDER, RANGE_LABELS

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


def esc(s):
    if s is None:
        return ''
    return (str(s).replace('&', '&amp;').replace('<', '&lt;').replace('>', '&gt;').replace('"', '&quot;'))


def fmt_int(n):
    return f"{n:,}" if isinstance(n, (int, float)) else esc(n)


# --- Section builders --------------------------------------------------
def render_topic_gaps(data):
    if not data:
        return '<p class="empty">No FR-01 data yet — run cluster_topics.py.</p>'
    gaps = sorted(data.get('gaps', []), key=lambda g: g['competitor_engagement'], reverse=True)[:10]
    if not gaps:
        return '<p class="empty">No topic gaps detected.</p>'
    rows = ''.join(f"""
      <tr>
        <td>{esc(g['label'])}</td>
        <td class="num">{fmt_int(g['own_count'])}</td>
        <td class="num">{fmt_int(g['competitor_count'])}</td>
        <td class="num">{fmt_int(g['competitor_engagement'])}</td>
        <td>{esc(', '.join(g['competitors_covering'][:4]))}</td>
      </tr>""" for g in gaps)
    return f"""
    <div class="table-wrap"><table>
      <thead><tr><th>Topic</th><th>Our posts</th><th>Competitor posts</th><th>Competitor engagement</th><th>Covered by</th></tr></thead>
      <tbody>{rows}</tbody>
    </table></div>"""


def render_rising_topics(data):
    if not data:
        return '<p class="empty">No FR-02 data yet — run fuzzy_trend.py.</p>'
    sections = []
    for platform, pdata in data.get('platforms', {}).items():
        topics = pdata.get('top_rising_topics', [])
        cards = ''.join(f"""
        <div class="card">
          <div class="card-head">
            <span class="badge score">{t['rising_score']}</span>
            <strong>{esc(t['label'])}</strong>
          </div>
          <div class="muted">{esc(t['rationale'])}</div>
          <div class="kols">{''.join(f'<span class="chip">{esc(k["handle"])} ({k["rising_score"]})</span>' for k in t['rising_kols'][:4])}</div>
        </div>""" for t in topics)
        sections.append(f'<h3>{esc(platform)}</h3><div class="card-grid">{cards}</div>')
    return ''.join(sections)


def render_sentiment(data):
    if not data:
        return '<p class="empty">No FR-03 data yet — run nlp_sentiment.py.</p>'
    w = data['widgets']
    overview = w['sentiment_overview']
    share = overview.get('sentiment_share', {})
    stat_cards = f"""
    <div class="stat-grid">
      <div class="stat"><div class="stat-num">{fmt_int(overview['total_posts'])}</div><div class="stat-label">Posts ({esc(data['time_range'])})</div></div>
      <div class="stat pos"><div class="stat-num">{round(share.get('positive', 0) * 100, 1)}%</div><div class="stat-label">Positive</div></div>
      <div class="stat neu"><div class="stat-num">{round(share.get('neutral', 0) * 100, 1)}%</div><div class="stat-label">Neutral</div></div>
      <div class="stat neg"><div class="stat-num">{round(share.get('negative', 0) * 100, 1)}%</div><div class="stat-label">Negative</div></div>
    </div>"""

    heat_rows = ''.join(f"""
      <tr><td>{esc(b['label'])}</td><td class="num heat-{('hot' if b['heat']>=70 else 'warm' if b['heat']>=40 else 'cold')}">{b['heat']}</td>
      <td class="num">{fmt_int(b['volume'])}</td><td class="num">{fmt_int(b['engagement'])}</td></tr>"""
      for b in w['temperature_bar'][:10])

    engagement_rows = ''.join(f"""
      <tr><td>{esc(r['label'])}</td><td class="num">{fmt_int(r['total_engagement'])}</td><td class="num">{r['post_count']}</td></tr>"""
      for r in w['top_engagement_ranking'][:8])

    slots = w['posting_timeslot_analysis']['slots']
    peak = w['posting_timeslot_analysis']['peak_slot']
    slot_rows = ''.join(f"""
      <tr class="{'peak' if name == peak else ''}"><td>{esc(name)}</td><td class="num">{s['post_count']}</td>
      <td class="num">{fmt_int(s['likes'])}</td><td class="num">{fmt_int(s['engagement'])}</td></tr>"""
      for name, s in slots.items())

    keyword_search_html = """
    <h3>Keyword search (FR-03-04/05/06)</h3>
    <div class="keyword-search-bar">
      <input type="text" id="keyword-input" placeholder="Search a keyword, e.g. nvidia, tariff, dram..." autocomplete="off">
    </div>
    <div id="keyword-results"><p class="empty">Type a keyword to see mention counts by account and platform, for the currently selected time range.</p></div>
    """

    return f"""
    {stat_cards}
    {keyword_search_html}
    <div class="col-2">
      <div>
        <h3>Temperature bar</h3>
        <div class="table-wrap"><table><thead><tr><th>Topic</th><th>Heat</th><th>Volume</th><th>Engagement</th></tr></thead><tbody>{heat_rows}</tbody></table></div>
      </div>
      <div>
        <h3>Top engagement</h3>
        <div class="table-wrap"><table><thead><tr><th>Topic</th><th>Engagement</th><th>Posts</th></tr></thead><tbody>{engagement_rows}</tbody></table></div>
      </div>
    </div>
    <h3>Posting time-slot analysis (Mon-Fri, peak highlighted)</h3>
    <div class="table-wrap"><table><thead><tr><th>Slot</th><th>Posts</th><th>Likes</th><th>Engagement</th></tr></thead><tbody>{slot_rows}</tbody></table></div>"""


def render_summaries(data):
    if not data:
        return '<p class="empty">No FR-06 data yet — run generate_summaries.py.</p>'
    cards = ''.join(f"""
      <div class="summary-card">
        <span class="badge cat">{esc(s['category'])}</span>
        <span class="char-count">{s['char_count']} chars</span>
        <p>{esc(s['text'])}</p>
      </div>""" for s in data.get('summaries', []))
    return f"<div class='muted'>Generated {esc(data['generated_at'])}</div><div class='summary-grid'>{cards}</div>"


def render_accounts(data):
    if not data:
        return '<p class="empty">No FR-05 data yet — run account_comment_management.py build.</p>'
    rows = ''.join(f"""
      <tr>
        <td>{esc(a['handle'])}{' <span class="badge own">own</span>' if a['is_own'] else ''}</td>
        <td>{esc(a['platform'])}</td>
        <td><span class="badge status-{esc(a['status'])}">{esc(a['status'])}</span></td>
        <td class="num">{fmt_int(a['follower_count']) if a['follower_count'] else '—'}</td>
        <td class="num">{fmt_int(a['post_count'])}</td>
        <td>{esc(a['last_post_at'] or '—')}</td>
      </tr>""" for a in data.get('accounts', []))
    return f"""
    <div class="table-wrap"><table>
      <thead><tr><th>Handle</th><th>Platform</th><th>Status</th><th>Followers</th><th>Posts</th><th>Last post</th></tr></thead>
      <tbody>{rows}</tbody>
    </table></div>"""


def render_reply_queue(data):
    if not data:
        return '<p class="empty">No FR-05 reply drafts yet.</p>'
    records = sorted(data.values(), key=lambda r: r['reply_count'], reverse=True)
    if not records:
        return '<p class="empty">No own-account posts currently need a response.</p>'
    rows = ''.join(f"""
      <tr>
        <td><span class="badge status-{esc(r['status'])}">{esc(r['status'])}</span></td>
        <td>{esc(r['handle'])}</td>
        <td class="num">{r['reply_count']}</td>
        <td>{esc(r['topic_label'])}</td>
        <td>{esc(r['draft_reply'])}</td>
      </tr>""" for r in records)
    return f"""
    <div class="table-wrap"><table>
      <thead><tr><th>Status</th><th>Account</th><th>Replies</th><th>Topic</th><th>Draft reply</th></tr></thead>
      <tbody>{rows}</tbody>
    </table></div>"""


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
    type_chips = ''.join(f'<span class="chip">{esc(t)}: {fmt_int(c)}</span>' for t, c in sorted(by_type.items()))

    pending = [r for r in records if r['status'] == 'pending'][:10]
    rows = ''.join(f"""
      <tr><td>{esc(r['type'])}</td><td>{esc(r.get('platform', ''))}</td><td>{esc(r.get('handle', ''))}</td>
      <td>{esc((r['automated'].get('topic_label') or r['automated'].get('rationale') or r['automated'].get('text', ''))[:80])}</td></tr>"""
      for r in pending)

    return f"""
    <div class="stat-grid">{status_cards}</div>
    <div class="muted">{fmt_int(len(records))} total records — {type_chips}</div>
    <h3>Sample of pending items</h3>
    <div class="table-wrap"><table><thead><tr><th>Type</th><th>Platform</th><th>Handle</th><th>Automated label</th></tr></thead><tbody>{rows}</tbody></table></div>"""


def main():
    os.makedirs(DOCS_DIR, exist_ok=True)

    gaps_html_by_range = {}
    rising_html_by_range = {}
    sentiment_html_by_range = {}
    window_caption_by_range = {}
    window_bounds_by_range = {}
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
        window_caption_by_range[range_key] = (
            f"Data window: {esc(window['start_tw'])} – {esc(window['end_tw'])} (Taiwan time)"
            if window else 'No window data available for this range.'
        )
        window_bounds_by_range[range_key] = (
            {'start': window['start_utc'], 'end': window['end_utc']} if window else None
        )

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

    html = f"""<!doctype html>
<html lang="en"><head>
<meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>TrendForceDash</title>
<link rel="icon" href="data:image/svg+xml,{FAVICON_SVG}">
<style>
  :root {{ --bg: #0d1117; --surface: #161b22; --border: #30363d; --text: #e6edf3; --muted: #8b949e;
           --blue: #3b9eff; --gold: #f0b429; --green: #3fb950; --red: #f85149; --yellow: #d29922; }}
  * {{ box-sizing: border-box; }}
  body {{ background: var(--bg); color: var(--text); font-family: -apple-system, "Segoe UI", sans-serif; margin: 0; }}
  header {{ padding: 24px 32px; border-bottom: 1px solid var(--border); }}
  header h1 {{ margin: 0; font-size: 22px; }}
  header .muted {{ margin-top: 4px; }}
  .muted {{ color: var(--muted); font-size: 13px; }}
  nav {{ display: flex; gap: 4px; padding: 0 32px; border-bottom: 1px solid var(--border); overflow-x: auto; }}
  nav button {{ background: none; border: none; color: var(--muted); padding: 12px 16px; font-size: 14px;
                cursor: pointer; border-bottom: 2px solid transparent; white-space: nowrap; }}
  nav button.active {{ color: var(--text); border-bottom-color: var(--blue); }}
  main {{ padding: 24px 32px; max-width: 1200px; margin: 0 auto; }}
  .range-bar {{ display: flex; align-items: center; gap: 10px; margin-bottom: 20px; }}
  .range-bar label {{ color: var(--muted); font-size: 13px; }}
  .range-bar select {{ background: var(--surface); color: var(--text); border: 1px solid var(--border);
                        border-radius: 6px; padding: 6px 10px; font-size: 13px; }}
  .keyword-search-bar {{ margin-bottom: 12px; }}
  .keyword-search-bar input {{ width: 100%; max-width: 420px; background: var(--surface); color: var(--text);
                                border: 1px solid var(--border); border-radius: 6px; padding: 8px 12px; font-size: 13px; }}
  .keyword-search-bar input:focus {{ outline: none; border-color: var(--blue); }}
  section {{ display: none; }}
  section.active {{ display: block; }}
  h2 {{ font-size: 18px; margin-top: 0; }}
  h3 {{ font-size: 14px; color: var(--muted); text-transform: uppercase; letter-spacing: 0.04em; margin: 20px 0 8px; }}
  .table-wrap {{ overflow-x: auto; margin-bottom: 16px; }}
  table {{ width: 100%; min-width: 480px; border-collapse: collapse; font-size: 13px; }}
  th, td {{ text-align: left; padding: 8px 10px; border-bottom: 1px solid var(--border); }}
  th {{ color: var(--muted); font-weight: 600; }}
  td.num, th.num {{ text-align: right; font-variant-numeric: tabular-nums; }}
  tr.peak {{ background: rgba(59, 158, 255, 0.08); }}
  .empty {{ color: var(--muted); font-style: italic; }}
  .col-2 {{ display: grid; grid-template-columns: 1fr 1fr; gap: 24px; }}
  .card-grid {{ display: grid; grid-template-columns: repeat(auto-fill, minmax(260px, 1fr)); gap: 12px; margin-bottom: 16px; }}
  .card {{ background: var(--surface); border: 1px solid var(--border); border-radius: 8px; padding: 12px 14px; }}
  .card-head {{ display: flex; align-items: center; gap: 8px; margin-bottom: 6px; }}
  .kols {{ margin-top: 8px; display: flex; flex-wrap: wrap; gap: 4px; }}
  .chip {{ background: var(--bg); border: 1px solid var(--border); border-radius: 999px; padding: 2px 8px; font-size: 11px; color: var(--muted); }}
  .badge {{ display: inline-block; padding: 2px 8px; border-radius: 999px; font-size: 11px; font-weight: 600; }}
  .badge.score {{ background: var(--gold); color: #000; }}
  .badge.cat {{ background: var(--blue); color: #000; margin-right: 8px; }}
  .badge.own {{ background: var(--green); color: #000; }}
  .badge.status-active, .badge.status-sent, .badge.status-approved {{ background: var(--green); color: #000; }}
  .badge.status-stale, .badge.status-drafted, .badge.status-pending {{ background: var(--yellow); color: #000; }}
  .badge.status-inactive, .badge.status-dismissed {{ background: var(--red); color: #000; }}
  .heat-hot {{ color: var(--red); font-weight: 700; }}
  .heat-warm {{ color: var(--yellow); }}
  .heat-cold {{ color: var(--muted); }}
  .stat-grid {{ display: grid; grid-template-columns: repeat(auto-fit, minmax(120px, 1fr)); gap: 12px; margin-bottom: 16px; }}
  .stat {{ background: var(--surface); border: 1px solid var(--border); border-radius: 8px; padding: 12px; text-align: center; }}
  .stat-num {{ font-size: 22px; font-weight: 700; }}
  .stat-label {{ color: var(--muted); font-size: 12px; text-transform: capitalize; }}
  .stat.pos .stat-num {{ color: var(--green); }} .stat.neg .stat-num {{ color: var(--red); }} .stat.neu .stat-num {{ color: var(--muted); }}
  .summary-grid {{ display: grid; grid-template-columns: repeat(auto-fill, minmax(280px, 1fr)); gap: 12px; }}
  .summary-card {{ background: var(--surface); border: 1px solid var(--border); border-radius: 8px; padding: 12px 14px; }}
  .summary-card p {{ margin: 8px 0 0; font-size: 13px; line-height: 1.5; }}
  .char-count {{ color: var(--muted); font-size: 11px; float: right; }}
  @media (max-width: 800px) {{ .col-2 {{ grid-template-columns: 1fr; }} }}
</style>
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
  <button class="tab-btn" data-tab="summaries">Daily Summaries</button>
  <button class="tab-btn" data-tab="accounts">Accounts</button>
  <button class="tab-btn" data-tab="replies">Reply Queue</button>
  <button class="tab-btn" data-tab="review">Review Queue</button>
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
  <section id="summaries"><h2>FR-06 &middot; Daily Executive Summaries</h2>{render_summaries(daily_summaries)}</section>
  <section id="accounts"><h2>FR-05 &middot; Account Status</h2>{render_accounts(account_status)}</section>
  <section id="replies"><h2>FR-05 &middot; Reply Queue</h2>{render_reply_queue(reply_queue)}</section>
  <section id="review"><h2>FR-04 &middot; Manual Review Queue</h2>{render_review_queue(review_queue)}</section>
</main>
<script>
  const RANGE_HTML = {range_data_json};
  const RANGE_WINDOW = {window_caption_json};
  const RANGE_BOUNDS = {window_bounds_json};
  const KEYWORD_POSTS = {keyword_index_json};

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
    for (const p of matches) {{
      byHandle[p.handle] = (byHandle[p.handle] || 0) + 1;
      byPlatform[p.platform] = (byPlatform[p.platform] || 0) + 1;
      byPlatformHandle[p.platform] = byPlatformHandle[p.platform] || {{}};
      byPlatformHandle[p.platform][p.handle] = (byPlatformHandle[p.platform][p.handle] || 0) + 1;
    }}

    const mentionRows = Object.entries(byHandle).sort((a, b) => b[1] - a[1])
      .map(([h, c]) => `<tr><td>${{h}}</td><td class="num">${{c}}</td></tr>`).join('');

    const total = matches.length;
    const shareRows = Object.entries(byPlatform).sort((a, b) => b[1] - a[1])
      .map(([plat, c]) => `<tr><td>${{plat}}</td><td class="num">${{Math.round(c / total * 1000) / 10}}%</td><td class="num">${{c}}</td></tr>`).join('');

    const rankingBlocks = Object.entries(byPlatformHandle).map(([plat, handles]) => {{
      const rows = Object.entries(handles).sort((a, b) => b[1] - a[1])
        .map(([h, c]) => `<tr><td>${{h}}</td><td class="num">${{c}}</td></tr>`).join('');
      return `<div><h3>${{plat}}</h3><div class="table-wrap"><table><thead><tr><th>Account</th><th>Mentions</th></tr></thead><tbody>${{rows}}</tbody></table></div></div>`;
    }}).join('');

    container.innerHTML = `
      <p class="muted">${{total}} post(s) mention "${{kw}}" in this window.</p>
      <div class="col-2">
        <div>
          <h3>Competitor mentions (FR-03-04)</h3>
          <div class="table-wrap"><table><thead><tr><th>Account</th><th>Mentions</th></tr></thead><tbody>${{mentionRows}}</tbody></table></div>
        </div>
        <div>
          <h3>Platform share of voice (FR-03-05)</h3>
          <div class="table-wrap"><table><thead><tr><th>Platform</th><th>Share</th><th>Mentions</th></tr></thead><tbody>${{shareRows}}</tbody></table></div>
        </div>
      </div>
      <h3>Platform keyword ranking (FR-03-06)</h3>
      <div class="col-2">${{rankingBlocks}}</div>
    `;
  }}

  document.addEventListener('input', e => {{
    if (e.target.id === 'keyword-input') {{
      currentKeyword = e.target.value;
      renderKeywordResults(document.getElementById('range-select').value);
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
  }}

  document.getElementById('range-select').addEventListener('change', e => applyRange(e.target.value));
  applyRange(document.getElementById('range-select').value);
</script>
</body></html>"""

    with open(OUT_FILE, 'w', encoding='utf-8') as f:
        f.write(html)
    print(f"Wrote dashboard to {OUT_FILE}")


if __name__ == '__main__':
    main()
