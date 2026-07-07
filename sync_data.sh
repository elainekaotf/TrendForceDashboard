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

X_HANDLES=(TrendForce dylan522p SemiAnalysis_ jukan05 QQ_Timmy technews_tw)
FB_HANDLES=(TrendForce.tw ctee.fans yutinghaosfinance)

synced=0
missing=0

for h in "${X_HANDLES[@]}"; do
  src="$TWITTER_SRC/csv/$h.csv"
  if [ -f "$src" ]; then
    cp "$src" "csv/$h.csv"
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
    : > "$out"
    first=1
    for f in $dated_files; do
      if [ "$first" -eq 1 ]; then
        cat "$f" > "$out"
        first=0
      else
        tail -n +2 "$f" >> "$out"
      fi
    done
    synced=$((synced + 1))
  else
    echo "[WARN] sync_data: no dated CSV found for Facebook handle $h in $FACEBOOK_SRC/csv"
    missing=$((missing + 1))
  fi
done

echo "sync_data: synced $synced account file(s), $missing missing"
[ "$missing" -eq 0 ]
