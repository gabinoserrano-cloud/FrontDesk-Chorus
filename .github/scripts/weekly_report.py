#!/usr/bin/env python3
"""
Chorus Front Desk - weekly report robot (redesigned).

Pulls the live state from Supabase (read-only), totals the last 7 days AND the
prior 7 days (for week-over-week deltas), and builds a decision-first HTML email:
a TL;DR line, a "needs attention" block, then the supporting detail. Sends from a
Gmail account (app password) to the recipient. Runs weekly on GitHub Actions; also
runnable locally for testing.

Stdlib only - no pip installs. GitHub's Ubuntu runners ship Python, same as this Mac.
Email-safe markup: tables + inline styles, web-safe fonts (Georgia/Arial), Unicode
glyphs (no icon webfonts), explicit light color-scheme.

Config via env vars (all optional except the Gmail password when you want it to send):
  SUPA_URL, SUPA_ANON, SUPA_EMAIL, SUPA_PASS   - default to the (already-public) sync creds
  REPORT_FROM   - sending Gmail address            (default gabinoserrano21@gmail.com)
  REPORT_TO     - recipient(s), comma-separated    (default gserrano@alignrealestate.com)
  GMAIL_USER    - SMTP login (usually == REPORT_FROM)
  GMAIL_APP_PASSWORD - 16-char Google app password. If unset, the report is built and
                       written to report.html but NOT sent (handy for local previews).
  REPORT_DAYS   - window length in days            (default 7)
  DASH_URL      - dashboard link for the CTA button
"""
import os, sys, json, re, html, smtplib, urllib.request, collections, datetime
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText

SUPA_URL  = os.environ.get("SUPA_URL",  "https://adovwmwpvwomljggscge.supabase.co")
SUPA_ANON = os.environ.get("SUPA_ANON", "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJpc3MiOiJzdXBhYmFzZSIsInJlZiI6ImFkb3Z3bXdwdndvbWxqZ2dzY2dlIiwicm9sZSI6ImFub24iLCJpYXQiOjE3NzUyNzkxNjcsImV4cCI6MjA5MDg1NTE2N30.dZgl3jEL1FbBSe_gM3LN9Tc3wGGmsf_Bq6YaKJf4dyk")
SUPA_EMAIL= os.environ.get("SUPA_EMAIL","sync@chorusfd.internal")
SUPA_PASS = os.environ.get("SUPA_PASS", "uM7Jaq1HolIGJaolh3YwRD45LRbpNJ2h")

REPORT_FROM = os.environ.get("REPORT_FROM", "gabinoserrano21@gmail.com")
REPORT_TO   = os.environ.get("REPORT_TO",   "gserrano@alignrealestate.com")
GMAIL_USER  = os.environ.get("GMAIL_USER",  REPORT_FROM)
GMAIL_PASS  = os.environ.get("GMAIL_APP_PASSWORD", "")
WINDOW_DAYS = int(os.environ.get("REPORT_DAYS", "7"))
DASH_URL    = os.environ.get("DASH_URL", "https://gabinoserrano-cloud.github.io/FrontDesk-Chorus/index.html")

NAVY="#0D1B2A"; GREEN="#1A5C3E"; GOLD="#C9A45E"; CREAM="#F7F5F0"; INK="#1c2530"
MUTE="#5f6670"; RED="#b1442f"; REDDK="#8f3625"; AMBER="#854f0b"
REDBG="#f6e3de"; AMBERBG="#faeeda"; GREENBG="#e8f0e3"; GREENDK="#3b6d11"; LINE="#efe9dd"

def die(msg):
    sys.stderr.write("[weekly_report] " + msg + "\n"); sys.exit(1)

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

def pacific_now():
    try:
        from zoneinfo import ZoneInfo
        return datetime.datetime.now(ZoneInfo("America/Los_Angeles"))
    except Exception:
        return datetime.datetime.now(datetime.timezone(datetime.timedelta(hours=-7)))

NOW   = pacific_now()
END   = NOW
START = NOW - datetime.timedelta(days=WINDOW_DAYS)
NOW_MS   = int(NOW.timestamp() * 1000)
START_MS = int(START.timestamp() * 1000)
PREV_MS  = START_MS - WINDOW_DAYS * 86400 * 1000
TODAY    = NOW.date()
START_D  = START.date()
PREV_D   = START_D - datetime.timedelta(days=WINDOW_DAYS)

def in_win_ms(ts):
    try: return START_MS <= int(ts) <= NOW_MS
    except Exception: return False
def in_prev_ms(ts):
    try: return PREV_MS <= int(ts) < START_MS
    except Exception: return False
def parse_ymd(s):
    try: return datetime.datetime.strptime(str(s)[:10], "%Y-%m-%d").date()
    except Exception: return None
def date_in_last(s):
    d = parse_ymd(s); return bool(d and START_D <= d <= TODAY)
