#!/usr/bin/env bash
# Hold up the laptop→VPS SSH reverse forward for the Forge control room.
#
# Publishes Forge (running on this laptop at 127.0.0.1:8787) on the VPS
# loopback as 127.0.0.1:8791. The tunnel direction mirrors the existing PBN
# test tunnel: the server side publishes no foreign port; nginx is the only
# intended consumer, and it must stay on loopback.
set -euo pipefail

readonly HOST="ubuntu@51.83.199.206"
readonly IDENTITY="${HOME}/.ssh/pbn_vps"
readonly REMOTE_BIND="127.0.0.1:8791"
readonly LOCAL_TARGET="127.0.0.1:8787"

usage() {
  echo "usage: deploy/tunnelforge.sh up|status|down" >&2
  exit 1
}

check_prerequisites() {
  [[ -f "$IDENTITY" ]] || { echo "missing identity $IDENTITY" >&2; exit 1; }
}

ssh_opts() {
  printf '%s\n' -o BatchMode=yes -o IdentitiesOnly=yes -i "$IDENTITY"
}

cmd="${1:-}"; shift 2>/dev/null || true
[[ "$cmd" == "up" || "$cmd" == "status" || "$cmd" == "down" ]] || usage

case "$cmd" in
  up)
    check_prerequisites
    echo "Forwarding VPS ${REMOTE_BIND} -> ${LOCAL_TARGET}; Ctrl-C tears it down."
    exec ssh $(ssh_opts) \
      -o ExitOnForwardFailure=yes \
      -o ServerAliveInterval=30 \
      -o ServerAliveCountMax=3 \
      -N \
      -R "${REMOTE_BIND}:${LOCAL_TARGET}" \
      "$HOST"
    ;;
  status|down)
    check_prerequisites
    pattern="ssh .*-R ${REMOTE_BIND}:"
    pid="$(pgrep -f "$pattern" | head -1 || true)"
    if [[ -z "$pid" ]]; then
      echo "tunnel: not running"
      [[ "$cmd" == "down" ]] && exit 0 || exit 1
    fi
    echo "tunnel: pid ${pid}"
    [[ "$cmd" == "down" ]] && kill "$pid"
    ;;
esac
