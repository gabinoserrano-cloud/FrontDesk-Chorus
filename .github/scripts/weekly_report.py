#!/usr/bin/env python3
"""
Chorus Front Desk - weekly report robot.

Pulls the live state from Supabase (read-only), totals up the last 7 days, builds
an HTML email, and sends it from a Gmail account (via app password) to the
recipient. Runs weekly on GitHub Actions; also runnable locally for testing.

Stdlib only - no pip installs. GitHub's Ubuntu runners ship Python, same as this Mac.

Config via env vars (all optional except the Gmail password when you want it to send):
  SUPA_URL, SUPA_ANON, SUPA_EMAIL, SUPA_PASS   - default to the (already-public) sync creds
  REPORT_FROM   - sending Gmail address            (default gabinoserrano21@gmail.com)
  REPORT_TO     - recipient(s), comma-separated    (default gserrano@alignrealestate.com)
  GMAIL_USER    - SMTP login (usually == REPORT_FROM)
  GMAIL_APP_PASSWORD - 16-char Google app password. If unset, the report is built and
                       written to report.html but NOT sent (handy for local previews).
  REPORT_DAYS   - window length in days            (default 7)
"""
import os, sys, json, time, html, smtplib, urllib.request, collections, datetime
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText

# ---- config (Supabase creds already ship in the public page source; no new exposure) ----
SUPA_URL  = os.environ.get("SUPA_URL",  "https://adovwmwpvwomljggscge.supabase.co")
SUPA_ANON = os.environ.get("SUPA_ANON", "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJpc3MiOiJzdXBhYmFzZSIsInJlZiI6ImFkb3Z3bXdwdndvbWxqZ2dzY2dlIiwicm9sZSI6ImFub24iLCJpYXQiOjE3NzUyNzkxNjcsImV4cCI6MjA5MDg1NTE2N30.dZgl3jEL1FbBSe_gM3LN9Tc3wGGmsf_Bq6YaKJf4dyk")
SUPA_EMAIL= os.environ.get("SUPA_EMAIL","sync@chorusfd.internal")
SUPA_PASS = os.environ.get("SUPA_PASS", "uM7Jaq1HolIGJaolh3YwRD45LRbpNJ2h")

REPORT_FROM = os.environ.get("REPORT_FROM", "gabinoserrano21@gmail.com")
REPORT_TO   = os.environ.get("REPORT_TO",   "gserrano@alignrealestate.com")
GMAIL_USER  = os.environ.get("GMAIL_USER",  REPORT_FROM)
GMAIL_PASS  = os.environ.get("GMAIL_APP_PASSWORD", "")
WINDOW_DAYS = int(os.environ.get("REPORT_DAYS", "7"))

# brand
NAVY="#0D1B2A"; GREEN="#1A5C3E"; GOLD="#C9A45E"; CREAM="#F7F5F0"; INK="#1c2530"; MUTE="#6b7480"

def die(msg):
    sys.stderr.write("[weekly_report] " + msg + "\n"); sys.exit(1)

# ---------- fetch ----------
def _post(url, headers, body):
    req = urllib.request.Request(url, data=json.dumps(body).encode(), headers=headers, method="POST")
    with urllib.request.urlopen(req, timeout=90) as r: return json.load(r)
def _get(url, headers):
    req = urllib.request.Request(url, headers=headers, method="GET")
    with urllib.request.urlopen(req, timeout=90) as r: return json.load(r)

def fetch_state():
    tok = _post(SUPA_URL + "/auth/v1/token?grant_type=password",
                {"Content-Type": "application/json", "apikey": SUPA_ANON},
                {"email": SUPA_EMAIL, "password": SUPA_PASS}).get("access_token")
    if not tok: die("Supabase auth returned no access_token")
    rows = _get(SUPA_URL + "/rest/v1/fd_data?id=eq.main&select=data,updated_at",
                {"apikey": SUPA_ANON, "Authorization": "Bearer " + tok})
    if not rows: die("Supabase returned no data row")
    return rows[0]["data"]

