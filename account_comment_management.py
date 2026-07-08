"""
FR-05 Account & Comment Management.

Compliance note (SRS Open Issue #2 - still TBC): platforms prohibit
inauthentic accounts and unauthorized automated interaction. This script
therefore only ever DRAFTS replies for our own official accounts
(TrendForce on X, TrendForce.tw on Facebook) and never sends anything -
a human must mark a draft 'approved' and, after posting it themselves
through the official account, mark it 'sent'. It never manages or
auto-replies through competitor or third-party accounts; those are
monitoring-only, matching FR-01/02/03's scope.

Processing:
  - Account status: platform, follower count (from follower_cache.json,
    where available), last-post recency, computed status (active/stale/
    inactive) for every account load_posts() tracks (own + competitors).
  - Comment aggregation: for OUR OWN posts only, flag ones with a reply
    count at or above NEEDS_RESPONSE_THRESHOLD as needing a response
    (actual comment/reply text isn't scraped today - only reply counts -
    so aggregation happens at the post level, not per individual comment).
  - Reply-draft suggestions: a template reply per flagged post, informed
    by that post's FR-01 topic and FR-03 sentiment, queued for human
    approval in analysis/reply_queue.json.
  - Response tracking: status lifecycle drafted -> approved -> sent, or
    dismissed, persisted across runs like FR-04's review queue.

Output: analysis/account_status.json, analysis/reply_queue.json

CLI:
  python3 account_comment_management.py build
  python3 account_comment_management.py list [--status drafted]
  python3 account_comment_management.py show <id>
  python3 account_comment_management.py approve <id> --reviewer <name>
  python3 account_comment_management.py sent <id> --reviewer <name>
  python3 account_comment_management.py dismiss <id> --reviewer <name> [--notes "..."]
"""
import argparse
import hashlib
import json
import os
from collections import defaultdict
from datetime import datetime, timezone

from cluster_topics import PLATFORM_ACCOUNTS, OWN_HANDLES, N_CLUSTERS, parse_count, cluster_posts, label_cluster
from nlp_sentiment import load_dashboard_posts

BASE = os.path.dirname(__file__)
STATUS_FILE = os.path.join(BASE, 'analysis', 'account_status.json')
REPLY_QUEUE_FILE = os.path.join(BASE, 'analysis', 'reply_queue.json')
FOLLOWER_CACHE_FILE = os.path.join(BASE, 'follower_cache.json')

NEEDS_RESPONSE_THRESHOLD = 5  # replies on a post before it's queued for a draft
STALE_AFTER_DAYS = 3
INACTIVE_AFTER_DAYS = 14

# Reply-count cutoff that adds a warm nod to how much a post has taken off,
# on top of the base tone - free/instant/offline, unlike an LLM-drafted
# version (traded off against that with elainekao: no API key/cost, just
# reuses data already computed by FR-01/03).
HIGH_ENGAGEMENT_REPLIES = 20


def draft_reply(sentiment, reply_count=0):
    buzzing = reply_count >= HIGH_ENGAGEMENT_REPLIES

    if sentiment == 'positive':
        base = "Thank you so much for the kind words, it truly means a lot to us!"
        base += (f" Seeing {reply_count} replies like this makes our day, we'll keep the great content coming."
                  if buzzing else " We're so glad this resonated with you, and we'll keep the great content coming.")
    elif sentiment == 'negative':
        base = "Thank you for taking the time to share your thoughts with us."
        base += " We really do appreciate the feedback, and we'd love to hear more so we can make things better."
        if buzzing:
            base += f" With {reply_count} people weighing in, we want to make sure everyone feels heard."
    else:
        base = "Thanks so much for reading and taking the time to comment!"
        base += (f" This post has sparked {reply_count} replies already, so please let us know what you'd love to see next."
                  if buzzing else " We'd love to hear what you'd like us to cover next.")
    return base


def now_iso():
    return datetime.now(timezone.utc).isoformat()


def record_id(*parts):
    return hashlib.sha1('|'.join(str(p) for p in parts).encode('utf-8')).hexdigest()[:16]


def load_follower_cache():
    if not os.path.exists(FOLLOWER_CACHE_FILE):
        return {}
    with open(FOLLOWER_CACHE_FILE, encoding='utf-8') as f:
        return json.load(f)


