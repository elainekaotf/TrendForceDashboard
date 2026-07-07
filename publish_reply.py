#!/usr/bin/env python3
"""
FR-05 confirm-before-send helper: takes an *approved* reply_queue.json
record and opens it for a human to actually send - it never posts
anything itself.

Why not automate the send too? The sibling scraper repos
(TrendforceTwitterScraper, TrendforceFacebookScraper) authenticate via
Playwright browser sessions with saved login cookies, not an official
API with write scope - reusing that for posting would mean scripting
clicks against a bot session in a real account, which is both fragile
(breaks on any DOM/UI change) and against both platforms' automation
terms. Instead:

  - X: opens x.com's own reply-intent URL in your actual default
    browser, pre-filled with the draft text and pointed at the right
    tweet. You review it and click "Reply" yourself - your own logged-in
    session does the posting, not a script.
  - Facebook: has no equivalent public intent URL for comments, so this
    opens the post in your browser and copies the draft text to your
    clipboard - paste it into the comment box and post it yourself.

After you've actually sent it, mark it done:
    python3 account_comment_management.py sent <id> --reviewer <name>

Usage:
    python3 publish_reply.py <record_id>
"""
import json
import os
import re
import sys
import webbrowser
from urllib.parse import quote

BASE = os.path.dirname(__file__)
REPLY_QUEUE_FILE = os.path.join(BASE, 'analysis', 'reply_queue.json')

TWEET_ID_RE = re.compile(r'/status(?:es)?/(\d+)')


def load_queue():
    with open(REPLY_QUEUE_FILE, encoding='utf-8') as f:
        return json.load(f)


def copy_to_clipboard(text):
    """Best-effort clipboard copy without requiring an extra dependency -
    falls back to just printing the text if no OS clipboard tool exists."""
    for cmd in (['pbcopy'], ['xclip', '-selection', 'clipboard'], ['clip']):
        try:
            import subprocess
            p = subprocess.Popen(cmd, stdin=subprocess.PIPE)
            p.communicate(text.encode('utf-8'))
            if p.returncode == 0:
                return True
        except FileNotFoundError:
            continue
    return False


def main():
    if len(sys.argv) != 2:
        print(__doc__)
        sys.exit(1)
    rid = sys.argv[1]

    queue = load_queue()
    rec = queue.get(rid)
    if not rec:
        print(f"No record {rid} in {REPLY_QUEUE_FILE}")
        sys.exit(1)
    if rec['status'] != 'approved':
        print(f"Refusing to publish {rid}: status is '{rec['status']}', not 'approved'.\n"
              f"Approve it first: python3 account_comment_management.py approve {rid} --reviewer <name>")
        sys.exit(1)
    if not rec.get('url'):
        print(f"{rid} has no source post URL recorded - nothing to open.")
        sys.exit(1)

    draft = rec['draft_reply']
    url = rec['url']

    if rec['platform'] == 'X':
        m = TWEET_ID_RE.search(url)
        if not m:
            print(f"Could not find a tweet ID in {url} - opening the post itself instead.")
            webbrowser.open(url)
        else:
            intent_url = f"https://twitter.com/intent/tweet?in_reply_to={m.group(1)}&text={quote(draft)}"
            print(f"Opening reply composer for {rid} (pre-filled - review, then click Reply yourself):\n{intent_url}")
            webbrowser.open(intent_url)
    else:
        copied = copy_to_clipboard(draft)
        print(f"Opening the post for {rid}:\n{url}")
        webbrowser.open(url)
        if copied:
            print("Draft reply copied to your clipboard - paste it into the comment box and post it yourself:")
        else:
            print("Could not access the system clipboard - copy this draft reply into the comment box yourself:")
        print(f"\n    {draft}\n")

    print(f"Once you've actually sent it: python3 account_comment_management.py sent {rid} --reviewer <name>")


if __name__ == '__main__':
    main()