def date_in_prev(s):
    d = parse_ymd(s); return bool(d and PREV_D <= d < START_D)
def date_in_next(s, days=7):
    d = parse_ymd(s); return bool(d and TODAY <= d <= TODAY + datetime.timedelta(days=days))
def lst(data, key):
    v = data.get(key); return v if isinstance(v, list) else []

CLOSED = ("resolved", "closed", "completed", "done", "cancelled", "canceled")

def move_keys_ready(m):
    for t in (m.get("tasks") or []):
        if not isinstance(t, dict): continue
        txt = str(t.get("text") or t.get("task") or t.get("label") or t.get("name") or "")
        if re.search(r"unit key", txt, re.I):
            return bool(t.get("done") or t.get("checked") or t.get("complete") or t.get("completed") or t.get("checkedAt"))
    return None
def welcome_sent(m):
    return bool(m.get("welcomeSent") or m.get("welcomeLetterSent"))
def fee_amount(k):
    if k.get("feeWaived"): return 0.0
    f = k.get("fee")
    if isinstance(f, bool): return 25.0 if f else 0.0
    if isinstance(f, (int, float)): return float(f)
    if f: return 25.0
    return 0.0

def build_report(data):
    R = {}

    moves_all = lst(data, "moves") + lst(data, "archivedMoves")
    seen, this_arch, prev_arch = set(), [], []
    for m in moves_all:
        if not isinstance(m, dict): continue
        mid = m.get("id")
        if mid in seen: continue
        seen.add(mid)
        a = m.get("archivedAt") or 0
        if in_win_ms(a): this_arch.append(m)
        elif in_prev_ms(a): prev_arch.append(m)
    R["mv_in"]  = sum(1 for m in this_arch if m.get("type") == "in")
    R["mv_out"] = sum(1 for m in this_arch if m.get("type") == "out")
    R["pv_in"]  = sum(1 for m in prev_arch if m.get("type") == "in")
    R["pv_out"] = sum(1 for m in prev_arch if m.get("type") == "out")
    R["net_occ"] = R["mv_in"] - R["mv_out"]

    arch_ids = {m.get("id") for m in lst(data, "archivedMoves")}
    upcoming = []
    for m in lst(data, "moves"):
        if not isinstance(m, dict) or m.get("archivedAt") or m.get("id") in arch_ids: continue
        anchor = m.get("apptDate") or m.get("date")
        if date_in_next(anchor, 7):
            upcoming.append((parse_ymd(anchor), m))
    upcoming.sort(key=lambda t: (t[0] or TODAY))
    R["up_in"]  = sum(1 for _, m in upcoming if m.get("type") == "in")
    R["up_out"] = sum(1 for _, m in upcoming if m.get("type") == "out")
    rows = []
    for d, m in upcoming[:10]:
        typ = (m.get("type") or "").upper()
        flag = ""
        if m.get("type") == "in":
            ready = move_keys_ready(m)
            if not welcome_sent(m): flag = ("warn", "no welcome letter")
            elif ready is False:    flag = ("warn", "keys not ready")
            elif ready is True:     flag = ("ok", "keys ready")
        rows.append((typ, m.get("unit", "?"), m.get("resident", ""), (d.strftime("%a %b %-d") if d else "?"), flag))
    R["up_rows"] = rows

    ur = lst(data, "unitRecords")
    R["units_total"] = len(ur)
    R["occupied"] = sum(1 for x in ur if isinstance(x, dict) and str(x.get("leaseholder") or "").strip())
    R["vacant"] = R["units_total"] - R["occupied"]
    R["vacancy_pct"] = (100.0 * R["vacant"] / R["units_total"]) if R["units_total"] else 0.0
    R["blueground"] = sum(1 for x in ur if isinstance(x, dict) and x.get("blueground"))
    wifi = []
    for x in ur:
        if not isinstance(x, dict): continue
        d = parse_ymd(x.get("wifiExpiry"))
        if d and TODAY <= d <= TODAY + datetime.timedelta(days=14):
            wifi.append((d, x.get("unit", "?")))
    wifi.sort()
    R["wifi_soon"] = wifi

    keys = lst(data, "keys")
    otk = [k for k in keys if isinstance(k, dict) and k.get("type") == "one-time"]
    R["keys_iss"] = sum(1 for k in otk if in_win_ms(k.get("createdAt") or k.get("issuedAt") or 0))
    R["keys_iss_prev"] = sum(1 for k in otk if in_prev_ms(k.get("createdAt") or k.get("issuedAt") or 0))
    out_otk = [k for k in otk if k.get("status") == "issued"]
    R["keys_out"] = len(out_otk)
    day_ago = NOW_MS - 86400 * 1000
    overdue = [k for k in out_otk if (k.get("issuedAt") or k.get("createdAt") or NOW_MS) < day_ago]
    R["keys_overdue"] = len(overdue)
    oldest_h = 0
    for k in out_otk:
        t = k.get("issuedAt") or k.get("createdAt")
        if t:
            try: oldest_h = max(oldest_h, int((NOW_MS - int(t)) / 3600000))
            except Exception: pass
    R["keys_oldest_h"] = oldest_h
    R["fee_n"] = sum(1 for k in keys if isinstance(k, dict) and in_win_ms(k.get("feeAddedAt") or 0) and fee_amount(k) > 0)
    R["fee_sum"] = sum(fee_amount(k) for k in keys if isinstance(k, dict) and in_win_ms(k.get("feeAddedAt") or 0))

    conc_all = [c for c in lst(data, "concierge") if isinstance(c, dict)]
    conc = [c for c in conc_all if in_win_ms(c.get("createdAt") or 0)]
    R["conc_n"] = len(conc)
    R["conc_prev"] = sum(1 for c in conc_all if in_prev_ms(c.get("createdAt") or 0))
    R["conc_open"] = sum(1 for c in conc_all if str(c.get("status") or "").lower() not in CLOSED)
    R["conc_by_type"] = collections.Counter((c.get("type") or "Other") for c in conc).most_common()
    rep = collections.Counter(str(c.get("unit") or "").strip() for c in conc if str(c.get("unit") or "").strip())
    R["conc_repeat"] = [(u, n) for u, n in rep.most_common() if n >= 2]

    R["guests_n"]    = sum(1 for g in lst(data, "guests") if isinstance(g, dict) and in_win_ms(g.get("createdAt") or 0))
    R["guests_prev"] = sum(1 for g in lst(data, "guests") if isinstance(g, dict) and in_prev_ms(g.get("createdAt") or 0))

    am_all = [a for a in lst(data, "amenities") if isinstance(a, dict)]
    R["am_n"]    = sum(1 for a in am_all if date_in_last(a.get("date")))
    R["am_prev"] = sum(1 for a in am_all if date_in_prev(a.get("date")))
    amn = []
    for a in am_all:
        d = parse_ymd(a.get("date"))
        if d and TODAY <= d <= TODAY + datetime.timedelta(days=7):
            amn.append((d, a.get("space") or a.get("type") or "booking", a.get("type") or ""))
    amn.sort()
    R["am_next"] = amn[:6]

    sec_all = [s for s in lst(data, "security") if isinstance(s, dict)]
    sec = [s for s in sec_all if in_win_ms(s.get("createdAt") or 0) or date_in_last(s.get("date"))]
    R["sec_n"] = len(sec)
    R["sec_prev"] = sum(1 for s in sec_all if in_prev_ms(s.get("createdAt") or 0) or date_in_prev(s.get("date")))
    inc_rows, open_inc = [], []
    for s in sec:
        st = str(s.get("status") or "").lower()
        is_open = st not in CLOSED
        days = ""
        t = s.get("createdAt")
        if t:
            try: days = "%dd" % max(0, int((NOW_MS - int(t)) / 86400000))
            except Exception: days = ""
        row = (s.get("type") or "Incident", s.get("unit") or s.get("location") or "", st or "open", is_open, days)
        inc_rows.append(row)
        if is_open: open_inc.append(row)
    R["inc_rows"] = inc_rows
    R["open_inc"] = open_inc

    mt = [x for x in lst(data, "maintenance") if isinstance(x, dict)]
    R["mt_open"] = sum(1 for x in mt if str(x.get("status", "")).lower() not in CLOSED)
    R["mt_opened"] = sum(1 for x in mt if in_win_ms(x.get("createdAt") or 0))
    R["mt_resolved"] = sum(1 for x in mt if str(x.get("status", "")).lower() in CLOSED and in_win_ms(x.get("resolvedAt") or x.get("updatedAt") or 0))
    aging = []
    for x in mt:
        if str(x.get("status", "")).lower() in CLOSED: continue
        t = x.get("createdAt")
        if not t: continue
        try:
            d = int((NOW_MS - int(t)) / 86400000)
            if d >= 7: aging.append((d, x.get("unit", "?")))
        except Exception: pass
    aging.sort(reverse=True)
    R["mt_aging"] = aging

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
    R["sup_out"] = out; R["sup_low"] = low

    up = data.get("userPassdowns") if isinstance(data.get("userPassdowns"), dict) else {}
    pd_days = set(); pd_logged = 0
    for _uid, plist in up.items():
        if not isinstance(plist, list): continue
        for p in plist:
            if not isinstance(p, dict): continue
            if date_in_last(p.get("date")):
                pd_logged += 1
                pd_days.add(str(p.get("date"))[:10])
    R["pd_logged"] = pd_logged; R["pd_days"] = len(pd_days)

    cnt = collections.Counter()
    for a in lst(data, "activityLog"):
        if isinstance(a, dict) and in_win_ms(a.get("ts") or 0):
            cnt[a.get("by") or "Unknown"] += 1
    R["staff"] = cnt.most_common()
    R["actions_total"] = sum(cnt.values())

    needs = []
    for typ, unit, st, is_open, days in R["open_inc"]:
        d = (" · " + days + " open") if days else ""
        needs.append((RED, "Incident: %s%s" % (typ, (" — Unit %s" % unit) if unit else ""),
                      "Status: %s%s" % (st, d), pill("open", "red"), "Review"))
    if R["keys_overdue"]:
        det = "Oldest out %dh" % R["keys_oldest_h"] if R["keys_oldest_h"] else "Past 24h"
        if R["fee_n"]: det += " · %d at $25 fee" % R["fee_n"]
        needs.append((RED, "%d one-time key%s overdue (>24h)" % (R["keys_overdue"], "" if R["keys_overdue"] == 1 else "s"),
                      det, pill("overdue", "red"), "Front desk"))
    if R["sup_out"]:
        needs.append((RED, "Out of stock — " + ", ".join(n for n, q, u in R["sup_out"]),
                      "Reorder before the next print/mail run", pill("out", "red"), "Order"))
    wl = [u for t, u, r, w, f in R["up_rows"] if t == "IN" and f and f[0] == "warn" and "welcome" in f[1]]
    kn = [u for t, u, r, w, f in R["up_rows"] if t == "IN" and f and f[0] == "warn" and "keys not" in f[1]]
    if wl:
        needs.append((AMBER, "%d upcoming move-in%s missing a welcome letter" % (len(wl), "" if len(wl) == 1 else "s"),
                      "Units " + ", ".join(wl), pill("open", "amber"), "Leasing"))
    if kn:
        needs.append((AMBER, "%d upcoming move-in%s with keys not ready" % (len(kn), "" if len(kn) == 1 else "s"),
                      "Units " + ", ".join(kn), pill("prep", "amber"), "Front desk"))
    if R["mt_aging"]:
        d0, u0 = R["mt_aging"][0]
        needs.append((AMBER, "%d maintenance ticket%s open >7 days" % (len(R["mt_aging"]), "" if len(R["mt_aging"]) == 1 else "s"),
                      "Oldest: Unit %s, %d days" % (u0, d0), pill("aging", "amber"), "Maintenance"))
    R["needs"] = needs
    return R

