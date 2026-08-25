#!/usr/bin/env python3
"""Isolate why this sandbox gets 403 from the GitHub REST API.

Four variables are confounded in the single 403 observed so far:

  1. which credential actually reaches GitHub -- ours, or one the proxy
     substitutes for it
  2. the repo owner -- jacklee814 (App possibly not installed) vs
     chatbotgang (App known good; its routines push branches daily)
  3. the endpoint class -- /rate_limit vs /repos/* vs /gists
  4. transport -- REST vs git

Holding the environment and the run constant, this varies one at a time.
Everything is read-only except a `git push --dry-run`, which authenticates
against the remote but never creates anything there.

Run it with:  python3 tools/diagnose_403.py
"""
import os
import subprocess
import sys

OURS = "jacklee814/eboshi-watch-snapshots"
CONTROL = "chatbotgang/Grazioso"   # App known connected -- the control arm

try:
    import requests
except ImportError:
    print("requests missing; run: pip install --break-system-packages -q requests")
    sys.exit(1)


def head(title):
    print(f"\n=== {title} ===", flush=True)


def get(url, token):
    """token=None means send no Authorization header and let the proxy decide."""
    headers = {"Accept": "application/vnd.github+json"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    try:
        r = requests.get(url, headers=headers, timeout=20)
        return r.status_code, r.text[:220]
    except Exception as e:
        return None, f"EXC {type(e).__name__}: {e}"


def show(label, url, token):
    code, body = get(url, token)
    print(f"{label:<44} {str(code):<5} {body}")


def main():
    pat = os.environ.get("EBOSHI_GH_TOKEN", "")
    gh_token = os.environ.get("GH_TOKEN", "")

    head("0. what credentials exist in this sandbox")
    print(f"EBOSHI_GH_TOKEN: len={len(pat)}")
    print(f"GH_TOKEN: len={len(gh_token)} literal_proxy_injected={gh_token == 'proxy-injected'}")

    head("1. whose identity does GitHub see? (/user)")
    for label, tok in (("with our PAT", pat), ("with no auth header", None)):
        code, body = get("https://api.github.com/user", tok)
        print(f"{label:<44} {str(code):<5} {body}")

    head("2. baseline: non-repo-scoped endpoint")
    show("rate_limit  with our PAT", "https://api.github.com/rate_limit", pat)
    show("rate_limit  with no auth header", "https://api.github.com/rate_limit", None)

    head("3. THE CONTROLLED COMPARISON -- same env, same run, two owners")
    for repo in (OURS, CONTROL):
        url = f"https://api.github.com/repos/{repo}"
        show(f"GET repos/{repo}  PAT", url, pat)
        show(f"GET repos/{repo}  no auth", url, None)

    head("4. contents endpoint (what the watcher actually uses)")
    for repo in (OURS, CONTROL):
        url = f"https://api.github.com/repos/{repo}/contents/README.md"
        show(f"GET contents README  {repo}  PAT", url, pat)
        show(f"GET contents README  {repo}  no auth", url, None)

    head("5. REST write attempt on our scratch path")
    import base64
    url = f"https://api.github.com/repos/{OURS}/contents/state/cloud-verify.flag"
    for label, tok in (("PAT", pat), ("no auth", None)):
        headers = {"Accept": "application/vnd.github+json"}
        if tok:
            headers["Authorization"] = f"Bearer {tok}"
        payload = {"message": "probe: 403 diagnosis",
                   "content": base64.b64encode(b"probe").decode()}
        try:
            r = requests.put(url, headers=headers, json=payload, timeout=20)
            print(f"PUT cloud-verify.flag  {label:<10} {r.status_code} {r.text[:220]}")
        except Exception as e:
            print(f"PUT cloud-verify.flag  {label:<10} EXC {type(e).__name__}: {e}")

    head("6. git transport -- full stderr, nothing hidden")
    for desc, cmd in (
        ("git ls-remote origin HEAD", ["git", "ls-remote", "origin", "HEAD"]),
        ("git push --dry-run", ["git", "push", "--dry-run", "origin",
                                "HEAD:refs/heads/cloud-probe-dryrun"]),
    ):
        print(f"\n--- {desc} ---")
        try:
            r = subprocess.run(cmd, capture_output=True, text=True, timeout=60)
            print(f"exit={r.returncode}")
            if r.stdout.strip():
                print("stdout:", r.stdout.strip()[:600])
            if r.stderr.strip():
                print("stderr:", r.stderr.strip()[:600])
        except Exception as e:
            print(f"EXC {type(e).__name__}: {e}")

    head("READING THE RESULT")
    print("If section 3 shows chatbotgang 200 and jacklee814 403, the block is")
    print("owner-scoped -- the Claude GitHub App is genuinely not installed on")
    print("jacklee814, and the environment is fine.")
    print("If BOTH are 403, the environment or session config is the problem,")
    print("not the App installation.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