def parse_ts(ts):
    if not ts:
        return None
    try:
        return datetime.fromisoformat(ts.replace('Z', '+00:00'))
    except ValueError:
        return None


def build_account_status(posts, now):
    follower_cache = load_follower_cache()
    by_handle = defaultdict(list)
    for p in posts:
        by_handle[p['handle']].append(p)

    accounts = []
    for platform, cfg in PLATFORM_ACCOUNTS.items():
        for handle in cfg['own'] + cfg['competitors']:
            handle_posts = by_handle.get(handle, [])
            timestamps = [parse_ts(p['timestamp']) for p in handle_posts]
            timestamps = [t for t in timestamps if t]
            last_post_at = max(timestamps) if timestamps else None
            days_since = (now - last_post_at).days if last_post_at else None

            if days_since is None:
                status = 'no_data'
            elif days_since <= STALE_AFTER_DAYS:
                status = 'active'
            elif days_since <= INACTIVE_AFTER_DAYS:
                status = 'stale'
            else:
                status = 'inactive'

            follower_raw = follower_cache.get(handle, {}).get('followers')
            accounts.append({
                'handle': handle,
                'platform': platform,
                'is_own': handle in OWN_HANDLES,
                'follower_count': parse_count(follower_raw) if follower_raw else None,
                'post_count': len(handle_posts),
                'last_post_at': last_post_at.isoformat() if last_post_at else None,
                'days_since_last_post': days_since,
                'status': status,
            })
    return accounts


def build_comment_queue(posts, topic_labels_by_cluster, cluster_id_by_post):
    """Flag our own high-reply posts and draft a suggested response for each."""
    flagged = []
    for p, cid in zip(posts, cluster_id_by_post):
        if p['handle'] not in OWN_HANDLES:
            continue
        replies = p.get('replies', 0)
        if replies < NEEDS_RESPONSE_THRESHOLD:
            continue
        rid = record_id('reply', p['platform'], p['handle'], p['timestamp'], p['text'][:80])
        text_excerpt = p['text'][:200]
        topic_label = topic_labels_by_cluster[cid]
        sentiment_score = p.get('sentiment_score', 0.0)
        flagged.append({
            'id': rid,
            'platform': p['platform'],
            'handle': p['handle'],
            'timestamp': p['timestamp'],
            'url': p.get('url', ''),
            'text_excerpt': text_excerpt,
            'reply_count': replies,
            'topic_label': topic_label,
            'sentiment': p['sentiment'],
            'sentiment_score': sentiment_score,
            'draft_reply': draft_reply(p['sentiment'], replies),
        })
    flagged.sort(key=lambda r: r['reply_count'], reverse=True)
    return flagged


def load_reply_queue():
    if not os.path.exists(REPLY_QUEUE_FILE):
        return {}
    with open(REPLY_QUEUE_FILE, encoding='utf-8') as f:
        return json.load(f)


def save_reply_queue(queue):
    os.makedirs(os.path.dirname(REPLY_QUEUE_FILE), exist_ok=True)
    with open(REPLY_QUEUE_FILE, 'w', encoding='utf-8') as f:
        json.dump(queue, f, ensure_ascii=False, indent=2)