def esc(x): return html.escape(str(x if x is not None else ""))
def fmtq(q): return str(int(q)) if float(q).is_integer() else ("%.1f" % q)
def money(n): return "$" + format(int(round(n)), ",")

def pill(text, kind):
    bg, fg = {"red": (REDBG, REDDK), "amber": (AMBERBG, AMBER), "green": (GREENBG, GREENDK),
              "gold": (GOLD, NAVY), "gray": ("#eef0f2", INK)}.get(kind, ("#eef0f2", INK))
    return ('<span style="background:%s;color:%s;font:500 12px Arial,sans-serif;'
            'padding:2px 8px;border-radius:6px;white-space:nowrap;">%s</span>') % (bg, fg, esc(text))

def newtag():
    return ('<span style="background:%s;color:%s;font:500 12px Arial;padding:1px 7px;border-radius:6px;'
            'text-transform:none;letter-spacing:0;margin-left:6px;">New</span>') % (GOLD, NAVY)

def delta(cur, prev, sentiment="neutral"):
    d = cur - prev
    if d == 0:
        return '<span style="font:12px Arial,sans-serif;color:%s;">no change vs last wk</span>' % MUTE
    up = d > 0
    arrow = "&#9650;" if up else "&#9660;"
    if sentiment == "up_bad":   col = RED if up else GREENDK
    elif sentiment == "up_good": col = GREENDK if up else RED
    else:                        col = MUTE
    return '<span style="font:12px Arial,sans-serif;color:%s;">%s %d vs last wk</span>' % (col, arrow, abs(d))

