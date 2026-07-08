#!/bin/bash
# Copies the freshest scraped CSVs from the sibling scraper repos into
# TrendForceDash's own csv/ folder. TrendForceDash keeps its own copy on
# purpose (see cluster_topics.py's docstring) so this repo stays portable
# and self-contained on GitHub, rather than depending on
# TrendforceTwitterScraper/TrendforceFacebookScraper existing at fixed
# paths - but that means nothing here scrapes anything, so without this
# sync step the copy just goes stale forever.
#
# Run before analysis, not after: cluster_topics.py etc. only see whatever
# is already in csv/ when they start.
#
# Locking: run_pipeline.sh calls this at the start of every job, and the
# cron schedule has jobs that land on the same minute (scan every 4h and
# accounts every 8h both fire at 0/8/16h) - two concurrent instances both
# truncating and writing the same csv/facebook/<handle>.csv raced and
# corrupted it (a UTF-8 multi-byte sequence split mid-character) the first
# time this actually happened. mkdir is atomic on POSIX filesystems, so use
# a lock directory as a mutex; a second instance waits rather than racing.

set -u
cd "$(dirname "$0")"

LOCKDIR=".sync_data.lock"
waited=0
while ! mkdir "$LOCKDIR" 2>/dev/null; do
  if [ "$waited" -ge 60 ]; then
    echo "[WARN] sync_data: lock held for 60s - assuming it's stale (a crashed run left it behind) and taking over"
    rmdir "$LOCKDIR" 2>/dev/null
    mkdir "$LOCKDIR" 2>/dev/null
    break
  fi
  sleep 1
  waited=$((waited + 1))
done
trap 'rmdir "$LOCKDIR" 2>/dev/null' EXIT

TWITTER_SRC=/Users/elainekao/TrendforceTwitterScraper
FACEBOOK_SRC=/Users/elainekao/TrendforceFacebookScraper

# Pulled from cluster_topics.PLATFORM_ACCOUNTS (own + competitors merged,
# accounts_config.json included) rather than hardcoded here - a hardcoded
# second copy of the account list is exactly how tphuang/technewsinside
# ended up registered via add_account.py but never actually synced into
# this repo's own csv/: this file's list just didn't know they existed.
X_HANDLES=($(python3 -c "from cluster_topics import PLATFORM_ACCOUNTS as P; print(' '.join(P['X']['own'] + P['X']['competitors']))"))
FB_HANDLES=($(python3 -c "from cluster_topics import PLATFORM_ACCOUNTS as P; print(' '.join(P['Facebook']['own'] + P['Facebook']['competitors']))"))

synced=0
missing=0

for h in "${X_HANDLES[@]}"; do
  src="$TWITTER_SRC/csv/$h.csv"
  if [ -f "$src" ]; then
    # cp truncates-then-writes the destination, same non-atomic hazard as
    # the Facebook concatenation below - copy to a temp path and rename
    # into place instead.
    tmp="csv/$h.csv.tmp.$$"
    cp "$src" "$tmp"
    mv "$tmp" "csv/$h.csv"
    synced=$((synced + 1))
  else
    echo "[WARN] sync_data: missing $src"
    missing=$((missing + 1))
  fi
done

mkdir -p csv/facebook
for h in "${FB_HANDLES[@]}"; do
  # The Facebook scraper writes one dated file per day it ran
  # (facebook_<handle>_<date>.csv), each holding only that day's *new*
  # posts (parse_facebook.py dedups against all prior dated files before
  # writing). Copying only the newest file (as this used to do) silently
  # dropped every earlier day's history the moment a new day's file
  # appeared - concatenate all of them instead, keeping one header row.
  dated_files=$(ls -tr "$FACEBOOK_SRC"/csv/facebook_"$h"_*.csv 2>/dev/null)
  if [ -n "$dated_files" ]; then
    out="csv/facebook/$h.csv"
    # Merge via merge_facebook_csv.py (schema-aware: each dated file is read
    # under its OWN header, not assumed to match the first file's - a plain
    # `cat`/`tail -n +2` broke the day the scraper added a
    # reactionsBreakdown column, silently shifting every field in the newer
    # rows and leaving `text` empty), building into a temp path and
    # `mv`-ing it into place atomically for the same reason as the X copy
    # below: a concurrent reader (a *different* job's cluster_topics.py)
    # must never see a partially-written file.
    tmp="$out.tmp.$$"
    python3 merge_facebook_csv.py "$tmp" $dated_files
    mv "$tmp" "$out"
    synced=$((synced + 1))
  else
    echo "[WARN] sync_data: no dated CSV found for Facebook handle $h in $FACEBOOK_SRC/csv"
    missing=$((missing + 1))
  fi
done

echo "sync_data: synced $synced account file(s), $missing missing"
[ "$missing" -eq 0 ]