def build():
    posts = load_dashboard_posts()
    if not posts:
        print("No posts available, skipping build.")
        return

    timestamps = [parse_ts(p['timestamp']) for p in posts]
    timestamps = [t for t in timestamps if t]
    now = max(timestamps) if timestamps else datetime.now(timezone.utc)

    accounts = build_account_status(posts, now)
    os.makedirs(os.path.dirname(STATUS_FILE), exist_ok=True)
    with open(STATUS_FILE, 'w', encoding='utf-8') as f:
        json.dump({'generated_at': now.isoformat(), 'accounts': accounts}, f, ensure_ascii=False, indent=2)

    vectorizer, X, km, labels = cluster_posts(posts, N_CLUSTERS)
    topic_labels = {int(cid): ' / '.join(label_cluster(vectorizer, km.cluster_centers_[cid])) or f'cluster-{cid}'
                    for cid in set(labels)}
    flagged = build_comment_queue(posts, topic_labels, [int(l) for l in labels])

    queue = load_reply_queue()
    added, refreshed = 0, 0
    for rec in flagged:
        rid = rec['id']
        if rid in queue:
            queue[rid]['reply_count'] = rec['reply_count']
            queue[rid]['url'] = queue[rid].get('url') or rec['url']
            queue[rid]['topic_label'] = rec['topic_label']  # re-clustering (e.g. a noise-filter fix) should refresh stale labels
            queue[rid]['sentiment_score'] = rec['sentiment_score']
            # Regenerate the draft while it's still just a suggestion nobody
            # has acted on yet (e.g. picking up a template wording change) -
            # but never touch it once a human has approved or sent it, since
            # that's the actual record of what was reviewed/posted.
            if queue[rid]['status'] == 'drafted':
                queue[rid]['draft_reply'] = rec['draft_reply']
            refreshed += 1
        else:
            rec['status'] = 'drafted'
            rec['reviewer'] = None
            rec['reviewed_at'] = None
            rec['notes'] = None
            queue[rid] = rec
            added += 1
    save_reply_queue(queue)

    print(f"Wrote account status for {len(accounts)} accounts to {STATUS_FILE}")
    print(f"Reply queue: {added} new, {refreshed} refreshed, {len(queue)} total (own-account posts needing a response draft).")


def list_records(status=None, limit=20):
    queue = load_reply_queue()
    records = list(queue.values())
    if status:
        records = [r for r in records if r['status'] == status]
    records.sort(key=lambda r: r['reply_count'], reverse=True)
    for r in records[:limit]:
        print(f"{r['id']}  [{r['status']:9}] {r['platform']:8} {r['handle']:14} replies={r['reply_count']:<5} {r['topic_label']}")
    print(f"({min(len(records), limit)} of {len(records)} shown)")


def show_record(rid):
    queue = load_reply_queue()
    rec = queue.get(rid)
    print(json.dumps(rec, ensure_ascii=False, indent=2) if rec else f"No record {rid}")


def transition(rid, status, reviewer, notes=None, allowed_from=None):
    queue = load_reply_queue()
    rec = queue.get(rid)
    if not rec:
        print(f"No record {rid}")
        return
    if allowed_from and rec['status'] not in allowed_from:
        print(f"Cannot move {rid} from '{rec['status']}' to '{status}' (expected one of {allowed_from}).")
        return
    rec['status'] = status
    rec['reviewer'] = reviewer
    rec['reviewed_at'] = now_iso()
    if notes is not None:
        rec['notes'] = notes
    save_reply_queue(queue)
    print(f"{rid}: {status}")


def main():
    parser = argparse.ArgumentParser(description='FR-05 Account & Comment Management')
    sub = parser.add_subparsers(dest='command', required=True)

    sub.add_parser('build')

    p_list = sub.add_parser('list')
    p_list.add_argument('--status', choices=['drafted', 'approved', 'sent', 'dismissed'])
    p_list.add_argument('--limit', type=int, default=20)

    p_show = sub.add_parser('show')
    p_show.add_argument('record_id')

    p_approve = sub.add_parser('approve')
    p_approve.add_argument('record_id')
    p_approve.add_argument('--reviewer', required=True)

    p_sent = sub.add_parser('sent')
    p_sent.add_argument('record_id')
    p_sent.add_argument('--reviewer', required=True)

    p_dismiss = sub.add_parser('dismiss')
    p_dismiss.add_argument('record_id')
    p_dismiss.add_argument('--reviewer', required=True)
    p_dismiss.add_argument('--notes')

    args = parser.parse_args()
    if args.command == 'build':
        build()
    elif args.command == 'list':
        list_records(args.status, args.limit)
    elif args.command == 'show':
        show_record(args.record_id)
    elif args.command == 'approve':
        transition(args.record_id, 'approved', args.reviewer, allowed_from={'drafted'})
    elif args.command == 'sent':
        # Human has posted the reply themselves through the official account;
        # this script never calls any posting API.
        transition(args.record_id, 'sent', args.reviewer, allowed_from={'approved'})
    elif args.command == 'dismiss':
        transition(args.record_id, 'dismissed', args.reviewer, args.notes, allowed_from={'drafted', 'approved'})


if __name__ == '__main__':
    main()
