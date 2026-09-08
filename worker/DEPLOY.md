# Sync proxy — deploy guide

Moves the Supabase credentials out of `index.html` and onto Cloudflare, so the browser
never sees them and the public repo has nothing left to leak.

**Do the credential rotation first** (below). The Worker prevents the *next* exposure;
it does not un-leak the key that has already been public.

---

## 0. Rotate first — ~20 min, do this today

The current anon key and sync password have been readable in the public repo and remain
in git history. Whether or not you ever deploy this Worker, they must be replaced.

1. Supabase → Project Settings → API → roll the **anon key**.
2. Supabase → Authentication → Users → `sync@chorusfd.internal` → set a new password.
3. Paste both into `index.html` (lines ~2179 and ~2181) so the current setup keeps working.
4. Push. The old values stay in history but no longer open anything.

You are now safe. Everything below can happen on an unhurried shift.

---

## 1. Install and log in

```bash
npm install -g wrangler
wrangler login
```

## 2. Set the secrets

Run from the `worker/` directory. Each prompts for a value; nothing is written to disk.

```bash
cd worker
wrangler secret put SUPA_URL
wrangler secret put SUPA_KEY
wrangler secret put SUPA_SYNC_EMAIL
wrangler secret put SUPA_SYNC_PASS
```

Use the **new** rotated values from step 0.

## 3. Deploy

```bash
wrangler deploy
```

`wrangler.toml` routes `chorusfd.com/api/*` to the Worker.

## 4. Confirm Cloudflare Access covers it — do not skip

This is the step that makes the whole exercise worth doing. Right now the leaked key
works from anywhere on earth. Once the API sits behind Access, only your logged-in team
can reach the data at all.

1. Cloudflare dashboard → Zero Trust → Access → Applications.
2. Open the application protecting `chorusfd.com`.
3. Confirm its path rule covers `/api/*` (a domain-wide rule already does).
4. Test it: open a **private window** (not logged in) and visit
   `https://chorusfd.com/api/data?meta=1`.

   - You get the **Access login page** → correct, you're done.
   - You get **JSON** → the API is publicly reachable. Stop and fix the Access path
     rule before continuing.

## 5. Cut over

In `index.html`, change one line:

```js
var SYNC_API = null;      // before
var SYNC_API = '/api';    // after
```

Push, hard-refresh, and confirm the sidebar reads **Synced**.

## 6. Verify with two devices before trusting it

Sync is the part of this app you least want to destabilize, so prove it:

- Load the dashboard on two devices, both logged in.
- Edit something different on each (add a Logbook entry on each, say).
- Save both, wait for the 30s poll.
- **Both entries must survive on both devices.** If either disappears, roll back (step 7).

## 7. Rollback

Set `SYNC_API = null` and push. The legacy direct-to-Supabase path is still in the file
and takes over immediately. Nothing else to undo.

## 8. Only after a clean week — delete the credentials

Once you trust it, remove these three lines from `index.html`:

```js
var SUPA_KEY = '...';
var SUPA_SYNC_EMAIL = '...';
var SUPA_SYNC_PASS = '...';
```

`SUPA_URL` can stay (it isn't a secret). Deleting these is what finally makes the public
repo harmless — until then you are protected by rotation alone.

Note: removing them also removes the rollback path in step 7, so do this only after the
Worker has run a full week without incident.

---

## What the Worker exposes

| method | path | returns |
|---|---|---|
| GET | `/api/data` | `{data, updated_at}` or `null` |
| GET | `/api/data?meta=1` | `{updated_at}` — the ~60-byte poll probe |
| PUT | `/api/data` | `{updated_at}`, or `null` when the compare-and-swap lost |
| GET | `/api/inbox` | `{data}` — the triage queue row |
| PUT | `/api/inbox` | `204` — clears the drained queue |

The `expected` field on `PUT /api/data` carries your compare-and-swap timestamp through
unchanged, so a stale write still matches zero rows and returns `null` rather than
overwriting. The client's three-way merge is untouched.

## Cost

Free. Workers allow 100k requests/day; the 30s poll across a handful of users is roughly
5k/day, and the poll uses the 60-byte `meta=1` probe rather than the full ~1.2MB row.

## Tested

The client path was verified against a local mock implementing this exact contract:
full pull, meta poll, write round-trip, stale-write rejection (no clobber), and a
two-device concurrent edit where both edits survived. `SYNC_API = null` was confirmed
to leave the legacy path untouched.
