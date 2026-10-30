#!/usr/bin/env bash
# Runs a heavy job (ext4 images, kernel build, emulator) through the kaynak memory/disk gate when
# this machine has it, otherwise directly:
#
#   kapi.sh --gb 2 --tur derleme --ad "GooglebookOS: ext4" [--disk-gb 5 --yol /var/tmp] -- CMD ARG...
#
# The gate (kaynak.py al ...) queues jobs of every session on a shared machine and refuses to start
# one when a disk it writes to is below its free-space floor. KAYNAK_PY names it; without it the
# skill link ~/.claude/skills/kaynak is tried. On a machine without either the options are only
# checked and CMD runs as it is: there is nobody to share memory with.
set -euo pipefail

K="${KAYNAK_PY:-$HOME/.claude/skills/kaynak/scripts/kaynak.py}"
if [[ -f "$K" ]]; then
    exec python3 "$K" al "$@"
fi
while [[ $# -gt 0 && "$1" != "--" ]]; do
    shift
done
[[ $# -gt 1 ]] || { echo "usage: kapi.sh [gate options] -- CMD [ARG...]" >&2; exit 2; }
shift
exec "$@"