def metric(n, label, color=GREEN, sub=""):
    s = ('<table role="presentation" cellpadding="0" cellspacing="0" style="display:inline-table;margin:0 22px 8px 0;vertical-align:top;">'
         '<tr><td style="font:500 24px/1 Arial,sans-serif;color:%s;">%s</td></tr>'
         '<tr><td style="font:12px/1.3 Arial,sans-serif;color:%s;padding-top:4px;">%s</td></tr>') % (color, esc(n), MUTE, esc(label))
    if sub: s += '<tr><td style="padding-top:3px;">%s</td></tr>' % sub
    return s + '</table>'

def card(title, inner, accent=NAVY, tag=False):
    return ('<table role="presentation" width="100%%" cellpadding="0" cellspacing="0" style="border:1px solid #e3ddd0;border-radius:10px;background:#ffffff;margin-bottom:14px;">'
            '<tr><td style="background:%s;color:%s;font:500 12px Arial,sans-serif;letter-spacing:.05em;text-transform:uppercase;padding:9px 14px;border-radius:10px 10px 0 0;">%s%s</td></tr>'
            '<tr><td style="padding:13px 14px;font:14px/1.55 Arial,sans-serif;color:%s;">%s</td></tr>'
            '</table>') % (accent, GOLD, esc(title), (newtag() if tag else ""), INK, inner)

