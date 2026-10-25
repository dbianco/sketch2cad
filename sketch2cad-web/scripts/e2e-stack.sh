#!/usr/bin/env bash
# Full compose stack for the Playwright E2E (jotdown decision D-9, H0-12).
#
# The stack is the compose of sketch2cad-api/ from the same commit (db, api, worker, web);
# the tests talk to the web service published at http://127.0.0.1:${WEB_PORT}, never to a
# static preview server.
#
#   bash scripts/e2e-stack.sh up     build and start the stack, wait until it is healthy and
#                                    create a technician user with the CLI
#   bash scripts/e2e-stack.sh down   remove the stack, including its volumes (down -v)
#
# Secrets: POSTGRES_PASSWORD and SESSION_SECRET are throwaway random values generated in
# memory and exported only to this process and its children (same as
# sketch2cad-api/scripts/compose_smoke.sh). The E2E user's random password is written only
# to a mode-600 file inside E2E_STATE_DIR (default: a new `mktemp -d` directory outside the
# repository); it is never printed. `up` prints the path of that file on its last line as
# `E2E_CREDENTIALS_FILE=<path>`. The file holds two lines: username and password.
#
# Optional environment variables:
#   WEB_PORT          host port of the web service (default 18080)
#   APP_ENV           application environment (default dev)
#   SIGNING_KEY_PATH  signing key path passed to the api (default placeholder, unused in H0)
#   E2E_STATE_DIR     existing private directory for the credentials file
#   E2E_REUSE=1       reuse an already running `sketch2cad` project instead of refusing; the
#                     stack is not rebuilt and only the user is created
#   KEEP_UP=1         `up` leaves the stack running even when it fails
#
# Usage (from sketch2cad-web/): bash scripts/e2e-stack.sh up|down
set -euo pipefail

web_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
api_dir="$(cd "$web_dir/../sketch2cad-api" && pwd)"
cd "$api_dir"

PROJECT="sketch2cad"
export WEB_PORT="${WEB_PORT:-18080}"
export APP_ENV="${APP_ENV:-dev}"
export SIGNING_KEY_PATH="${SIGNING_KEY_PATH:-/run/sketch2cad/signing-key.pem}"

compose() {
  docker compose -p "$PROJECT" "$@"
}

fail() {
  echo "FAIL: $*" >&2
  exit 1
}

stack_down() {
  # Compose only interpolates the required variables here; their values are irrelevant.
  POSTGRES_PASSWORD="${POSTGRES_PASSWORD:-unused}" SESSION_SECRET="${SESSION_SECRET:-unused}" \
    compose down -v --remove-orphans >/dev/null 2>&1 || echo "WARN: teardown failed" >&2
}

up_teardown() {
  local status=$?
  if [ "$status" -ne 0 ]; then
    [ -n "${credentials:-}" ] && rm -f "$credentials"
    if [ "${KEEP_UP:-0}" = "1" ] || [ "${reused:-0}" = "1" ]; then
      echo "stack left running after a failure (KEEP_UP=1 or E2E_REUSE=1)" >&2
    else
      echo "up failed: tearing down (down -v)" >&2
      stack_down
    fi
  fi
  exit "$status"
}

cmd_up() {
  reused=0
  credentials=""
  if [ -n "$(POSTGRES_PASSWORD=unused SESSION_SECRET=unused compose ps -a -q)" ]; then
    [ "${E2E_REUSE:-0}" = "1" ] \
      || fail "compose project '$PROJECT' already has containers; bring it down first or set E2E_REUSE=1"
    reused=1
  fi
  trap up_teardown EXIT
  trap 'exit 130' INT TERM

  if [ "$reused" = "1" ]; then
    echo "E2E_REUSE=1: using the running '$PROJECT' stack"
    # `exec` does not recreate containers, so placeholder secrets are safe here.
    export POSTGRES_PASSWORD=unused SESSION_SECRET=unused
  else
    POSTGRES_PASSWORD="$(openssl rand -hex 24)"
    SESSION_SECRET="$(openssl rand -hex 32)"
    export POSTGRES_PASSWORD SESSION_SECRET
    echo "docker compose up -d --build --wait"
    up_start=$(date +%s)
    compose up -d --build --wait --wait-timeout 300 || {
      compose ps -a >&2 || true
      fail "docker compose up --wait failed"
    }
    echo "stack_up_seconds=$(( $(date +%s) - up_start ))"
  fi

  local state_dir="${E2E_STATE_DIR:-}"
  if [ -z "$state_dir" ]; then
    state_dir="$(mktemp -d "${TMPDIR:-/tmp}/sketch2cad-e2e.XXXXXX")"
  fi
  [ -d "$state_dir" ] || fail "E2E_STATE_DIR '$state_dir' is not a directory"
  case "$(cd "$state_dir" && pwd -P)/" in
    "$(cd "$web_dir/.." && pwd -P)"/*) fail "E2E_STATE_DIR must be outside the repository" ;;
  esac

  local username password
  username="e2e-$(openssl rand -hex 4)"
  password="$(openssl rand -hex 24)"
  credentials="$state_dir/credentials"
  (umask 077 && printf '%s\n%s\n' "$username" "$password" >"$credentials")

  printf '%s\n' "$password" \
    | compose exec -T api python -m sketch2cad.cli create-user \
      --username "$username" --role technician --display-name "E2E" \
    || fail "could not create the E2E user"

  trap - EXIT
  echo "E2E_CREDENTIALS_FILE=$credentials"
}

case "${1:-}" in
  up) cmd_up ;;
  down) stack_down; echo "stack '$PROJECT' removed (down -v)" ;;
  *) fail "usage: $0 up|down" ;;
esac
