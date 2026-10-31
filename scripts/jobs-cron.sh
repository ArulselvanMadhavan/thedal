#!/usr/bin/env bash
# Job digest sender for cron and manual tests. Cron injects THEDAL_RESEND_API_KEY from
# crontab env lines. Manual runs reuse that key from crontab, or prompt / read stdin.
#
# Runs every profile listed in cron.toml [jobs] profiles (legacy single
# `profile` also works) and sends ONE SEPARATE EMAIL PER PROFILE. A failing
# profile does not stop the others; the exit code is non-zero if any failed.
set -uo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
# shellcheck source=scripts/thedal-paths.sh
source "$ROOT/scripts/thedal-paths.sh"
resolve_thedal_paths "$ROOT"

PROFILE_OVERRIDE=""

usage() {
  echo "Usage: scripts/jobs-cron.sh [--profile PATH] [--help]"
  echo ""
  echo "Run the configured job searches and email one digest per profile via Resend."
  echo ""
  echo "After install-jobs-cron.sh, run with no args to use cron.toml defaults."
  echo "  --profile PATH  Run/send only this profile instead of the cron.toml list."
}

load_crontab_key() {
  crontab -l 2>/dev/null | grep '^THEDAL_RESEND_API_KEY=' | tail -1 | sed 's/^THEDAL_RESEND_API_KEY=//'
}

load_crontab_state_dir() {
  crontab -l 2>/dev/null | grep '^THEDAL_STATE_DIR=' | tail -1 | sed 's/^THEDAL_STATE_DIR=//'
}

init_python() {
  export PYENV_ROOT="${PYENV_ROOT:-$HOME/.pyenv}"
  export PATH="$PYENV_ROOT/bin:$PATH"
  if command -v pyenv >/dev/null 2>&1; then
    # shellcheck disable=SC1090
    eval "$(pyenv init -)"
  fi
  cd "$THEDAL_ROOT"
}

# Print cron.toml profiles, one per line (python has tomllib; bash/sed don't
# understand the multi-line `profiles = [...]` array).
read_cron_profiles() {
  THEDAL_STATE_DIR="$THEDAL_STATE_DIR" python - <<'PY'
import os, tomllib
from pathlib import Path

path = Path(os.environ["THEDAL_STATE_DIR"]) / "cron.toml"
profiles = []
if path.exists():
    with path.open("rb") as handle:
        jobs = tomllib.load(handle).get("jobs", {})
    raw = jobs.get("profiles")
    if isinstance(raw, list):
        profiles = [str(v).strip() for v in raw if str(v).strip()]
    elif str(jobs.get("profile", "")).strip():
        profiles = [str(jobs["profile"]).strip()]
for profile in profiles:
    print(profile)
PY
}

run_send() {
  local key="$1"
  mkdir -p "$THEDAL_STATE_DIR" "$THEDAL_LOG_DIR" "$THEDAL_JOBS_DIR"
  init_python

  local profiles=()
  if [[ -n "$PROFILE_OVERRIDE" ]]; then
    profiles=("$PROFILE_OVERRIDE")
  else
    mapfile -t profiles < <(read_cron_profiles)
  fi
  if [[ ${#profiles[@]} -eq 0 ]]; then
    profiles=("profiles/data-engineer.example.json")
  fi

  local failures=0 profile
  for profile in "${profiles[@]}"; do
    if [[ "$profile" != /* ]]; then
      profile="$THEDAL_ROOT/$profile"
    fi
    echo "=== thedal: $profile ==="
    if ! printf '%s' "$key" | python "$THEDAL_ROOT/search_jobs.py" "$profile" --cron --email --api-key-stdin; then
      echo "jobs-cron: FAILED for $profile" >&2
      failures=$((failures + 1))
    fi
  done
  return "$failures"
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    -h | --help)
      usage
      exit 0
      ;;
    --profile)
      PROFILE_OVERRIDE="${2:-}"
      if [[ -z "$PROFILE_OVERRIDE" ]]; then
        echo "--profile needs a path" >&2
        exit 2
      fi
      shift 2
      ;;
    *)
      echo "Unknown option: $1 (try --help)" >&2
      exit 2
      ;;
  esac
done

STATE_FROM_CRON="$(load_crontab_state_dir)"
if [[ -n "$STATE_FROM_CRON" ]]; then
  export THEDAL_STATE_DIR="$STATE_FROM_CRON"
  resolve_thedal_paths "$ROOT"
fi

EXIT_CODE=0
if [[ -n "${THEDAL_RESEND_API_KEY:-}" ]]; then
  KEY="$THEDAL_RESEND_API_KEY"
  unset THEDAL_RESEND_API_KEY
  run_send "$KEY" || EXIT_CODE=$?
  unset KEY
elif KEY="$(load_crontab_key)" && [[ -n "$KEY" ]]; then
  run_send "$KEY" || EXIT_CODE=$?
  unset KEY
elif [[ ! -t 0 ]]; then
  IFS= read -r KEY || true
  KEY="${KEY//$'\r'/}"
  if [[ -z "$KEY" ]]; then
    echo "jobs-cron: empty key on stdin" >&2
    exit 1
  fi
  run_send "$KEY" || EXIT_CODE=$?
  unset KEY
elif [[ -t 0 && -t 1 ]]; then
  read -rsp "Resend API key (re_…): " KEY
  echo
  if [[ -z "$KEY" ]]; then
    echo "No key entered." >&2
    exit 1
  fi
  run_send "$KEY" || EXIT_CODE=$?
  unset KEY
else
  echo "jobs-cron: no API key (run install-jobs-cron.sh first)." >&2
  usage >&2
  exit 1
fi
exit "$EXIT_CODE"
