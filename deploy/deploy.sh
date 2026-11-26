#!/usr/bin/env bash
# Brings a VPS checkout of FuelWatch up to date and rebuilds the stack. Run on the server from anywhere in the clone:
#   bash deploy/deploy.sh            # the newest commit on main
#   bash deploy/deploy.sh <commit>   # that commit (what the GitHub workflow passes: the one CI tested)
# It only moves forward (fast-forward), so local commits or edited tracked files make it stop instead of losing them.
# Setup: docs/DEPLOY.md
set -euo pipefail

fail() {
  echo "deploy: $*" >&2
  exit 1
}

# The last value of NAME in .env, without surrounding quotes. The file is read, never executed.
setting() {
  sed -n "s/^[[:space:]]*$1=//p" .env | tail -n 1 | tr -d '\r' | sed -e 's/[[:space:]]*$//' -e 's/^"\(.*\)"$/\1/' -e "s/^'\(.*\)'$/\1/"
}

# A server on the internet must not run on the demo defaults: Compose would publish the simulator, which has no
# sign-in at all, and anyone who read the README could approve shipments.
check_settings() {
  [ -f .env ] || fail "no .env file. Create it from .env.example (docs/DEPLOY.md)."
  [ "$(setting BIND_ADDRESS)" = "127.0.0.1" ] || fail "set BIND_ADDRESS=127.0.0.1 in .env, so only the console is reachable from outside."
  local token accounts grafana
  token="$(setting OPERATOR_TOKEN)"
  accounts="$(setting ACCOUNTS)"
  grafana="$(setting GRAFANA_ADMIN_PASSWORD)"
  if [ -z "$accounts" ]; then
    { [ "${#token}" -ge 16 ] && [ "$token" != "demo-token" ]; } || fail "set ACCOUNTS, or an OPERATOR_TOKEN of your own with 16 characters or more."
  fi
  { [ -n "$grafana" ] && [ "$grafana" != "admin" ]; } || fail "set GRAFANA_ADMIN_PASSWORD in .env to something other than admin."
  [ -z "$(setting DASHBOARD_PREFILL_TOKEN)" ] || fail "remove DASHBOARD_PREFILL_TOKEN: it sends the token to every visitor."
}

# Everything is inside functions so that bash has read the whole script before git replaces this file.
main() {
  local target="${1:-origin/main}"
  cd "$(dirname "$0")/.."
  exec 9>.git/deploy.lock
  if command -v flock >/dev/null 2>&1; then
    flock -n 9 || fail "another deployment is running."
  fi
  check_settings

  git fetch --quiet origin main
  git merge --ff-only --quiet "$target" || fail "cannot fast-forward to $target. Check 'git status' on the server."
  echo "deploy: at $(git log --oneline -1)"
  check_settings # the new version may ask for more

  docker compose up -d --build --wait --wait-timeout 600
  # Compose does not see a change inside a mounted config file, so the services that read them start again.
  docker compose restart prometheus grafana
  docker compose up -d --wait --wait-timeout 180
  docker compose ps
  echo "deploy: done"
}

main "$@"
