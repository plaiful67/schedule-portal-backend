#!/usr/bin/env bash
# Snapshot / diff the 16 bowel-prep variant repos for the builder-collapse refactor.
# Usage: snapshot_sites.sh snapshot   # capture current output as baseline
#        snapshot_sites.sh check      # assert current output == baseline (diff -r, sans .git)
set -euo pipefail
ROOT="${HOME}/Desktop/peds-gi-system"
BASE="/tmp/giready-sites-baseline"
REPOS=(
  prep-giready prep86-giready
  egdcolon-giready egdcolon86-giready
  preplact-giready preplact86-giready
  egdcolonlact-giready egdcolonlact86-giready
  prepclenpiq-giready prepclenpiq86-giready
  egdcolonclenpiq-giready egdcolonclenpiq86-giready
  prepsuprep-giready prepsuprep86-giready
  egdcolonsuprep-giready egdcolonsuprep86-giready
)
case "${1:-}" in
  snapshot)
    rm -rf "$BASE"; mkdir -p "$BASE"
    for r in "${REPOS[@]}"; do
      [ -d "$ROOT/$r" ] && cp -R "$ROOT/$r" "$BASE/$r"
    done
    echo "snapshot: ${#REPOS[@]} repos -> $BASE"
    ;;
  check)
    rc=0
    for r in "${REPOS[@]}"; do
      [ -d "$ROOT/$r" ] || continue
      if ! diff -r -x '.git' "$BASE/$r" "$ROOT/$r" >/tmp/giready-diff-$r.txt 2>&1; then
        echo "DIFF in $r:"; cat /tmp/giready-diff-$r.txt; rc=1
      fi
    done
    [ $rc -eq 0 ] && echo "check: all repos byte-identical to baseline"
    exit $rc
    ;;
  *) echo "usage: $0 {snapshot|check}"; exit 2 ;;
esac
