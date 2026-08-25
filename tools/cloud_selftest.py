#!/usr/bin/env python3
"""Read-only self-test for running this watcher in a Claude Code cloud routine.

Answers one question: can a cloud sandbox do the four things eboshi_watch.py
needs? It never sends a LINE message, never touches state/notified.flag (the
real flag), and never prints a credential value -- only lengths.

Run it with:  python3 tools/cloud_selftest.py
Exit code is always 0; the verdict is in the output.
"""
import os
import sys

# The watcher modules live in the repo root, but running this as
# `python3 tools/cloud_selftest.py` puts tools/ on sys.path -- not the root.
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

VERIFY_PATH = "state/cloud-verify.flag"   # scratch path, never the real flag


def head(title):
    print(f"\n=== {title} ===", flush=True)


def test_env():
    head("A. environment variables reached the sandbox")
    for name in ("EBOSHI_GH_TOKEN", "LINE_CHANNEL_TOKEN", "LINE_GROUP_ID"):
        print(f"{name}: len={len(os.environ.get(name, ''))}")
    for name in ("EBOSHI_SNAPSHOT_REPO", "EBOSHI_STATE_REPO", "EBOSHI_STATE_PATH"):
        print(f"{name}: {os.environ.get(name, '<unset>')}")
    return bool(os.environ.get("EBOSHI_GH_TOKEN"))


def test_calendar():
    head("B. outbound reachability: freecalend.com")
    try:
        import calendar_http
        data = calendar_http.fetch_month(2026, 9)
        print(f"OK type={type(data).__name__} sample={str(data)[:200]}")
        return True
    except Exception as e:
        print(f"FAIL {type(e).__name__}: {e}")
        return False


def test_line():
    head("C. outbound reachability: api.line.me (read-only, sends nothing)")
    try:
        import requests
        r = requests.get(
            "https://api.line.me/v2/bot/info",
            headers={"Authorization": f"Bearer {os.environ.get('LINE_CHANNEL_TOKEN', '')}"},
            timeout=20,
        )
        print(f"status={r.status_code} body={r.text[:200]}")
        return r.status_code == 200
    except Exception as e:
        print(f"FAIL {type(e).__name__}: {e}")
        return False


def test_state_roundtrip():
    head("D. state persistence: GitHub Contents API write/read round-trip")
    os.environ["EBOSHI_STATE_PATH"] = VERIFY_PATH
    try:
        import eboshi_watch as W
    except Exception as e:
        print(f"FAIL import eboshi_watch: {type(e).__name__}: {e}")
        return False

    print(f"USE_REMOTE_STATE={W.USE_REMOTE_STATE} repo={W.STATE_REPO} path={W.STATE_PATH}")
    if not W.USE_REMOTE_STATE:
        print("FAIL remote state disabled -- token or repo missing")
        return False

    # set_notified() swallows write errors and only warns on stderr, so the
    # verdict must come from reading the value back, not from "no exception".
    W.set_notified(True)
    after_true = W.already_notified()
    W.set_notified(False)
    after_false = W.already_notified()
    print(f"wrote True  -> read back {after_true}")
    print(f"wrote False -> read back {after_false}")
    return after_true is True and after_false is False


def test_where_blocked():
    head("D2. if D failed: is it the token or the sandbox proxy?")
    try:
        import requests
    except Exception as e:
        print(f"skip: {type(e).__name__}: {e}")
        return
    headers = {
        "Authorization": f"Bearer {os.environ.get('EBOSHI_GH_TOKEN', '')}",
        "Accept": "application/vnd.github+json",
    }
    probes = [
        ("rate_limit (not repo-scoped)", "https://api.github.com/rate_limit"),
        ("repo read", "https://api.github.com/repos/jacklee814/eboshi-watch-snapshots"),
        ("gists list", "https://api.github.com/gists"),
    ]
    for label, url in probes:
        try:
            r = requests.get(url, headers=headers, timeout=20)
            print(f"{label}: {r.status_code} {r.text[:160]}")
        except Exception as e:
            print(f"{label}: EXC {type(e).__name__} {e}")


def main():
    results = {
        "A env": test_env(),
        "B calendar": test_calendar(),
        "C line": test_line(),
        "D state": test_state_roundtrip(),
    }
    if not results["D state"]:
        test_where_blocked()

    head("VERDICT")
    for name, ok in results.items():
        print(f"{name}: {'PASS' if ok else 'FAIL'}")
    print("\nD is the blocking one: without it the hourly job re-notifies "
          "the LINE group on every run.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