# ---------- time window ----------
def pacific_now():
    try:
        from zoneinfo import ZoneInfo
        return datetime.datetime.now(ZoneInfo("America/Los_Angeles"))
    except Exception:
        # fallback: fixed PDT offset (June). Good enough for a weekly digest.
        return datetime.datetime.now(datetime.timezone(datetime.timedelta(hours=-7)))

NOW   = pacific_now()
END   = NOW
START = NOW - datetime.timedelta(days=WINDOW_DAYS)
NOW_MS   = int(NOW.timestamp() * 1000)
START_MS = int(START.timestamp() * 1000)
TODAY    = NOW.date()

def in_win_ms(ts):
    try: return START_MS <= int(ts) <= NOW_MS
    except Exception: return False

def parse_ymd(s):
    try: return datetime.datetime.strptime(str(s)[:10], "%Y-%m-%d").date()
    except Exception: return None

def date_in_last_window(s):
    d = parse_ymd(s)
    return bool(d and START.date() <= d <= TODAY)

def date_in_next_window(s, days=7):
    d = parse_ymd(s)
    return bool(d and TODAY <= d <= TODAY + datetime.timedelta(days=days))

def lst(data, key):
    v = data.get(key)
    return v if isinstance(v, list) else []

# ---------- compute ----------
def build_report(data):
    R = {}

    # --- moves: completed this week (archived), split by type; plus upcoming ---
    seen, archived = set(), []
    for m in lst(data, "moves") + lst(data, "archivedMoves"):
        if not isinstance(m, dict): continue
        mid = m.get("id")
        if mid in seen: continue
        seen.add(mid)
        if in_win_ms(m.get("archivedAt") or 0):
            archived.append(m)
    R["mv_in"]  = sum(1 for m in archived if m.get("type") == "in")
    R["mv_out"] = sum(1 for m in archived if m.get("type") == "out")

    upcoming = []
    arch_ids = {m.get("id") for m in lst(data, "archivedMoves")}
    for m in lst(data, "moves"):
        if not isinstance(m, dict) or m.get("archivedAt") or m.get("id") in arch_ids: continue
        anchor = m.get("apptDate") or m.get("date")
        if date_in_next_window(anchor, 7):
            upcoming.append((parse_ymd(anchor), m))
    upcoming.sort(key=lambda t: (t[0] or TODAY))
    R["up_in"]  = sum(1 for _, m in upcoming if m.get("type") == "in")
    R["up_out"] = sum(1 for _, m in upcoming if m.get("type") == "out")
    R["upcoming_rows"] = [
        (m.get("type", "").upper(), m.get("unit", "?"), m.get("resident", ""), (d.strftime("%a %b %-d") if d else "?"))
        for d, m in upcoming[:12]
    ]

    # --- keys (one-time) ---
    keys = lst(data, "keys")
    R["keys_issued_wk"] = sum(1 for k in keys if k.get("type") == "one-time" and in_win_ms(k.get("createdAt") or k.get("issuedAt") or 0))
    out_otk = [k for k in keys if k.get("type") == "one-time" and k.get("status") == "issued"]
    R["keys_out_now"] = len(out_otk)
    day_ago = NOW_MS - 86400 * 1000
    R["keys_overdue"] = sum(1 for k in out_otk if (k.get("issuedAt") or k.get("createdAt") or NOW_MS) < day_ago)

    # --- guests ---
    R["guests_wk"] = sum(1 for g in lst(data, "guests") if isinstance(g, dict) and in_win_ms(g.get("createdAt") or 0))

    # --- concierge ---
    conc = [c for c in lst(data, "concierge") if isinstance(c, dict) and in_win_ms(c.get("createdAt") or 0)]
    R["conc_wk"] = len(conc)
    R["conc_done"] = sum(1 for c in conc if c.get("status") == "completed")
    R["conc_open_now"] = sum(1 for c in lst(data, "concierge") if isinstance(c, dict) and c.get("status") not in ("completed", "cancelled"))

    # --- amenities (bookings/events that took place this week) ---
    am_wk = [a for a in lst(data, "amenities") if isinstance(a, dict) and date_in_last_window(a.get("date"))]
    R["am_wk"] = len(am_wk)
    R["am_by_type"] = dict(collections.Counter((a.get("type") or "booking") for a in am_wk))
    R["am_next"] = sum(1 for a in lst(data, "amenities") if isinstance(a, dict) and date_in_next_window(a.get("date"), 7))

    # --- security incidents (by type) ---
    sec = [s for s in lst(data, "security") if isinstance(s, dict) and (in_win_ms(s.get("createdAt") or 0) or date_in_last_window(s.get("date")))]
    R["sec_wk"] = len(sec)
    R["sec_by_type"] = dict(collections.Counter((s.get("type") or "Incident") for s in sec))

    # --- maintenance ---
    mt = lst(data, "maintenance")
    closed = ("resolved", "closed", "completed", "done")
    R["mt_opened"] = sum(1 for x in mt if isinstance(x, dict) and in_win_ms(x.get("createdAt") or 0))
    R["mt_resolved"] = sum(1 for x in mt if isinstance(x, dict) and str(x.get("status", "")).lower() in closed and in_win_ms(x.get("resolvedAt") or x.get("updatedAt") or 0))
    R["mt_open_now"] = sum(1 for x in mt if isinstance(x, dict) and str(x.get("status", "")).lower() not in closed)

    # --- supplies needing attention (snapshot) ---
    low, out = [], []
    for s in lst(data, "supplies"):
        if not isinstance(s, dict): continue
        try: qty = float(s.get("qty"))
        except (TypeError, ValueError): continue
        try: thr = float(s.get("lowAt"))
        except (TypeError, ValueError): thr = 0
        nm = s.get("name", "?"); u = s.get("unit", "")
        if qty <= 0: out.append((nm, qty, u))
        elif qty <= thr: low.append((nm, qty, u))
    R["sup_out"] = out
    R["sup_low"] = low

    # --- staff activity this week ---
    cnt = collections.Counter()
    for a in lst(data, "activityLog"):
        if isinstance(a, dict) and in_win_ms(a.get("ts") or 0):
            cnt[a.get("by") or "Unknown"] += 1
    R["staff"] = cnt.most_common()
    R["actions_total"] = sum(cnt.values())

    return R

