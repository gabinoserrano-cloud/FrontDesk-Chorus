#!/usr/bin/env python3
"""Nightly backup of the dashboard's cloud state.

Copies the fd_data 'main' row into a rolling backup row keyed by day of month
(backup-d01 ... backup-d31), so roughly a month of daily snapshots is kept with
no delete permissions needed (rows are upserted in place as the month wraps).

Restore: fetch the backup row's data and PATCH it back onto id=main, e.g. from
an admin machine:
    GET  /rest/v1/fd_data?id=eq.backup-d05&select=data
    PATCH /rest/v1/fd_data?id=eq.main   body: {"data": <that data>, "updated_at": now}

Config via env vars (set as GitHub Actions secrets — no fallbacks on purpose):
  SUPA_URL, SUPA_ANON, SUPA_EMAIL, SUPA_PASS
"""
import os, sys, json, datetime, urllib.request

SUPA_URL  = os.environ.get("SUPA_URL", "https://adovwmwpvwomljggscge.supabase.co")
SUPA_ANON = os.environ.get("SUPA_ANON", "")
SUPA_EMAIL = os.environ.get("SUPA_EMAIL", "")
SUPA_PASS  = os.environ.get("SUPA_PASS", "")


def die(msg):
    sys.stderr.write("[nightly_backup] " + msg + "\n")
    sys.exit(1)


def call(path, method="GET", body=None, headers=None):
    h = {"apikey": SUPA_ANON, "Content-Type": "application/json"}
    if headers:
        h.update(headers)
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(SUPA_URL + path, data=data, headers=h, method=method)
    with urllib.request.urlopen(req, timeout=120) as r:
        raw = r.read()
        return json.loads(raw) if raw.strip() else None


def main():
    if not (SUPA_ANON and SUPA_EMAIL and SUPA_PASS):
        die("Missing SUPA_ANON / SUPA_EMAIL / SUPA_PASS env vars. "
            "Add them as repository secrets (Settings > Secrets and variables > Actions).")

    tok = call("/auth/v1/token?grant_type=password", "POST",
               {"email": SUPA_EMAIL, "password": SUPA_PASS})
    tok = (tok or {}).get("access_token")
    if not tok:
        die("Supabase auth returned no access_token")
    auth = {"Authorization": "Bearer " + tok}

    rows = call("/rest/v1/fd_data?id=eq.main&select=data,updated_at", headers=auth)
    if not rows or not rows[0].get("data"):
        die("Main row is missing or empty — refusing to overwrite a backup with nothing.")
    data = rows[0]["data"]

    # Sanity floor: a real state has many top-level collections. An empty/wiped
    # state must never overwrite a good backup slot.
    if len(data.keys()) < 10:
        die("Main row has only %d top-level keys — looks wiped, not backing up." % len(data.keys()))

    day = datetime.date.today().strftime("%d")
    backup_id = "backup-d" + day
    payload = {"id": backup_id, "data": data,
               "updated_at": datetime.datetime.utcnow().isoformat() + "Z"}
    call("/rest/v1/fd_data", "POST", payload,
         headers=dict(auth, **{"Prefer": "resolution=merge-duplicates,return=minimal"}))
    size_kb = len(json.dumps(data)) // 1024
    print("Backed up main (%d KB) to %s" % (size_kb, backup_id))


if __name__ == "__main__":
    main()
