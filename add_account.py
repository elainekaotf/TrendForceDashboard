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

This is the entire "accept a request" flow in one command: it registers
the handle, triggers a real one-off scrape (Facebook via
scrape_facebook.js + parse_facebook.py; X via scrape_accounts.js, which
writes csv/<handle>.csv directly), then runs `run_pipeline.sh core` to
sync, rebuild every analysis file, regenerate the dashboard, and publish
it to GitHub Pages - so accepting a request never needs a second command.

X's KNOWN_ACCOUNTS list in scraper.js still needs the handle added by
hand for it to stay covered by *future* scheduled runs (there's no CLI
hook for that file) - this script prints a reminder but can't do that
part for you.
"""
import json
import os
import re
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

# A brand-new account has no history yet, so its first scrape goes deeper
# than the daily top-up (X: 15 scrolls, Facebook light: 20) but well short
# of a full backfill (Facebook: 400, which took ~1.5h onboarding
# technewsinside) - deep enough to be useful, fast enough not to block on it.
ONBOARDING_SCROLLS = {'X': 75, 'Facebook': 250}


def _default_own(platform):
    return _DEFAULT_OWN[platform]


def normalize_handle(raw):
    """Accepts a bare handle or a pasted profile URL - the dashboard's
    request form normalizes client-side too, but a request can still reach
    here typed straight from a GitHub issue (one came through as
    "https://x.com/tphuang" before the form-side fix), so normalize here
    too rather than trusting the caller."""
    h = raw.strip()
    h = re.sub(r'^https?://(www\.)?(x\.com|twitter\.com|facebook\.com)/', '', h, flags=re.IGNORECASE)
    h = h.lstrip('@').rstrip('/')
    h = re.split(r'[/?#]', h)[0]
    return h


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
    handle = normalize_handle(handle)

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

    scraped = False
    if platform == 'Facebook':
        if not FACEBOOK_SCRAPER_DIR.exists():
            print(f"[WARN] {FACEBOOK_SCRAPER_DIR} not found - skipping scrape trigger.")
        else:
            page_url = f'https://www.facebook.com/{handle}'
            scrolls = ONBOARDING_SCROLLS['Facebook']
            print(f"Starting a one-off Facebook scrape for {page_url} ({scrolls} scrolls) ...")
            subprocess.run(['node', 'scrape_facebook.js', page_url, str(scrolls)], cwd=FACEBOOK_SCRAPER_DIR)
            # scrape_facebook.js only saves the scrolled-through raw HTML
            # (raw_facebook_<slug>.html) - parse_facebook.py is the step
            # that turns it into the dated CSV sync_data.sh looks for.
            print("Parsing the scraped HTML into a CSV ...")
            subprocess.run(['python3', 'parse_facebook.py', page_url], cwd=FACEBOOK_SCRAPER_DIR)
            scraped = True
    else:
        if not TWITTER_SCRAPER_DIR.exists():
            print(f"[WARN] {TWITTER_SCRAPER_DIR} not found - skipping scrape trigger.")
        else:
            scrolls = ONBOARDING_SCROLLS['X']
            print(f"Starting a one-off X scrape for @{handle} ({scrolls} scrolls) ...")
            env = {**os.environ, 'MAX_SCROLLS': str(scrolls)}
            subprocess.run(['node', 'scrape_accounts.js', f'@{handle}'], cwd=TWITTER_SCRAPER_DIR, env=env)
            scraped = True
        print(
            f"[REMINDER] For @{handle} to stay covered by *future* scheduled scrapes, also add "
            f"it to KNOWN_ACCOUNTS in {TWITTER_SCRAPER_DIR / 'scraper.js'} by hand - "
            f"there's no CLI hook for that file."
        )

    if not scraped:
        print("Skipping the pipeline run since no scrape ran - nothing new to pick up.")
        return

    # 'core' rebuilds Topic Gaps/Rising Trends/Sentiment; 'accounts' rebuilds
    # the Account Status tab (FR-05) separately, on its own 8h schedule -
    # a new account showed up in the former but not the latter the first
    # time this ran, since only 'core' was being chained here.
    ok = True
    for job in ('core', 'accounts'):
        print(f"Running the TrendForceDash '{job}' pipeline job ...")
        result = subprocess.run(['bash', 'run_pipeline.sh', job], cwd=BASE)
        if result.returncode != 0:
            print(f"[WARN] run_pipeline.sh {job} exited with code {result.returncode} - check pipeline.log.")
            ok = False
    if ok:
        print(f"Done. {handle} is live on the dashboard.")


if __name__ == '__main__':
    main()
