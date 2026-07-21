
#!/usr/bin/env python3
"""local_finder.py — cloud version. Runs on GitHub Actions, emails you at 8am MT."""

import feedparser, json, re, os, smtplib, sys
from email.message import EmailMessage
from datetime import datetime, timezone, timedelta

MT = timezone(timedelta(hours=-6))   # Mountain Time
def now_mt(): return datetime.now(MT)

# ---------------------------------------------------------------- CONFIG
CITY    = "Ogden"
NEARBY  = ["ogden","layton","roy","clearfield","bountiful","salt lake","slc",
           "weber","davis county","utah","ut","online","nationwide"]
EXCLUDE = ["uk","canada","australia","eu only","europe"]

DATA_DIR   = "freebie_data"
SEEN_FILE  = os.path.join(DATA_DIR, "seen.json")
CLAIM_FILE = os.path.join(DATA_DIR, "claimed.json")
REPORT     = os.path.join(DATA_DIR, "report.txt")

RSS_SOURCES = {
    "r/freebies":    "https://www.reddit.com/r/freebies/new/.rss",
    "r/free":        "https://www.reddit.com/r/free/new/.rss",
    "r/sweepstakes": "https://www.reddit.com/r/sweepstakes/new/.rss",
    "r/giveaways":   "https://www.reddit.com/r/giveaways/new/.rss",
    "r/Utah":        "https://www.reddit.com/r/Utah/new/.rss",
    "r/Ogden":       "https://www.reddit.com/r/ogden/new/.rss",
    "r/WeberState":  "https://www.reddit.com/r/weberstate/new/.rss",
    "SlickDeals":    "https://slickdeals.net/newsearch.php?mode=frontpage&searcharea=deals&searchin=first&rss=1",
    "WSU Events":    "https://www.weber.edu/events/rss.xml",
}

KEYWORDS = {
    "food":   ["free meal","free food","free coffee","free pizza","free lunch","free breakfast",
               "free donut","free dinner","refreshments","light lunch","snacks provided",
               "food provided","free samples","bogo","kids eat free","grand opening",
               "free burrito","free sandwich","free ice cream","free tacos","catered"],
    "raffle": ["giveaway","raffle","sweepstakes","enter to win","contest","drawing","door prize"],
    "deal":   ["free shipping","100% off","free sample","free trial","$0","free admission",
               "no cost","free entry","free class","free event"],
}

RECURRING = [
    ("Ibotta",           "Check 'Free Item' rebates — often 1-3 free groceries/week"),
    ("Fetch Rewards",    "Scan every receipt"),
    ("Too Good To Go",   "Check Ogden bags after 5pm — $4-6 for $20 of food"),
    ("Flashfood",        "Smith's/Kroger markdowns, 50-70% off"),
    ("Buy Nothing",      "Your Ogden Buy Nothing FB group"),
    ("Craigslist Free",  "https://saltlakecity.craigslist.org/search/zip"),
    ("Birthday rewards", "Starbucks/Chipotle/Dunkin/Denny's if birthday month"),
    ("PINCHme",          "New sample box drops 1st Tuesday monthly"),
]

# ---------------------------------------------------------------- STORAGE
def _load(path, default):
    try:
        with open(path) as f: return json.load(f)
    except Exception: return default

def _save(path, data):
    os.makedirs(DATA_DIR, exist_ok=True)
    with open(path, "w") as f: json.dump(data, f, indent=2)

# ---------------------------------------------------------------- FILTERS
def categorize(text):
    t = text.lower()
    for cat, words in KEYWORDS.items():
        if any(w in t for w in words): return cat
    return None

def is_local(text):
    t = text.lower()
    if any(x in t for x in EXCLUDE): return False
    return any(n in t for n in NEARBY)

def estimate_value(text):
    m = re.findall(r"\$(\d+(?:\.\d{1,2})?)", text)
    return max((float(x) for x in m), default=0.0)

def find_deadline(text):
    t = text.lower()
    if any(w in t for w in ["today","tonight","ends today","last day","expires today"]): return 0
    if any(w in t for w in ["tomorrow","ends tomorrow"]): return 1
    m = re.search(r"end[s]?\s+(\d{1,2})/(\d{1,2})", t)
    if m:
        try:
            n = now_mt()
            d = datetime(n.year, int(m.group(1)), int(m.group(2)), tzinfo=MT)
            if d < n: d = d.replace(year=n.year + 1)
            return (d - n).days
        except ValueError: pass
    m = re.search(r"\b(\d{1,2})\s*(day|hour)s?\s*left", t)
    if m: return int(m.group(1)) if m.group(2) == "day" else 0
    return None

# ---------------------------------------------------------------- SCAN
def scan():
    seen, found = set(_load(SEEN_FILE, [])), []
    for name, url in RSS_SOURCES.items():
        try:
            feed = feedparser.parse(url, agent="Mozilla/5.0 (local-finder)")
        except Exception:
            continue
        for e in feed.entries:
            uid = e.get("id") or e.get("link")
            if not uid or uid in seen: continue
            title = (e.get("title") or "").strip()
            body  = re.sub(r"<[^>]+>", " ", e.get("summary",""))[:400]
            blob  = f"{title} {body}"
            cat = categorize(blob)
            if not cat: continue

            local_src = any(k in name for k in ["Ogden","Utah","WSU","Weber"])
            local = local_src or is_local(blob)
            if not local and not local_src:
                if re.search(r"\b(nyc|chicago|austin|seattle|boston|denver|dallas|"
                             r"portland|atlanta|miami|phoenix)\b", blob.lower()):
                    continue

            seen.add(uid)
            found.append({"source":name,"category":cat,"title":title[:140],
                          "link":e.get("link",""),"value":estimate_value(blob),
                          "local":local,"days_left":find_deadline(blob)})
    _save(SEEN_FILE, sorted(seen)[-4000:])
    return found

