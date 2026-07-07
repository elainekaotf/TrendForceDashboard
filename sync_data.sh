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

set -u
cd "$(dirname "$0")"

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
  # The Facebook scraper writes dated files like facebook_<handle>_<date>.csv - take the newest.
  latest=$(ls -t "$FACEBOOK_SRC"/csv/facebook_"$h"_*.csv 2>/dev/null | head -1)
  if [ -n "$latest" ]; then
    cp "$latest" "csv/facebook/$h.csv"
    synced=$((synced + 1))
  else
    echo "[WARN] sync_data: no dated CSV found for Facebook handle $h in $FACEBOOK_SRC/csv"
    missing=$((missing + 1))
  fi
done

echo "sync_data: synced $synced account file(s), $missing missing"
[ "$missing" -eq 0 ]
