#!/bin/bash
# Scheduler entry point for TrendForceDash. Runs a named job (a group of
# FR scripts in dependency order) and reports failures without stopping
# the rest of the job. See crontab.txt for the recommended schedule and
# SRS Section 6 (Scheduling Requirements) for the source frequencies.
#
# Usage: bash run_pipeline.sh <core|accounts|daily>
#
# Jobs:
#   core     (every 6h)  FR-01 -> FR-02 -> FR-03 -> FR-04
#            Topic clustering feeds FR-02/03/06; FR-04 refreshes right
#            after so newly-produced topic/sentiment/KOL calls are
#            queued for review promptly.
#   accounts (every 8h)  FR-05
#            Independent of the core chain - only touches account
#            status + own-account reply drafts.
#   daily    (once/day)  FR-06 -> FR-04
#            generate_summaries.py runs FR-03 itself in 'daily' mode
#            internally, so this doesn't need to call nlp_sentiment.py
#            separately. FR-04 refreshes again to queue the day's
#            summaries for review.
#
# FR-07 (self-service upload) is user-triggered, not scheduled - it isn't
# part of any job here.
#
# FR-02's frequency is still an open question in the SRS (Open Issue #1:
# "detect every 4 hours" vs "run every 6 hours"). This runs it inside the
# 6-hour 'core' job pending that decision - see docs/fr02-frequency.md if
# a two-tier schedule gets confirmed instead.

set -u
export PATH="/usr/local/bin:/usr/bin:/bin:/Library/Frameworks/Python.framework/Versions/3.10/bin:$PATH"
cd "$(dirname "$0")"

JOB="${1:-}"
if [[ -z "$JOB" ]]; then
  echo "Usage: $0 <core|accounts|daily>" >&2
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

case "$JOB" in
  core)
    run_step "cluster_topics"  cluster_topics.py
    run_step "fuzzy_trend"     fuzzy_trend.py
    run_step "nlp_sentiment"   nlp_sentiment.py daily
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
    echo "Unknown job '$JOB'. Expected core|accounts|daily." >&2
    exit 1
    ;;
esac

if [ ${#FAILURES[@]} -gt 0 ]; then
  JOINED=$(IFS=', '; echo "${FAILURES[*]}")
  bash alert.sh "TrendForceDash: $JOB run — issues" "Steps that failed: ${JOINED}. Check pipeline.log."
  echo "[$(date '+%Y-%m-%d %H:%M:%S')] '$JOB' pipeline run finished with failures: ${JOINED}"
  exit 1
fi

echo "[$(date '+%Y-%m-%d %H:%M:%S')] '$JOB' pipeline run complete."
