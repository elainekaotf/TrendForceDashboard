#!/bin/bash
# Scheduler entry point for TrendForceDash. Runs a named job (a group of
# FR scripts in dependency order) and reports failures without stopping
# the rest of the job. See crontab.txt for the recommended schedule and
# SRS Section 6 (Scheduling Requirements) for the source frequencies.
#
# Usage: bash run_pipeline.sh <scan|core|accounts|daily>
#
# Jobs:
#   scan     (every 4h)   FR-02 (scan tier only) -> FR-04
#            Implements the SRS's own proposed resolution to Open Issue #1
#            ("detect every 4 hours" vs "run every 6 hours"): a two-tier
#            schedule. This tier is FR-02-01 only (top rising topics, no
#            KOL/sub-topic drill-down) for the 4h range - fast enough to
#            run every 4h without duplicating 'core's full 6h recompute.
#   core     (every 6h)  FR-01 -> FR-02 (full) -> FR-03 -> FR-04
#            Topic clustering feeds FR-02/03/06; FR-04 refreshes right
#            after so newly-produced topic/sentiment/KOL calls are
#            queued for review promptly. FR-02 here is the full
#            FR-02-01..04 chain across all 5 ranges - the other tier of
#            the two-tier schedule above.
#   accounts (every 8h)  FR-05
#            Independent of the core chain - only touches account
#            status + own-account reply drafts.
#   daily    (once/day)  FR-06 -> FR-04
#            generate_summaries.py runs FR-03 itself in 'daily' mode
#            internally, so this doesn't need to call nlp_sentiment.py
#            separately. FR-04 refreshes again to queue the day's
#            summaries for review.
#
# Every job starts with sync_data.sh (pulls fresh CSVs from the sibling
# scraper repos - see that script's header for why this is needed at all)
# and ends by regenerating docs/index.html (generate_dashboard.py) so the
# dashboard reflects whatever that job just produced.
#
# FR-07 (self-service upload) is user-triggered, not scheduled - it isn't
# part of any job here.

set -u
export PATH="/usr/local/bin:/usr/bin:/bin:/Library/Frameworks/Python.framework/Versions/3.10/bin:$PATH"
cd "$(dirname "$0")"

JOB="${1:-}"
if [[ -z "$JOB" ]]; then
  echo "Usage: $0 <scan|core|accounts|daily>" >&2
  exit 1
fi

echo "[$(date '+%Y-%m-%d %H:%M:%S')] Starting '$JOB' pipeline run..."

FAILURES=()
run_step() {
  local label="$1"; shift
  if ! python3 "$@"; then
    echo "[WARN] $label failed"
    FAILURES+=("$label")
  fi
}

if ! bash sync_data.sh; then
  echo "[WARN] sync_data had missing sources - continuing with whatever csv/ already has"
  FAILURES+=("sync_data")
fi

case "$JOB" in
  scan)
    run_step "fuzzy_trend_scan" fuzzy_trend.py scan
    run_step "manual_review"    manual_review.py build
    ;;
  core)
    run_step "cluster_topics"  cluster_topics.py
    run_step "fuzzy_trend"     fuzzy_trend.py full
    run_step "nlp_sentiment"   nlp_sentiment.py
    run_step "manual_review"   manual_review.py build
    ;;
  accounts)
    run_step "account_comment_management" account_comment_management.py build
    ;;
  daily)
    run_step "generate_summaries" generate_summaries.py
    run_step "manual_review"      manual_review.py build
    ;;
  *)
    echo "Unknown job '$JOB'. Expected scan|core|accounts|daily." >&2
    exit 1
    ;;
esac

# Regenerate the dashboard (docs/index.html) after every job so it always
# reflects whichever analysis files that job just refreshed.
run_step "generate_dashboard" generate_dashboard.py

if [ ${#FAILURES[@]} -gt 0 ]; then
  JOINED=$(IFS=', '; echo "${FAILURES[*]}")
  bash alert.sh "TrendForceDash: $JOB run — issues" "Steps that failed: ${JOINED}. Check pipeline.log."
  echo "[$(date '+%Y-%m-%d %H:%M:%S')] '$JOB' pipeline run finished with failures: ${JOINED}"
  exit 1
fi

echo "[$(date '+%Y-%m-%d %H:%M:%S')] '$JOB' pipeline run complete."