def cta(url, label):
    return ('<table role="presentation" cellpadding="0" cellspacing="0" style="margin:0 0 14px;"><tr>'
            '<td bgcolor="%s" style="border-radius:8px;">'
            '<a href="%s" style="display:inline-block;padding:12px 22px;font:500 14px Arial,sans-serif;color:#ffffff;text-decoration:none;border-radius:8px;">%s &#8594;</a>'
            '</td></tr></table>') % (GREEN, esc(url), esc(label))

def render_html(R):
    rng = "%s – %s" % (START.strftime("%b %-d"), END.strftime("%b %-d, %Y"))
    P = []

    # TL;DR
    occ = "%.1f%%" % (100.0 - R["vacancy_pct"]) if R["units_total"] else "n/a"
    tl1 = "%d move-in%s, %d move-out%s · occupancy %s." % (
        R["mv_in"], "" if R["mv_in"] == 1 else "s", R["mv_out"], "" if R["mv_out"] == 1 else "s", occ)
    nn = len(R["needs"])
    tl2 = ("%d item%s need attention%s." % (nn, "" if nn == 1 else "s",
           (", including %d open incident" % len(R["open_inc"])) if R["open_inc"] else "")) if nn else "Nothing needs your attention this week. ✓"
    P.append('<table role="presentation" width="100%%" cellpadding="0" cellspacing="0" style="background:%s;border-radius:10px;margin-bottom:14px;"><tr><td style="padding:13px 15px;">'
             '<div style="font:500 15px/1.4 Arial,sans-serif;color:#ffffff;">%s</div>'
             '<div style="font:13px/1.4 Arial,sans-serif;color:%s;margin-top:4px;">%s</div>'
             '</td></tr></table>' % (NAVY, esc(tl1), GOLD, esc(tl2)))

    P.append(cta(DASH_URL, "Open the dashboard"))

    # NEEDS ATTENTION
    if R["needs"]:
        body = '<table role="presentation" width="100%" cellpadding="0" cellspacing="0">'
        for i, (bar, title, det, tag_html, owner) in enumerate(R["needs"]):
            br = "" if i == len(R["needs"]) - 1 else ("border-bottom:0.5px solid %s;" % LINE)
            body += ('<tr><td width="4" style="background:%s;font-size:1px;line-height:1px;">&nbsp;</td>'
                     '<td style="padding:9px 12px;%s"><div style="font:500 14px Arial,sans-serif;color:%s;">%s</div>'
                     '<div style="font:13px Arial,sans-serif;color:%s;margin-top:2px;">%s</div></td>'
                     '<td style="padding:9px 12px;%stext-align:right;white-space:nowrap;vertical-align:top;">%s'
                     '<div style="font:12px Arial,sans-serif;color:%s;margin-top:4px;">%s</div></td></tr>'
                     ) % (bar, br, INK, esc(title), MUTE, esc(det), br, tag_html, MUTE, esc(owner))
        body += '</table>'
        P.append(card("Needs attention", body, accent=REDDK, tag=True))
    else:
        P.append(card("Needs attention", '<span style="color:%s;">Nothing needs your attention this week. ✓</span>' % GREENDK, accent=REDDK, tag=True))

    # OCCUPANCY
    occ_body = (metric(R["occupied"], "Occupied", GREEN) + metric(R["vacant"], "No leaseholder", AMBER)
                + metric(("+%d" % R["net_occ"]) if R["net_occ"] >= 0 else str(R["net_occ"]), "Net this week", NAVY)
                + metric("%.1f%%" % R["vacancy_pct"], "Vacancy", MUTE)
                + metric(R["blueground"], "Blueground", NAVY)
                + '<div style="font:12px Arial,sans-serif;color:%s;margin-top:2px;">Basis: %d unit records; "occupied" = leaseholder on file.</div>' % (MUTE, R["units_total"]))
    P.append(card("Occupancy", occ_body, tag=True))

    # MOVES
    mv = (metric(R["mv_in"], "Move-ins done", GREEN, delta(R["mv_in"], R["pv_in"]))
          + metric(R["mv_out"], "Move-outs done", GREEN, delta(R["mv_out"], R["pv_out"]))
          + metric("%d / %d" % (R["up_in"], R["up_out"]), "Upcoming in / out (7d)", NAVY))
    if R["up_rows"]:
        mv += '<div style="font:12px Arial,sans-serif;color:%s;margin:6px 0 4px;">Coming up</div>' % MUTE
        mv += '<table role="presentation" cellpadding="0" cellspacing="0" style="font:13px Arial,sans-serif;">'
        for typ, unit, res, when, flag in R["up_rows"]:
            tcol = GREEN if typ == "IN" else GOLD
            fl = ""
            if flag:
                kind = "green" if flag[0] == "ok" else "amber"
                fl = " &nbsp;" + pill(flag[1], kind)
            mv += ('<tr><td style="padding:3px 10px 3px 0;color:%s;font-weight:500;">%s</td>'
                   '<td style="padding:3px 12px 3px 0;">Unit %s</td>'
                   '<td style="padding:3px 12px 3px 0;color:%s;">%s</td>'
                   '<td style="padding:3px 0;color:%s;">%s%s</td></tr>'
                   ) % (tcol, esc(typ), esc(unit), INK, esc(res), MUTE, esc(when), fl)
        mv += '</table>'
    P.append(card("Moves", mv))

    # INCIDENTS
    if R["inc_rows"]:
        ib = '<div style="margin-bottom:9px;"><span style="font:500 18px Arial,sans-serif;color:%s;">%d</span> <span style="font:13px Arial;color:%s;">incident%s this week</span> &nbsp;%s</div>' % (
            RED if R["sec_n"] else GREEN, R["sec_n"], "" if R["sec_n"] == 1 else "s", MUTE, delta(R["sec_n"], R["sec_prev"], "up_bad"))
        ib += '<table role="presentation" width="100%" cellpadding="0" cellspacing="0" style="font:13px Arial,sans-serif;">'
        for i, (typ, unit, st, is_open, days) in enumerate(R["inc_rows"]):
            br = "" if i == len(R["inc_rows"]) - 1 else ("border-bottom:0.5px solid %s;" % LINE)
            label = (unit and ("%s — Unit %s" % (typ, unit))) or typ
            tag_html = pill(("open" if is_open else st) + (" · " + days if (is_open and days) else ""), "amber" if is_open else "green")
            ib += '<tr><td style="padding:5px 0;%s">%s</td><td style="padding:5px 0;%stext-align:right;">%s</td></tr>' % (br, esc(label), br, tag_html)
        ib += '</table>'
    else:
        ib = '<span style="color:%s;">No incidents logged this week. ✓</span>' % GREENDK
    P.append(card("Security incidents", ib, tag=True))

    # FRONT DESK ACTIVITY (guests + concierge + amenities)
    cb = (metric(R["guests_n"], "Guests signed in", GREEN, delta(R["guests_n"], R["guests_prev"]))
          + metric(R["conc_n"], "Concierge requests", GREEN, delta(R["conc_n"], R["conc_prev"]))
          + metric(R["am_n"], "Amenity bookings", GREEN, delta(R["am_n"], R["am_prev"])))
    if R["conc_open"]:
        cb += '<div style="font:13px Arial,sans-serif;color:%s;margin:2px 0 6px;">%d concierge request%s still open.</div>' % (
            MUTE, R["conc_open"], "" if R["conc_open"] == 1 else "s")
    if R["conc_by_type"]:
        cb += '<div style="margin-top:6px;">' + " ".join(
            '<span style="display:inline-block;background:#eef0f2;color:%s;font:13px Arial;padding:2px 9px;border-radius:6px;margin:0 5px 5px 0;">%s %d</span>' % (INK, esc(t), n)
            for t, n in R["conc_by_type"]) + '</div>'
    if R["conc_repeat"]:
        cb += '<div style="font:13px Arial,sans-serif;color:%s;background:#f7f3ea;border:1px solid #ece2cd;border-radius:7px;padding:7px 10px;margin-top:6px;">Repeat units: %s</div>' % (
            INK, ", ".join("%s (×%d)" % (u, n) for u, n in R["conc_repeat"]))
    P.append(card("Front desk activity", cb, tag=True))

    # KEYS + MAINTENANCE
    kb = (metric(R["keys_iss"], "Keys issued", GREEN, delta(R["keys_iss"], R["keys_iss_prev"]))
          + metric(R["keys_out"], "Out now", AMBER if R["keys_out"] else GREEN)
          + metric(R["keys_overdue"], "Overdue >24h", RED if R["keys_overdue"] else GREEN)
          + metric(money(R["fee_sum"]), "Late fees logged", NAVY))
    mt_line = "Maintenance: %d opened · %d resolved · %d open now" % (R["mt_opened"], R["mt_resolved"], R["mt_open"])
    if R["mt_aging"]:
        d0, u0 = R["mt_aging"][0]
        mt_line += '<br><span style="color:%s;">%d open &gt;7 days</span> — oldest Unit %s, %d days' % (RED, len(R["mt_aging"]), u0, d0)
    elif not R["mt_open"]:
        mt_line = "No maintenance tickets open. ✓"
    kb += '<div style="border-top:0.5px solid %s;padding-top:10px;margin-top:2px;font:13px/1.6 Arial,sans-serif;color:%s;">%s</div>' % (LINE, INK, mt_line)
    P.append(card("One-time keys & maintenance", kb))

    # WEEK AHEAD
    wa = []
    prep = [(u, w) for t, u, r, w, f in R["up_rows"] if t == "IN" and f and f[0] == "warn"]
    if prep:
        wa.append("Move-ins needing prep: " + ", ".join("Unit %s (%s)" % (u, w) for u, w in prep[:5]))
    if R["wifi_soon"]:
        wa.append("WiFi expiring within 2 weeks: " + ", ".join("Unit %s (%s)" % (u, d.strftime("%b %-d")) for d, u in R["wifi_soon"][:6]))
    if R["am_next"]:
        wa.append("Amenity bookings: " + ", ".join("%s %s" % (sp, d.strftime("%a")) for d, sp, ty in R["am_next"]))
    if wa:
        P.append(card("Week ahead", "<br>".join("• " + esc(x) for x in wa), tag=True))

    # SUPPLIES
    if R["sup_out"] or R["sup_low"]:
        si = ""
        if R["sup_out"]:
            si += '<div style="color:%s;font-weight:500;">Out of stock</div>' % RED + "".join('<div>• %s</div>' % esc(n) for n, q, u in R["sup_out"])
        if R["sup_low"]:
            si += '<div style="color:%s;font-weight:500;margin-top:6px;">Running low</div>' % AMBER + "".join('<div>• %s — %s %s left</div>' % (esc(n), esc(fmtq(q)), esc(u)) for n, q, u in R["sup_low"])
    else:
        si = '<span style="color:%s;">All supplies stocked. ✓</span>' % GREENDK
    P.append(card("Supplies", si))

    # STAFF + PASSDOWNS
    pd_col = GREENDK if R["pd_days"] >= 7 else AMBER
    sb = '<div style="font:13px Arial,sans-serif;color:%s;margin-bottom:10px;">Passdowns logged: <span style="color:%s;font-weight:500;">%d</span>, covering %d of the last 7 days. %s</div>' % (
        INK, pd_col, R["pd_logged"], R["pd_days"], newtag())
    if R["staff"]:
        mx = max(c for _, c in R["staff"]) or 1
        sb += '<table role="presentation" cellpadding="0" cellspacing="0" style="width:100%;">'
        for name, c in R["staff"]:
            w = max(4, int(round(c * 180.0 / mx)))
            sb += ('<tr><td style="padding:4px 12px 4px 0;font:13px Arial,sans-serif;color:%s;white-space:nowrap;">%s</td>'
                   '<td style="padding:4px 0;"><span style="display:inline-block;height:11px;width:%dpx;background:%s;border-radius:3px;vertical-align:middle;"></span>'
                   '<span style="font:12px Arial,sans-serif;color:%s;padding-left:8px;">%d</span></td></tr>') % (INK, esc(name), w, GREEN, MUTE, c)
        sb += '</table><div style="font:12px Arial,sans-serif;color:%s;margin-top:8px;">Volume of logged actions — not a measure of quality.</div>' % MUTE
    else:
        sb += '<span style="color:%s;">No activity logged this week.</span>' % MUTE
    P.append(card("By staff member", sb))

    return ("""<!doctype html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<meta name="color-scheme" content="light"><meta name="supported-color-schemes" content="light">
</head><body style="margin:0;background:%s;padding:24px 12px;-webkit-text-size-adjust:100%%;">
<table role="presentation" align="center" width="640" cellpadding="0" cellspacing="0" style="max-width:640px;margin:0 auto;">
<tr><td style="padding:6px 4px 12px;">
  <div style="font:500 21px/1.2 Georgia,'Times New Roman',serif;color:%s;">Chorus Front Desk &mdash; Weekly Report</div>
  <div style="font:13px Arial,sans-serif;color:%s;padding-top:4px;">30 Otis St &middot; %s</div>
</td></tr>
<tr><td>%s</td></tr>
<tr><td style="padding:8px 4px;font:12px/1.5 Arial,sans-serif;color:%s;">
  Automated report &middot; generated %s PT &middot; covers the last %d days. Data pulled live from the Front Desk dashboard.
</td></tr>
</table></body></html>""") % (CREAM, NAVY, MUTE, esc(rng), "".join(P), MUTE,
                              esc(NOW.strftime("%b %-d, %Y %-I:%M %p")), WINDOW_DAYS)

