#!/bin/sh
set -eu
exec sshpass -f /run/secrets/ssh_password ssh -NT \
  -o StrictHostKeyChecking=yes -o UserKnownHostsFile=/run/secrets/known_hosts \
  -o ExitOnForwardFailure=yes -o ConnectTimeout=10 \
  -o ServerAliveInterval=15 -o ServerAliveCountMax=3 \
  -L 0.0.0.0:5432:127.0.0.1:25433 \
  -L 0.0.0.0:5433:127.0.0.1:25432 \
  "${SSH_USER:-collective}@${SSH_HOST:-193.232.208.42}"
