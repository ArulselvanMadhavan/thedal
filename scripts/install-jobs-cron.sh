#!/usr/bin/env bash
# One-time cron install. The Resend API key is read with read -s (not echoed, not
# saved to bash history) and stored only in your user crontab.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
# shellcheck source=scripts/thedal-paths.sh
source "$ROOT/scripts/thedal-paths.sh"
resolve_thedal_paths "$ROOT"

STATE="${THEDAL_STATE_DIR:-/lm/users/arul/thedal-state}"
CRON_SCRIPT="$ROOT/scripts/jobs-cron.sh"
TIME="${THEDAL_JOBS_TIME:-06:00}"
if [[ -f "$STATE/cron.toml" ]]; then
  TOML_TIME="$(sed -n 's/^time[[:space:]]*=[[:space:]]*"\([^"]*\)".*/\1/p' "$STATE/cron.toml" | head -1)"
  if [[ -n "$TOML_TIME" ]]; then TIME="$TOML_TIME"; fi
fi
HOUR="${TIME%%:*}"
MIN="${TIME#*:}"
USE_STDIN=false

for arg in "$@"; do
  case "$arg" in
    --api-key-stdin) USE_STDIN=true ;;
    -h | --help)
      echo "Usage: scripts/install-jobs-cron.sh [--api-key-stdin]"
      echo ""
      echo "Installs a daily job-search email cron job. The API key is stored in crontab only."
      exit 0
      ;;
    *)
      echo "Unknown option: $arg (try --help)" >&2
      exit 2
      ;;
  esac
done

if [[ ! -f "$STATE/cron.toml" ]]; then
  echo "Create $STATE/cron.toml first (see cron.toml.example)." >&2
  exit 1
fi
mkdir -p "$STATE/logs"

if [[ "$USE_STDIN" == true ]]; then
  if [[ -t 0 ]]; then
    echo "Expected API key on stdin (use --help for examples)." >&2
    exit 2
  fi
  IFS= read -r KEY || true
  KEY="${KEY//$'\r'/}"
elif [[ -t 0 ]]; then
  read -rsp "Resend API key (re_…): " KEY
  echo
else
  echo "Run interactively, or pipe the key: … | scripts/install-jobs-cron.sh --api-key-stdin" >&2
  exit 2
fi

if [[ -z "$KEY" ]]; then
  echo "No key entered." >&2
  exit 1
fi
if [[ "${KEY:0:3}" != "re_" ]]; then
  echo "Key should start with re_" >&2
  unset KEY
  exit 2
fi

TMP="$(mktemp)"
trap 'rm -f "$TMP"; unset KEY' EXIT

{
  crontab -l 2>/dev/null \
    | grep -v 'scripts/jobs-cron.sh' \
    | grep -v '^THEDAL_RESEND_API_KEY=' \
    | grep -v '^THEDAL_STATE_DIR=' \
    | grep -v '^CRON_TZ=' \
    || true
  echo "CRON_TZ=America/Los_Angeles"
  echo "THEDAL_STATE_DIR=$STATE"
  echo "THEDAL_RESEND_API_KEY=$KEY"
  echo "$MIN $HOUR * * * $CRON_SCRIPT >> $STATE/logs/cron.log 2>&1"
} >"$TMP"

crontab "$TMP"

echo "Installed daily job-search cron ($TIME America/Los_Angeles via CRON_TZ)."
echo "Recipient is jobs.to in cron.toml (Resend-verified address; forward to profile email)."
echo "Manual test: scripts/jobs-cron.sh"