# ---------------------------------------------------------------- REPORT
def build_report(items):
    n = now_mt()
    out = [f"FREEBIE REPORT — {n.strftime('%a %b %d, %I:%M %p')} MT", "="*46, ""]
    out += sweeps_section()

    urgent = [i for i in items if i["days_left"] is not None and i["days_left"] <= 1]
    if urgent:
        out.append(f"!! EXPIRING NOW ({len(urgent)})")
        for i in urgent:
            out += [f"   • {i['title']}", f"     {i['link']}"]
        out.append("")

    local = [i for i in items if i["local"] and i not in urgent]
    if local:
        out.append(f"NEAR {CITY.upper()} ({len(local)})")
        for i in local:
            v = f" [~${i['value']:.0f}]" if i["value"] else ""
            out += [f"   • [{i['category']}] {i['title']}{v}", f"     {i['link']}"]
        out.append("")

    rest = [i for i in items if not i["local"] and i not in urgent]
    for cat, label in [("food","FOOD"),("raffle","RAFFLES"),("deal","DEALS / FREE STUFF")]:
        g = [i for i in rest if i["category"] == cat]
        if not g: continue
        out.append(f"{label} — online/shippable ({len(g)})")
        for i in g[:12]:
            v = f" [~${i['value']:.0f}]" if i["value"] else ""
            out += [f"   • {i['title']}{v}", f"     {i['link']}"]
        out.append("")

    if not items: out.append("No new matches this run.\n")

    out.append("CHECK MANUALLY (no feeds)")
    for name, tip in RECURRING: out.append(f"   • {name}: {tip}")
    out.append("")

    claimed = _load(CLAIM_FILE, [])
    out.append("ACTUAL EARNINGS")
    if claimed:
        total = sum(c["value"] for c in claimed)
        month = [c for c in claimed if c["date"][:7] == n.strftime("%Y-%m")]
        out.append(f"   This month: ${sum(c['value'] for c in month):.2f} / {len(month)} items")
        out.append(f"   All time:   ${total:.2f} / {len(claimed)} items")
        for c in claimed[-5:]:
            out.append(f"     {c['date'][:10]}  ${c['value']:>6.2f}  {c['item']}")
    else:
        out.append("   Nothing logged yet — edit freebie_data/claimed.json to log wins.")
    return "\n".join(out)

# ---------------------------------------------------------------- SWEEPS
SWEEPS_FILE = os.path.join(DATA_DIR, "sweeps.json")

def sweeps_section():
    sweeps = _load(SWEEPS_FILE, [])
    if not sweeps: return []
    n, live = now_mt(), []
    for s in sweeps:
        try:
            end = datetime.strptime(s["ends"], "%Y-%m-%d").replace(tzinfo=MT)
        except Exception:
            continue
        days = (end - n).days
        if days < 0: continue
        ev = s.get("prize_value", 0) / max(s.get("est_entrants", 1), 1)
        live.append({**s, "days": days, "ev": ev})

    live.sort(key=lambda s: (-s["ev"], s["days"]))
    out = []

    closing = [s for s in live if s["days"] <= 2]
    if closing:
        out.append(f"CLOSING SOON ({len(closing)})")
        for s in closing:
            d = "TODAY" if s["days"] == 0 else f"{s['days']}d left"
            out += [f"   • {s['name']} — {d}", f"     {s['url']}"]
        out.append("")

    daily = [s for s in live if s.get("daily") and s not in closing]
    if daily:
        out.append(f"ENTER TODAY — daily resets ({len(daily)})")
        for s in daily:
            out += [f"   • {s['name']}  [EV ${s['ev']:.3f}/entry]", f"     {s['url']}"]
        out.append("")

    rest = [s for s in live if s not in closing and s not in daily]
    if rest:
        out.append(f"ONE-TIME ENTRY — open ({len(rest)})")
        for s in rest[:10]:
            out += [f"   • {s['name']} — {s['days']}d left  [EV ${s['ev']:.3f}]",
                    f"     {s['url']}"]
        out.append("")
    return out

# ---------------------------------------------------------------- EMAIL
def send_email(body):
    addr, pw = os.environ.get("FREEBIE_EMAIL"), os.environ.get("FREEBIE_PASS")
    if not (addr and pw):
        print("Email not configured."); return
    subj = "Freebie Report" + (" — EXPIRING TODAY" if "!! EXPIRING" in body else "")
    msg = EmailMessage()
    msg["Subject"], msg["From"], msg["To"] = subj, addr, addr
    msg.set_content(body)
    try:
        with smtplib.SMTP_SSL("smtp.gmail.com", 465) as s:
            s.login(addr, pw.replace(" ", "")); s.send_message(msg)
        print("Email sent.")
    except Exception as e:
        print(f"Email failed: {e}")

# ---------------------------------------------------------------- MAIN
if __name__ == "__main__":
    if len(sys.argv) > 3 and sys.argv[1] == "claim":
        log = _load(CLAIM_FILE, [])
        log.append({"date": now_mt().isoformat(), "item": sys.argv[2], "value": float(sys.argv[3])})
        _save(CLAIM_FILE, log)
        print(f"Logged. Lifetime: ${sum(c['value'] for c in log):.2f}")
    else:
        text = build_report(scan())
        print(text)
        os.makedirs(DATA_DIR, exist_ok=True)
        with open(REPORT, "w", encoding="utf-8") as f: f.write(text)
        send_email(text)
