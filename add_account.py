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


def load_config():
    if CONFIG_PATH.exists():
        return json.loads(CONFIG_PATH.read_text(encoding='utf-8'))
    return {}


def save_config(cfg):
    CONFIG_PATH.write_text(json.dumps(cfg, indent=2, ensure_ascii=False) + '\n', encoding='utf-8')


def main():
    if len(sys.argv) != 3 or sys.argv[1] not in ('X', 'Facebook'):
        print(__doc__)
        sys.exit(1)
    platform, handle = sys.argv[1], sys.argv[2]

    cfg = load_config()
    cfg.setdefault(platform, {}).setdefault('competitors', [])
    if handle in cfg[platform]['competitors']:
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
            print("Facebook scrape finished. Run 'bash run_pipeline.sh core' in TrendForceDash to pick it up.")
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
