#!/bin/sh
set -eu
: "${SSH_HOST:?Set SSH_HOST}" "${SSH_USER:?Set SSH_USER}"
: "${TUNNEL_LISTEN_PORT:?Set TUNNEL_LISTEN_PORT}"
: "${TUNNEL_DEST_HOST:?Set TUNNEL_DEST_HOST}" "${TUNNEL_DEST_PORT:?Set TUNNEL_DEST_PORT}"
test -s /run/secrets/ssh_password
test -s /run/secrets/known_hosts
exec sshpass -f /run/secrets/ssh_password ssh -NT \
  -o StrictHostKeyChecking=yes \
  -o UserKnownHostsFile=/run/secrets/known_hosts \
  -o ExitOnForwardFailure=yes \
  -o ServerAliveInterval=15 -o ServerAliveCountMax=3 \
  -o ConnectTimeout=10 \
  -L "${TUNNEL_LISTEN_HOST:-0.0.0.0}:${TUNNEL_LISTEN_PORT}:${TUNNEL_DEST_HOST}:${TUNNEL_DEST_PORT}" \
  -p "${SSH_PORT:-22}" "${SSH_USER}@${SSH_HOST}"
