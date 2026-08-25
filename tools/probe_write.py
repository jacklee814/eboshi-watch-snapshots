#!/usr/bin/env python3
"""Can this sandbox actually WRITE to the repo? Two paths, real attempts.

Reads started working once the Claude GitHub App was installed on the owner
account, but a read-only success says nothing about writes. This makes one
real REST write and one real git push -- no dry runs, since a dry run cannot
distinguish "would succeed" from "server never checked".

Output is deliberately tiny so nothing gets truncated on the way back.
Touches only state/cloud-verify.flag.
"""
import base64
import os
import subprocess
import sys

REPO = "jacklee814/eboshi-watch-snapshots"
PATH = "state/cloud-verify.flag"

import requests


def rest_put(label, token):
    url = f"https://api.github.com/repos/{REPO}/contents/{PATH}"
    headers = {"Accept": "application/vnd.github+json"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    # An existing file needs its sha; fetch it first and ignore failure.
    sha = None
    try:
        r = requests.get(url, headers=headers, timeout=20)
        if r.status_code == 200:
            sha = r.json().get("sha")
    except Exception:
        pass
    payload = {"message": f"probe: rest write ({label})",
               "content": base64.b64encode(b"probe\n").decode()}
    if sha:
        payload["sha"] = sha
    try:
        r = requests.put(url, headers=headers, json=payload, timeout=20)
        print(f"REST PUT [{label}]: {r.status_code} {r.text[:180]}")
        return r.status_code in (200, 201)
    except Exception as e:
        print(f"REST PUT [{label}]: EXC {type(e).__name__}: {e}")
        return False


def real_push():
    steps = [
        ["git", "fetch", "origin", "main"],
        ["git", "checkout", "-B", "probe-write", "origin/main"],
    ]
    for cmd in steps:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=60)
        if r.returncode != 0:
            print(f"git {' '.join(cmd[1:])}: exit={r.returncode} {r.stderr.strip()[:200]}")
            return False

    os.makedirs("state", exist_ok=True)
    with open(PATH, "w") as f:
        f.write("probe\n")
    subprocess.run(["git", "add", PATH], capture_output=True, text=True)
    subprocess.run(["git", "-c", "user.name=cloud-probe",
                    "-c", "user.email=cloud-probe@users.noreply.github.com",
                    "commit", "-q", "-m", "probe: git write"],
                   capture_output=True, text=True)

    r = subprocess.run(["git", "push", "origin", "HEAD:main"],
                       capture_output=True, text=True, timeout=90)
    print(f"GIT PUSH: exit={r.returncode}")
    out = (r.stdout + r.stderr).strip()
    print(f"GIT PUSH msg: {out[:400]}")
    return r.returncode == 0


def main():
    pat = os.environ.get("EBOSHI_GH_TOKEN", "")
    ok_rest = rest_put("PAT", pat)
    if not ok_rest:
        ok_rest = rest_put("no-auth", None)
    ok_git = real_push()
    print()
    print(f"VERDICT rest_write={'OK' if ok_rest else 'BLOCKED'} "
          f"git_write={'OK' if ok_git else 'BLOCKED'}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
