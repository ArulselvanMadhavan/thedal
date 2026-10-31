#!/usr/bin/env bash
# Shared path resolution for thedal cron scripts.
resolve_thedal_paths() {
  local root="$1"
  THEDAL_ROOT="$root"
  THEDAL_STATE_DIR="${THEDAL_STATE_DIR:-/lm/users/arul/thedal-state}"
  THEDAL_LOG_DIR="${THEDAL_LOG_DIR:-$THEDAL_STATE_DIR/logs}"
  THEDAL_JOBS_DIR="${THEDAL_JOBS_DIR:-$THEDAL_ROOT/jobs}"
}