# ---------- render ----------
def esc(x): return html.escape(str(x if x is not None else ""))

def card(title, inner):
    return ('<tr><td style="padding:14px 0 4px;">'
            '<table width="100%%" cellpadding="0" cellspacing="0" style="border:1px solid #e3ddd0;border-radius:10px;background:#ffffff;">'
            '<tr><td style="background:%s;color:%s;font:600 13px/1.2 Arial,sans-serif;letter-spacing:.04em;text-transform:uppercase;padding:10px 16px;border-radius:10px 10px 0 0;">%s</td></tr>'
            '<tr><td style="padding:14px 16px;font:14px/1.55 Arial,sans-serif;color:%s;">%s</td></tr>'
            '</table></td></tr>') % (NAVY, GOLD, esc(title), INK, inner)

def big(n, label, color=GREEN):
    return ('<table cellpadding="0" cellspacing="0" style="display:inline-table;margin:0 18px 8px 0;vertical-align:top;">'
            '<tr><td style="font:700 30px/1 Arial,sans-serif;color:%s;">%s</td></tr>'
            '<tr><td style="font:12px/1.3 Arial,sans-serif;color:%s;padding-top:3px;">%s</td></tr></table>') % (color, esc(n), MUTE, esc(label))

def render_html(R):
    rng = "%s – %s" % (START.strftime("%b %-d"), END.strftime("%b %-d, %Y"))
    parts = []

    # moves
    mv = big(R["mv_in"], "Move-ins done") + big(R["mv_out"], "Move-outs done") + big("%d / %d" % (R["up_in"], R["up_out"]), "Upcoming in / out (7d)", NAVY)
    if R["upcoming_rows"]:
        rows = "".join(
            '<tr><td style="padding:3px 10px 3px 0;font-weight:600;color:%s;">%s</td><td style="padding:3px 14px 3px 0;">Unit %s</td><td style="padding:3px 14px 3px 0;color:%s;">%s</td><td style="padding:3px 0;color:%s;">%s</td></tr>'
            % (GREEN if t == "IN" else GOLD, esc(t), esc(u), esc(r), INK, esc(when), MUTE) for (t, u, r, when) in R["upcoming_rows"])
        mv += '<div style="margin-top:8px;font:12px Arial;color:%s;">Coming up:</div><table cellpadding="0" cellspacing="0" style="font:13px Arial;margin-top:4px;">%s</table>' % (MUTE, rows)
    parts.append(card("Moves", mv))

    # keys
    ks = big(R["keys_issued_wk"], "One-time keys issued") + big(R["keys_out_now"], "Currently out", GOLD if R["keys_out_now"] else GREEN)
    if R["keys_overdue"]: ks += big(R["keys_overdue"], "Overdue (>24h)", "#b1442f")
    parts.append(card("One-Time Keys", ks))

    # desk activity
    da = (big(R["guests_wk"], "Guests signed in")
          + big(R["conc_wk"], "Concierge requests")
          + big(R["am_wk"], "Amenity bookings")
          + big(R["sec_wk"], "Security incidents", "#b1442f" if R["sec_wk"] else GREEN))
    extra = []
    if R["conc_wk"]: extra.append("%d concierge completed, %d still open" % (R["conc_done"], R["conc_open_now"]))
    if R["am_by_type"]: extra.append("amenities: " + ", ".join("%d %s" % (v, k) for k, v in R["am_by_type"].items()) + (" (%d upcoming)" % R["am_next"] if R["am_next"] else ""))
    if R["sec_by_type"]: extra.append("incidents: " + ", ".join("%d %s" % (v, k) for k, v in R["sec_by_type"].items()))
    if any([R["mt_opened"], R["mt_resolved"], R["mt_open_now"]]):
        extra.append("maintenance: %d opened, %d resolved, %d open now" % (R["mt_opened"], R["mt_resolved"], R["mt_open_now"]))
    if extra:
        da += '<div style="margin-top:6px;font:13px/1.6 Arial;color:%s;">%s</div>' % (INK, "<br>".join("• " + esc(e) for e in extra))
    parts.append(card("Front Desk Activity", da))

    # supplies
    if R["sup_out"] or R["sup_low"]:
        si = ""
        if R["sup_out"]:
            si += '<div style="color:#b1442f;font-weight:600;">Out of stock:</div>' + "".join('<div>• %s</div>' % esc(n) for n, q, u in R["sup_out"])
        if R["sup_low"]:
            si += '<div style="color:%s;font-weight:600;margin-top:6px;">Running low:</div>' % GOLD + "".join('<div>• %s &mdash; %s %s left</div>' % (esc(n), esc(_fmtq(q)), esc(u)) for n, q, u in R["sup_low"])
    else:
        si = '<span style="color:%s;">All supplies stocked ✓</span>' % GREEN
    parts.append(card("Supplies Needing Attention", si))

    # staff
    if R["staff"]:
        mx = max(c for _, c in R["staff"]) or 1
        rows = ""
        for name, c in R["staff"]:
            w = int(round(c * 160.0 / mx))
            rows += ('<tr><td style="padding:4px 12px 4px 0;font:13px Arial;color:%s;white-space:nowrap;">%s</td>'
                     '<td style="padding:4px 0;width:100%%;"><span style="display:inline-block;height:11px;width:%dpx;background:%s;border-radius:3px;vertical-align:middle;"></span>'
                     '<span style="font:12px Arial;color:%s;padding-left:8px;">%d</span></td></tr>') % (INK, esc(name), w, GREEN, MUTE, c)
        staff = ('<div style="font:12px Arial;color:%s;margin-bottom:6px;">%d actions logged this week</div>'
                 '<table cellpadding="0" cellspacing="0" style="width:100%%;">%s</table>') % (MUTE, R["actions_total"], rows)
    else:
        staff = '<span style="color:%s;">No activity logged this week.</span>' % MUTE
    parts.append(card("By Staff Member", staff))

    body = "".join(parts)
    return """<!doctype html><html><body style="margin:0;background:%s;padding:24px 12px;">
<table align="center" width="640" cellpadding="0" cellspacing="0" style="max-width:640px;margin:0 auto;">
<tr><td style="padding:6px 4px 2px;">
  <div style="font:700 24px/1.2 Georgia,serif;color:%s;">Chorus Front Desk &mdash; Weekly Report</div>
  <div style="font:13px Arial;color:%s;padding-top:4px;">30 Otis St &middot; %s</div>
</td></tr>
%s
<tr><td style="padding:16px 4px;font:11px/1.5 Arial;color:%s;">
  Automated report &middot; generated %s PT &middot; covers the last %d days.<br>
  Data pulled live from the Front Desk dashboard. Reply-to goes to %s.
</td></tr>
</table></body></html>""" % (CREAM, NAVY, MUTE, esc(rng), body, MUTE,
                              esc(NOW.strftime("%b %-d, %Y %-I:%M %p")), WINDOW_DAYS, esc(REPORT_FROM))

