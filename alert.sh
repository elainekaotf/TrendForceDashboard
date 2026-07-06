#!/bin/bash
# Usage: bash alert.sh "Title" "Message body"
# Fires a native macOS notification so pipeline failures don't just sit
# silently in pipeline.log until someone happens to check.

TITLE="${1:-TrendForceDash}"
MESSAGE="${2:-Something went wrong.}"

osascript -e "display notification \"${MESSAGE//\"/\\\"}\" with title \"${TITLE//\"/\\\"}\" sound name \"Basso\"" 2>/dev/null || true
