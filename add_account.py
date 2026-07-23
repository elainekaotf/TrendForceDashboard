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
    python3 add_account.py LinkedIn some-company-slug
    python3 add_account.py LinkedIn https://www.linkedin.com/company/some-company/
    python3 add_account.py LinkedIn https://www.linkedin.com/in/some-person/    (personal profile)
    python3 add_account.py X technews_tw --own      (register/promote as an own account, not a competitor)

Marking an account 'own' changes real behavior elsewhere: FR-01/02's
competitor-gap analysis stops counting its posts as competitor activity,
and FR-05's reply-drafting queue starts drafting suggested replies for
ITS posts too (account_comment_management.py never touches anything but
own accounts). Only mark an account own if TrendForce actually operates it.

This is the entire "accept a request" flow in one command: it registers
the handle, triggers a real one-off scrape (Facebook via
scrape_facebook.js + parse_facebook.py; X via scrape_accounts.js, which
writes csv/<handle>.csv directly; LinkedIn company pages via
scrape_accounts_linkedin.js - see normalize_linkedin_slug; LinkedIn
personal profiles via the separate scrape_profiles_linkedin.js - see
is_linkedin_profile_url/normalize_linkedin_profile_slug, added
2026-07-23 after two profile requests sat rejected since the original
LinkedIn support only covered company pages), then runs
`run_pipeline.sh core` to sync, rebuild every analysis file, regenerate
the dashboard, and publish it to GitHub Pages - so accepting a request
never needs a second command.

X's KNOWN_ACCOUNTS list in scraper.js still needs the handle added by
hand for it to stay covered by *future* scheduled runs (there's no CLI
hook for that file) - this script prints a reminder but can't do that
part for you. LinkedIn's own ACCOUNTS/PROFILES lists in
scrape_accounts_linkedin.js/scrape_profiles_linkedin.js ARE updated
automatically (small, simple-enough arrays to edit safely).
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
LINKEDIN_SCRAPER_DIR = Path('/Users/elainekao/TrendforceLinkedinScraper')
LINKEDIN_ACCOUNTS_JS = LINKEDIN_SCRAPER_DIR / 'scrape_accounts_linkedin.js'
LINKEDIN_PROFILES_JS = LINKEDIN_SCRAPER_DIR / 'scrape_profiles_linkedin.js'
# Mirrors cluster_topics.py's _DEFAULT_OWN - duplicated rather than imported
# so this script stays lightweight (no sklearn/pandas import chain) for a
# one-off local CLI action.
_DEFAULT_OWN = {'X': ['TrendForce'], 'Facebook': ['TrendForce.tw'], 'LinkedIn': ['TrendForce']}

# A brand-new account has no history yet, so its first scrape goes deeper
# than the daily top-up (X: 15 scrolls, Facebook light: 20, LinkedIn: 15)
# but well short of a full backfill (Facebook: 400, which took ~1.5h
# onboarding technewsinside; LinkedIn: 200, which pulled 800+ posts in
# testing) - deep enough to be useful, fast enough not to block on it.
ONBOARDING_SCROLLS = {'X': 75, 'Facebook': 250, 'LinkedIn': 100}


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


class UnsupportedLinkedInAccount(Exception):
    pass


def normalize_linkedin_slug(raw):
    """LinkedIn is COMPANY PAGES ONLY - scrape_accounts_linkedin.js has no
    personal-profile scraping capability at all (found 2026-07-23, when a
    /in/ profile URL was requested: different page structure entirely from
    a company page, and a materially more sensitive thing to scrape than a
    public company brand account). Reject a /in/ URL outright with a clear
    reason rather than mis-parsing it into a bogus "slug".

    Accepts either a bare slug or a full /company/<slug>/ URL. The slug
    itself becomes both the accounts_config.json handle AND the CSV
    filename for newly added accounts (see main()) - no separate
    display-name mapping needed, unlike the legacy 'TrendForce' entry."""
    h = raw.strip()
    if re.search(r'linkedin\.com/in/', h, re.IGNORECASE):
        raise UnsupportedLinkedInAccount(
            f"{raw!r} looks like a personal profile URL (/in/...), not a company page (/company/...). "
            f"scrape_accounts_linkedin.js only supports company pages - personal-profile scraping isn't "
            f"built and needs its own separate work, not just a config change."
        )
    h = re.sub(r'^https?://(www\.)?linkedin\.com/company/', '', h, flags=re.IGNORECASE)
    h = h.rstrip('/')
    h = re.split(r'[/?#]', h)[0]
    return h


def add_linkedin_account_to_scraper(handle, slug):
    """Appends a new {handle, slug} entry to scrape_accounts_linkedin.js's
    ACCOUNTS array - unlike X's KNOWN_ACCOUNTS (large, hand-curated, no
    safe automatic insertion point), this array is small and has one
    unambiguous insertion point (right before its closing bracket), so
    editing it programmatically is safe rather than needing a by-hand
    reminder."""
    if not LINKEDIN_ACCOUNTS_JS.exists():
        print(f"[WARN] {LINKEDIN_ACCOUNTS_JS} not found - add the account there by hand.")
        return False
    text = LINKEDIN_ACCOUNTS_JS.read_text(encoding='utf-8')
    if f"slug: '{slug}'" in text:
        print(f"{slug} is already in {LINKEDIN_ACCOUNTS_JS.name}'s ACCOUNTS list.")
        return True
    new_entry = f"  {{ handle: '{handle}', slug: '{slug}' }},\n"
    updated = re.sub(r'(const ACCOUNTS = \[\n)', r'\1' + new_entry, text, count=1)
    if updated == text:
        print(f"[WARN] Couldn't find ACCOUNTS array in {LINKEDIN_ACCOUNTS_JS} - add the account there by hand.")
        return False
    LINKEDIN_ACCOUNTS_JS.write_text(updated, encoding='utf-8')
    print(f"Added {{ handle: '{handle}', slug: '{slug}' }} to {LINKEDIN_ACCOUNTS_JS.name}'s ACCOUNTS list.")
    return True


def load_config():
    if CONFIG_PATH.exists():
        return json.loads(CONFIG_PATH.read_text(encoding='utf-8'))
    return {}


def save_config(cfg):
    CONFIG_PATH.write_text(json.dumps(cfg, indent=2, ensure_ascii=False) + '\n', encoding='utf-8')


def main():
    args = [a for a in sys.argv[1:] if a != '--own']
    as_own = '--own' in sys.argv
    if len(args) != 2 or args[0] not in ('X', 'Facebook', 'LinkedIn'):
        print(__doc__)
        sys.exit(1)
    platform, handle = args
    if platform == 'LinkedIn':
        try:
            handle = normalize_linkedin_slug(handle)
        except UnsupportedLinkedInAccount as e:
            print(f"[REJECTED] {e}")
            sys.exit(1)
    else:
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
    elif platform == 'LinkedIn':
        if not LINKEDIN_SCRAPER_DIR.exists():
            print(f"[WARN] {LINKEDIN_SCRAPER_DIR} not found - skipping scrape trigger.")
        else:
            add_linkedin_account_to_scraper(handle, handle)
            scrolls = ONBOARDING_SCROLLS['LinkedIn']
            print(f"Starting a one-off LinkedIn scrape for {handle} ({scrolls} scrolls) ...")
            result = subprocess.run(
                ['node', 'scrape_accounts_linkedin.js', handle, str(scrolls)], cwd=LINKEDIN_SCRAPER_DIR,
            )
            scraped = result.returncode == 0
            if not scraped:
                print(f"[WARN] LinkedIn scrape exited with code {result.returncode} - check for a manual-login prompt or a bad slug.")
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