def _fmtq(q):
    return str(int(q)) if float(q).is_integer() else ("%.1f" % q)

def render_text(R):
    rng = "%s - %s" % (START.strftime("%b %-d"), END.strftime("%b %-d, %Y"))
    L = ["CHORUS FRONT DESK - WEEKLY REPORT", "30 Otis St | %s" % rng, "",
         "MOVES", "  Completed: %d move-ins, %d move-outs" % (R["mv_in"], R["mv_out"]),
         "  Upcoming (7d): %d in, %d out" % (R["up_in"], R["up_out"]), "",
         "ONE-TIME KEYS", "  Issued this week: %d" % R["keys_issued_wk"],
         "  Currently out: %d (overdue >24h: %d)" % (R["keys_out_now"], R["keys_overdue"]), "",
         "FRONT DESK ACTIVITY",
         "  Guests signed in: %d" % R["guests_wk"],
         "  Concierge: %d logged (%d completed, %d open now)" % (R["conc_wk"], R["conc_done"], R["conc_open_now"]),
         "  Amenity bookings: %d" % R["am_wk"],
         "  Security incidents: %d" % R["sec_wk"],
         "  Maintenance: %d opened, %d resolved, %d open now" % (R["mt_opened"], R["mt_resolved"], R["mt_open_now"]), "",
         "SUPPLIES NEEDING ATTENTION"]
    if R["sup_out"]: L += ["  OUT: " + ", ".join(n for n, q, u in R["sup_out"])]
    if R["sup_low"]: L += ["  LOW: " + ", ".join("%s (%s %s)" % (n, _fmtq(q), u) for n, q, u in R["sup_low"])]
    if not (R["sup_out"] or R["sup_low"]): L += ["  All stocked."]
    L += ["", "BY STAFF (this week, %d actions)" % R["actions_total"]]
    L += ["  %s: %d" % (n, c) for n, c in R["staff"]] or ["  (none)"]
    L += ["", "Automated report - generated %s PT - last %d days." % (NOW.strftime("%b %-d %-I:%M %p"), WINDOW_DAYS)]
    return "\n".join(L)

