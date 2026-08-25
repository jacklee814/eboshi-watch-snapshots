#!/bin/sh
# Does a cloud sandbox have a working git push path to this repo?
#
# The REST Contents API (api.github.com/repos/.../contents/...) is what
# eboshi_watch.py uses for its "already notified" flag, and it returns 403 in
# the sandbox. But the sandbox clones this repo fine, so git and the REST API
# clearly authenticate differently. If push works, the flag can live in a
# plain commit instead and the REST block stops mattering.
#
# Writes only state/cloud-verify.flag -- a scratch path, never the real
# state/notified.flag. Sends no LINE message and reads no credentials.
set -u

echo "=== remotes ==="
git remote -v

echo
echo "=== credential configuration (names only, no values) ==="
git config --list --show-origin 2>/dev/null | grep -iE 'credential|helper|url\.' | sed -E 's#(https://)[^@]*@#\1<redacted>@#g' || echo "(none)"

echo
echo "=== gh CLI present? ==="
if command -v gh >/dev/null 2>&1; then gh --version | head -1; gh auth status 2>&1 | head -5; else echo "gh: not installed"; fi

echo
echo "=== fetch ==="
if git fetch origin main 2>&1; then echo "FETCH OK"; else echo "FETCH FAILED"; fi

echo
echo "=== build a scratch commit on top of origin/main ==="
git checkout -B cloud-verify origin/main 2>&1 | tail -2
mkdir -p state
date -u +%Y-%m-%dT%H:%M:%SZ > state/cloud-verify.flag
git add state/cloud-verify.flag
git -c user.name="cloud-selftest" \
    -c user.email="cloud-selftest@users.noreply.github.com" \
    commit -q -m "chore: cloud sandbox git push test" && echo "COMMIT OK"

echo
echo "=== push -- this is the decisive test ==="
if git push origin HEAD:main 2>&1; then echo "PUSH OK"; else echo "PUSH FAILED"; fi
