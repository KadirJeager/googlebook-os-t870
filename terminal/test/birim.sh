#!/usr/bin/env bash
# Host unit tests of the pieces that decide what other apps may ask of the Terminal:
#   daemon/droid.h                   destinations in the droid home, base64 of copied files
#   app/.../AgentRules.java          Android terminal's checks (commands, paths, URIs, folders)
# Builds into a temporary directory under /var/tmp and removes it. Needs c++ and javac/java.
set -euo pipefail
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
WORK="$(mktemp -d /var/tmp/gbos-terminal-birim.XXXXXX)"
trap 'rm -rf "$WORK"' EXIT

c++ -std=c++20 -Wall -Wextra -Werror -I"$HERE/daemon" -o "$WORK/droid_test" "$HERE/test/birim/droid_test.cpp"
"$WORK/droid_test"

mkdir -p "$WORK/classes"
javac -encoding UTF-8 -Xlint:all -Werror -d "$WORK/classes" \
    "$HERE/app/src/com/gbos/terminal/AgentRules.java" "$HERE/test/birim/AgentRulesTest.java"
java -cp "$WORK/classes" com.gbos.terminal.AgentRulesTest