# ---------- send ----------
def main():
    data = fetch_state()
    R = build_report(data)
    html_body = render_html(R)
    text_body = render_text(R)
    subject = "Chorus Front Desk — Weekly Report (%s–%s)" % (START.strftime("%b %-d"), END.strftime("%-d"))

    with open("report.html", "w", encoding="utf-8") as f:
        f.write(html_body)
    print(text_body)
    print("\n[built report.html, %d bytes]" % len(html_body))

    if not GMAIL_PASS:
        print("[GMAIL_APP_PASSWORD not set -> preview only, not sending]")
        return

    msg = MIMEMultipart("alternative")
    msg["Subject"] = subject
    msg["From"] = "Chorus Front Desk <%s>" % REPORT_FROM
    msg["To"] = REPORT_TO
    msg["Reply-To"] = REPORT_FROM
    msg.attach(MIMEText(text_body, "plain", "utf-8"))
    msg.attach(MIMEText(html_body, "html", "utf-8"))

    recipients = [a.strip() for a in REPORT_TO.split(",") if a.strip()]
    with smtplib.SMTP_SSL("smtp.gmail.com", 465, timeout=60) as s:
        s.login(GMAIL_USER, GMAIL_PASS)
        s.sendmail(REPORT_FROM, recipients, msg.as_string())
    print("[sent '%s' to %s]" % (subject, REPORT_TO))

if __name__ == "__main__":
    main()