def render_text(R):
    rng = "%s - %s" % (START.strftime("%b %-d"), END.strftime("%b %-d, %Y"))
    occ = "%.1f%%" % (100.0 - R["vacancy_pct"]) if R["units_total"] else "n/a"
    L = ["CHORUS FRONT DESK - WEEKLY REPORT", "30 Otis St | %s" % rng, "",
         "TL;DR: %d move-ins, %d move-outs, occupancy %s." % (R["mv_in"], R["mv_out"], occ),
         "Dashboard: %s" % DASH_URL, ""]
    L.append("NEEDS ATTENTION (%d)" % len(R["needs"]))
    if R["needs"]:
        for bar, title, det, tag_html, owner in R["needs"]:
            L.append("  - %s [%s] (%s)" % (title, det, owner))
    else:
        L.append("  Nothing needs your attention this week.")
    L += ["", "OCCUPANCY",
          "  %d occupied, %d without leaseholder, %.1f%% vacancy (net %+d this week)" % (R["occupied"], R["vacant"], R["vacancy_pct"], R["net_occ"]),
          "", "MOVES",
          "  Completed: %d in (%+d), %d out (%+d)" % (R["mv_in"], R["mv_in"] - R["pv_in"], R["mv_out"], R["mv_out"] - R["pv_out"]),
          "  Upcoming 7d: %d in, %d out" % (R["up_in"], R["up_out"]), "",
          "SECURITY INCIDENTS: %d this week (%+d)" % (R["sec_n"], R["sec_n"] - R["sec_prev"])]
    for typ, unit, st, is_open, days in R["inc_rows"]:
        L.append("  - %s%s [%s%s]" % (typ, (" Unit %s" % unit) if unit else "", st, (" " + days) if (is_open and days) else ""))
    L += ["", "FRONT DESK ACTIVITY",
          "  Guests signed in: %d (%+d)" % (R["guests_n"], R["guests_n"] - R["guests_prev"]),
          "  Concierge: %d requests (%+d), %d open now" % (R["conc_n"], R["conc_n"] - R["conc_prev"], R["conc_open"]),
          "  Amenity bookings: %d (%+d)" % (R["am_n"], R["am_n"] - R["am_prev"])]
    if R["conc_by_type"]: L.append("  Concierge types: " + ", ".join("%s %d" % (t, n) for t, n in R["conc_by_type"]))
    if R["conc_repeat"]: L.append("  Repeat units: " + ", ".join("%s x%d" % (u, n) for u, n in R["conc_repeat"]))
    L += ["", "ONE-TIME KEYS",
          "  Issued: %d (%+d) | Out now: %d | Overdue >24h: %d | Late fees logged: %s" % (
              R["keys_iss"], R["keys_iss"] - R["keys_iss_prev"], R["keys_out"], R["keys_overdue"], money(R["fee_sum"])),
          "  Maintenance: %d opened, %d resolved, %d open now" % (R["mt_opened"], R["mt_resolved"], R["mt_open"]), ""]
    wa = []
    prep = [(u, w) for t, u, r, w, f in R["up_rows"] if t == "IN" and f and f[0] == "warn"]
    if prep: wa.append("Move-ins needing prep: " + ", ".join("Unit %s" % u for u, w in prep[:5]))
    if R["wifi_soon"]: wa.append("WiFi expiring 2wk: " + ", ".join("Unit %s" % u for d, u in R["wifi_soon"][:6]))
    if R["am_next"]: wa.append("Amenity bookings: " + ", ".join("%s %s" % (sp, d.strftime("%a")) for d, sp, ty in R["am_next"]))
    if wa: L += ["WEEK AHEAD"] + ["  - " + x for x in wa] + [""]
    L.append("SUPPLIES")
    if R["sup_out"]: L.append("  OUT: " + ", ".join(n for n, q, u in R["sup_out"]))
    if R["sup_low"]: L.append("  LOW: " + ", ".join("%s (%s %s)" % (n, fmtq(q), u) for n, q, u in R["sup_low"]))
    if not (R["sup_out"] or R["sup_low"]): L.append("  All stocked.")
    L += ["", "BY STAFF (%d actions; passdowns logged %d, covering %d of last 7 days)" % (R["actions_total"], R["pd_logged"], R["pd_days"])]
    L += ["  %s: %d" % (n, c) for n, c in R["staff"]] or ["  (none)"]
    L += ["", "Automated report - generated %s PT - last %d days." % (NOW.strftime("%b %-d %-I:%M %p"), WINDOW_DAYS)]
    return "\n".join(L)

def main():
    data = fetch_state()
    R = build_report(data)
    html_body = render_html(R)
    text_body = render_text(R)
    nn = len(R["needs"])
    extra = (" - %d need attention" % nn) if nn else (" - toner/supplies out" if R["sup_out"] else "")
    subject = "Chorus Weekly - %d in / %d out%s (%s-%s)" % (
        R["mv_in"], R["mv_out"], extra, START.strftime("%b %-d"), END.strftime("%-d"))

    with open("report.html", "w", encoding="utf-8") as f:
        f.write(html_body)
    print(text_body)
    print("\n[built report.html, %d bytes; subject: %s]" % (len(html_body), subject))

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
