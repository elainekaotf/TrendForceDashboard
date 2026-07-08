#!/usr/bin/env python3
"""
Local approval step for FR-05's "request tracking" flow: the dashboard's
Account Status tab has no backend to add an account and start crawling
on the spot (it's a static site), so it opens a GitHub issue instead.
Run this script locally to approve a request - it registers the handle
in accounts_config.json and (where possible) kicks off scraping for it.

Usage:
    python3 add_account.py X technews_tw2
    python3 add_account.py Facebook SomeCompetitorPage
    python3 add_account.py X technews_tw --own      (register/promote as an own account, not a competitor)

Marking an account 'own' changes real behavior elsewhere: FR-01/02's
competitor-gap analysis stops counting its posts as competitor activity,
and FR-05's reply-drafting queue starts drafting suggested replies for
ITS posts too (account_comment_management.py never touches anything but
own accounts). Only mark an account own if TrendForce actually operates it.

For Facebook, this can trigger a genuine one-off scrape immediately,
since TrendforceFacebookScraper's scrape_facebook.js takes a page URL as
a CLI argument. For X, TrendforceTwitterScraper's KNOWN_ACCOUNTS list is
hardcoded in scraper.js - there's no one-off CLI handle argument to hook
into, so this prints the exact edit needed there instead of guessing at
undocumented behavior.
"""
import json
import subprocess
import sys
from pathlib import Path

BASE = Path(__file__).resolve().parent
CONFIG_PATH = BASE / 'accounts_config.json'
FACEBOOK_SCRAPER_DIR = Path('/Users/elainekao/TrendforceFacebookScraper')
TWITTER_SCRAPER_DIR = Path('/Users/elainekao/TrendforceTwitterScraper')
# Mirrors cluster_topics.py's _DEFAULT_OWN - duplicated rather than imported
# so this script stays lightweight (no sklearn/pandas import chain) for a
# one-off local CLI action.
_DEFAULT_OWN = {'X': ['TrendForce'], 'Facebook': ['TrendForce.tw']}


def _default_own(platform):
    return _DEFAULT_OWN[platform]


def load_config():
    if CONFIG_PATH.exists():
        return json.loads(CONFIG_PATH.read_text(encoding='utf-8'))
    return {}


def save_config(cfg):
    CONFIG_PATH.write_text(json.dumps(cfg, indent=2, ensure_ascii=False) + '\n', encoding='utf-8')


def main():
    args = [a for a in sys.argv[1:] if a != '--own']
    as_own = '--own' in sys.argv
    if len(args) != 2 or args[0] not in ('X', 'Facebook'):
        print(__doc__)
        sys.exit(1)
    platform, handle = args

    cfg = load_config()
    cfg.setdefault(platform, {}).setdefault('own', list(_default_own(platform)))
    cfg.setdefault(platform, {}).setdefault('competitors', [])

    if as_own:
        if handle in cfg[platform]['competitors']:
            cfg[platform]['competitors'].remove(handle)
            print(f"Moved {handle} on {platform} from competitor to own.")
        if handle in cfg[platform]['own']:
            print(f"{handle} is already marked own on {platform}.")
        else:
            cfg[platform]['own'].append(handle)
            print(f"Marked {handle} own on {platform}.")
        save_config(cfg)
    elif handle in cfg[platform]['competitors']:
        print(f"{handle} is already tracked on {platform}.")
    else:
        cfg[platform]['competitors'].append(handle)
        save_config(cfg)
        print(f"Added {handle} to {platform} in {CONFIG_PATH.name}.")

    if platform == 'Facebook':
        if not FACEBOOK_SCRAPER_DIR.exists():
            print(f"[WARN] {FACEBOOK_SCRAPER_DIR} not found - skipping scrape trigger.")
        else:
            page_url = f'https://www.facebook.com/{handle}'
            print(f"Starting a one-off Facebook scrape for {page_url} ...")
            subprocess.run(['node', 'scrape_facebook.js', page_url, '400'], cwd=FACEBOOK_SCRAPER_DIR)
            # scrape_facebook.js only saves the scrolled-through raw HTML
            # (raw_facebook_<slug>.html) - parse_facebook.py is the step
            # that turns it into the dated CSV sync_data.sh looks for.
            print("Parsing the scraped HTML into a CSV ...")
            subprocess.run(['python3', 'parse_facebook.py', page_url], cwd=FACEBOOK_SCRAPER_DIR)
            print("Done. Run 'bash run_pipeline.sh core' in TrendForceDash to pick it up.")
    else:
        print(
            f"X/Twitter scraping is driven by a hardcoded KNOWN_ACCOUNTS list in "
            f"{TWITTER_SCRAPER_DIR / 'scraper.js'} - there's no one-off CLI hook for a "
            f"single new handle. To finish onboarding {handle}:\n"
            f"  1. Add '@{handle}' to KNOWN_ACCOUNTS in {TWITTER_SCRAPER_DIR / 'scraper.js'}\n"
            f"  2. Run the scraper's normal job (e.g. `npm run scrape`) in that repo\n"
            f"  3. Run `bash run_pipeline.sh core` here in TrendForceDash to pick up the new CSV"
        )


if __name__ == '__main__':
    main()
