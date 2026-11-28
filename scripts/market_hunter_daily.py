#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Market Hunter — خط تولید قطعی روزانه (بدون هیچ فراخوانی مدل زبانی)

چه کاری می‌کند:
  ۱) از چند منبع مستقل، آگهی‌های فعال را می‌خواند (JSON ساختاریافته + HTML عمومی)
  ۲) هر رکورد را با اثرانگشت «شماره اعلان» با داده قبلی ادغام می‌کند (بدون تکرار)
  ۳) با قواعد ثابت (همان وزن‌های ۲۰/۲۰/۲۰/۱۵/۱۰/۱۰/۵) امتیاز می‌دهد
  ۴) data.json را روی شاخه gh-pages می‌نویسد؛ صفحه فقط همین فایل را می‌خواند

اجرا:  python3 scripts/market_hunter_daily.py            (تولید data.json محلی)
       GITHUB_TOKEN=... REPO=owner/repo python3 ...      (نوشتن روی gh-pages)
"""
import os
import re
import json
import sys
import gzip
import io
import datetime
import urllib.request
import urllib.error
import urllib.parse

BRANCH = "gh-pages"
DATA_PATH = "data.json"
UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/124.0 Safari/537.36")

# ---------------------------------------------------------------- منابع
MOZ_CATS = [
    ("food-industry", "مواد غذایی"),
    ("livestock-farming-industry", "دام و نهاده"),
    ("agriculture-industry", "کشاورزی"),
    ("packaging-services", "بسته‌بندی",
     ),
    ("oil-gas-petrochemical", "نفت و گاز (کترینگ)"),
    ("cleaning-hygiene-supplies", "شوینده و بهداشتی"),
]

# مناطقی که از IP دیتاسنتر معمولاً بسته‌اند؛ اسکریپت تلاش می‌کند و نتیجه را شفاف ثبت می‌کند
PROBE_SOURCES = [
    ("setadiran.ir — سامانه ستاد (منبع رسمی خرید دولتی)", "https://setadiran.ir/"),
    ("parsnamaddata.com — آینه فهرست ستاد", "https://www.parsnamaddata.com/"),
    ("ariatender.net — تجمیع‌کننده", "https://ariatender.net/"),
]

FOOD_TERMS = ["غذا", "غذایی", "طبخ", "پخت", "کترینگ", "سلف", "رستوران", "مواد اولیه",
              "برنج", "گوشت", "مرغ", "آجیل", "سبد کالا", "سبد معیشتی", "کارتن",
              "لبنی", "شیر", "ماست", "پنیر", "کره", "روغن", "قند", "چای", "خشکبار",
              "کنسانتره", "آب‌پنیر", "بسته‌بندی", "سردخانه", "آرد", "حبوبات"]
EQUIP_TERMS = ["خط تولید", "ماشین‌آلات", "ماشین آلات", "تجهیزات", "خط فرآوری",
               "دستگاه", "سردخانه", "بسته‌بندی"]
BIG_TERMS = ["سالانه", "شش‌ماهه", "شش ماهه", "سال ۱۴۰۵", "سال ۱۴۰۶", "ماه مبارک",
             "تن ", "هزار تن", "روزانه", "دانشجویی", "بیماران", "پرسنل"]

NOTICE_RE = re.compile(r"(ETEND[-A-Z0-9]*\d{8,}|\b\d{16}\b|\b\d{11,16}\b)")

W = {"source": 20, "fit": 20, "prob": 20, "money": 15, "urg": 10, "comp": 10, "complete": 5}


# ---------------------------------------------------------------- ابزار
def http_get(url, timeout=25):
    req = urllib.request.Request(url, headers={
        "User-Agent": UA,
        "Accept-Language": "fa-IR,fa;q=0.9",
        "Accept": "text/html,application/json,*/*",
    })
    with urllib.request.urlopen(req, timeout=timeout) as r:
        raw = r.read()
        if r.headers.get("Content-Encoding") == "gzip":
            raw = gzip.GzipFile(fileobj=io.BytesIO(raw)).read()
        return raw.decode("utf-8", "replace")


def jalali(d):
    """تبدیل میلادی به شمسی (الگوریتم استاندارد، بدون کتابخانه خارجی)."""
    gy, gm, gd = d.year, d.month, d.day
    g_d_m = [0, 31, 59, 90, 120, 151, 181, 212, 243, 273, 304, 334]
    gy2 = gy - 1600
    gm2 = gm - 1
    gd2 = gd - 1
    g_day_no = 365 * gy2 + (gy2 + 3) // 4 - (gy2 + 99) // 100 + (gy2 + 399) // 400
    g_day_no += g_d_m[gm2] + gd2
    if gm > 2 and ((gy % 4 == 0 and gy % 100 != 0) or gy % 400 == 0):
        g_day_no += 1
    j_day_no = g_day_no - 79
    j_np = j_day_no // 12053
    j_day_no %= 12053
    jy = 979 + 33 * j_np + 4 * (j_day_no // 1461)
    j_day_no %= 1461
    if j_day_no >= 366:
        jy += (j_day_no - 366) // 365 + 1
        j_day_no = (j_day_no - 366) % 365
    jm = 1
    for i in range(11):
        md = 31 if i < 6 else 30
        if j_day_no < md:
            break
        j_day_no -= md
        jm += 1
    jd = j_day_no + 1
    return jy, jm, jd


MONTHS = ["فروردین", "اردیبهشت", "خرداد", "تیر", "مرداد", "شهریور",
          "مهر", "آبان", "آذر", "دی", "بهمن", "اسفند"]


def stamp_fa(dt):
    jy, jm, jd = jalali(dt)
    return "%d %s %d – ساعت %s تهران" % (jd, MONTHS[jm - 1], jy, dt.strftime("%H:%M"))


def jdate(iso):
    try:
        d = datetime.date.fromisoformat(str(iso)[:10])
    except Exception:
        return ""
    jy, jm, jd = jalali(d)
    return "%04d/%02d/%02d" % (jy, jm, jd)


def norm_title(t):
    t = re.sub(r"[\u200c\u200f\u200e]", " ", t or "")
    t = re.sub(r"[إأٱآ]", "ا", t)
    t = re.sub(r"ي", "ی", t)
    t = re.sub(r"ك", "ک", t)
    t = re.sub(r"[^\w\u0600-\u06FF]+", " ", t)
    return re.sub(r"\s+", " ", t).strip()


def band(total, days):
    if days is not None and days < 0:
        return "منقضی"
    if total >= 85:
        return "بسیار جذاب"
    if total >= 70:
        return "جذاب"
    if total >= 50:
        return "قابل بررسی"
    return "ضعیف"


# ---------------------------------------------------------------- کشف
def fetch_mozayedegar():
    items, status = [], "ok"
    seen = set()
    for slug, _label in MOZ_CATS:
        for page in (1, 2) if slug == "food-industry" else (1,):
            url = ("https://mozayedegar.com/rfp-listing/category/%s" % slug) + \
                  ("?page=%d" % page if page > 1 else "")
            try:
                html = http_get(url)
            except Exception as e:
                status = "partial: %s" % type(e).__name__
                continue
            m = re.search(r'<script id="__NEXT_DATA__" type="application/json">(.*?)</script>',
                          html, re.S)
            if not m:
                status = "partial: no __NEXT_DATA__"
                continue
            try:
                d = json.loads(m.group(1))
                qs = d["props"]["pageProps"]["dehydratedState"]["queries"]
            except Exception:
                status = "partial: bad json"
                continue
            payload = None
            for q in qs:
                st = q.get("state", {}).get("data")
                if isinstance(st, str):
                    try:
                        st = json.loads(st)
                    except Exception:
                        continue
                if isinstance(st, dict) and isinstance(st.get("data"), list):
                    payload = st
                    break
            if not payload:
                continue
            for it in payload["data"]:
                if it.get("id") in seen:
                    continue
                seen.add(it.get("id"))
                items.append(it)
    return items, status


def probe(url):
    try:
        html = http_get(url, timeout=12)
        return ("ok", len(html))
    except urllib.error.HTTPError as e:
        return ("http %s" % e.code, 0)
    except Exception as e:
        return ("unreachable (%s)" % type(e).__name__, 0)


# ---------------------------------------------------------------- امتیازدهی قطعی
def score(title, desc, days, notice, verified):
    txt = (title or "") + " " + (desc or "")
    food_hits = sum(1 for t in FOOD_TERMS if t in txt)
    equip = any(t in txt for t in EQUIP_TERMS)
    big = any(t in txt for t in BIG_TERMS)

    p = {}
    p["source"] = 5 if notice else 4
    p["fit"] = 5 if food_hits >= 2 else (4 if food_hits == 1 else 3)
    p["prob"] = 4 if ("استعلام" in txt or "تأمین‌کننده" in txt or "تامین کننده" in txt) else 3
    if equip:
        p["money"] = 5
    elif big:
        p["money"] = 4
    else:
        p["money"] = 3
    if days is None:
        p["urg"] = 3
    elif days <= 7:
        p["urg"] = 5
    elif days <= 14:
        p["urg"] = 4
    elif days <= 30:
        p["urg"] = 3
    else:
        p["urg"] = 0
    p["comp"] = 5 if equip else 3
    p["complete"] = 3 if len(desc or "") > 400 else 2

    raw = sum(W[k] * p[k] / 5.0 for k in W)
    total = int(round(raw))
    # قاعده سخت: تا سند اصلی دیده نشده، سقف ۶۵
    if not verified:
        total = min(total, 65)
    if days is not None and days < 0:
        total = min(total, 20)
    return p, int(round(raw)), total


# ---------------------------------------------------------------- ساخت رکورد
def build(items, existing, today):
    by_notice = {}
    for e in existing:
        key = re.sub(r"\s+", "", str(e.get("ident") or ""))
        if key and key != "اعلان‌نشده":
            by_notice[key] = e
    out = list(existing)
    index_id = {str(e["id"]): i for i, e in enumerate(out)}
    added = 0

    for it in items:
        title = (it.get("title") or "").strip()
        org = (it.get("organization") or {}).get("title") or "اعلام نشده"
        desc = it.get("description") or ""
        pub = str(it.get("publishedDate") or "")[:10]
        dl = str(it.get("participationDeadline") or it.get("bidSubmissionDeadline") or "")[:10]
        notice = ""
        m = NOTICE_RE.search(desc + " " + title)
        if m:
            notice = m.group(1)

        days = None
        if dl:
            try:
                days = (datetime.date.fromisoformat(dl) - today).days
            except Exception:
                days = None

        provs = [p.get("name") for p in (it.get("provinces") or []) if p.get("name")]
        cities = [c.get("name") for c in (it.get("cities") or []) if c.get("name")]
        cats = [c.get("title") for c in (it.get("categories") or []) if c.get("title")]

        prev = by_notice.get(re.sub(r"\s+", "", notice)) if notice else None
        verified = bool(prev and prev.get("verified"))
        p, raw, total = score(title, desc, days, notice, verified)

        rec = {
            "id": prev["id"] if prev else ("MH-" + (notice[-8:] if notice else str(it.get("id")))),
            "title": title,
            "org": org,
            "domain": "مواد غذایی" if any(t in title for t in FOOD_TERMS) else (cats[0] if cats else "نامشخص"),
            "cat": cats[0] if cats else "—",
            "prov": "، ".join(provs) or "اعلام نشده",
            "city": " / ".join(cities) or "—",
            "pub": jdate(pub), "dl": jdate(dl),
            "days": days if days is not None else 0,
            "raw": raw, "total": total, "band": band(total, days),
            "ident": notice or "اعلام‌نشده",
            "url": "https://mozayedegar.com/listing/%s/" % it.get("id"),
            "route": "مستقیم از سامانه ستاد" if not any(t in title for t in EQUIP_TERMS)
                     else "اتحاد فنی + وندور لیست خریدار",
            "nxt": "دریافت اسناد از ستاد و بررسی شرایط تضمین",
            "sup": "—", "unit": "—", "judge": "—",
            "parts": p,
            "run": "به‌روزرسانی خودکار",
            "verified": verified,
            "descr": ("توضیحات کامل منتشر نشد؛ فقط خلاصه اعلان و مهلت."
                      if len(desc) < 400 else "خلاصه توضیحات از منبع تجمیع‌کننده دریافت شد."),
        }
        if prev and str(prev["id"]) in index_id:
            out[index_id[str(prev["id"])]] = rec
        else:
            out.append(rec)
            index_id[str(rec["id"])] = len(out) - 1
            added += 1
    return out, added



def finger(r):
    t = norm_title(r.get("title"))
    t = re.sub(r"\b(مناقصه|مزایده|استعلام|عمومی|یک\s*مرحله\s*ای|دو\s*مرحله\s*ای|تجدید)\b", " ", t)
    return (re.sub(r"\s+", " ", t).strip(), norm_title(r.get("org")), str(r.get("dl")))


def dedupe(items):
    """حذف بازنشرهای یک آگهی: امتیاز بالاتر / اطلاعات کامل‌تر نگه داشته می‌شود."""
    best = {}
    order = []
    for r in items:
        k = finger(r)
        if k in best:
            cur = best[k]
            if (int(r.get("total") or 0), int(r.get("raw") or 0)) > \
               (int(cur.get("total") or 0), int(cur.get("raw") or 0)):
                best[k] = r
        else:
            best[k] = r
            order.append(k)
    return [best[k] for k in order]


def prune(items, today):
    """حذف رکوردهای منقضی‌شده بیش از ۴۵ روز برای سالم نگه‌داشتن فایل داده."""
    keep = []
    for r in items:
        d = r.get("days")
        try:
            d = int(d)
        except Exception:
            d = 0
        if d < -45:
            continue
        keep.append(r)
    return keep


# ---------------------------------------------------------------- انتشار
def gh_api(method, path, payload=None):
    repo = os.environ.get("REPO", "")
    token = os.environ.get("GITHUB_TOKEN", "")
    if not repo or not token:
        return None
    url = "https://api.github.com/repos/%s/%s" % (repo, path)
    data = json.dumps(payload).encode("utf-8") if payload is not None else None
    req = urllib.request.Request(url, data=data, method=method, headers={
        "Authorization": "Bearer " + token,
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
        "Content-Type": "application/json",
        "User-Agent": "market-hunter-bot",
    })
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.loads(r.read().decode("utf-8"))


def publish(doc):
    try:
        cur = gh_api("GET", "contents/%s?ref=%s" % (DATA_PATH, BRANCH))
    except urllib.error.HTTPError as e:
        if e.code != 404:
            raise
        cur = None
    body = {
        "message": "data: به‌روزرسانی خودکار داشبورد (%s) — %d فرصت"
                   % (doc["meta"]["stamp_fa"], len(doc["items"])),
        "content": __import__("base64").b64encode(
            json.dumps(doc, ensure_ascii=False, indent=0).encode("utf-8")).decode("ascii"),
        "branch": BRANCH,
    }
    if cur and cur.get("sha"):
        body["sha"] = cur["sha"]
    res = gh_api("PUT", "contents/%s" % DATA_PATH, body)
    if not res:
        return None
    return (res.get("commit") or {}).get("sha", "?")


# ---------------------------------------------------------------- main
def main():
    now = datetime.datetime.utcnow() + datetime.timedelta(hours=3, minutes=30)
    today = now.date()

    existing = []
    here = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", DATA_PATH)
    if os.path.exists(here):
        try:
            existing = json.load(open(here, encoding="utf-8")).get("items", [])
        except Exception:
            existing = []

    raw_items, moz_status = fetch_mozayedegar()
    sources = [{
        "name": "mozayedegar.com (تجمیع‌کننده، حاوی شماره اعلان ستاد)",
        "status": moz_status, "count": len(raw_items), "level": 3,
    }]
    for name, url in PROBE_SOURCES:
        st, n = probe(url)
        sources.append({"name": name, "status": st, "count": 0,
                        "level": 1 if "ستاد" in name else 3,
                        "note": "نیازمند عضویت/IP ایران" if st != "ok" else ""})

    items, added = build(raw_items, existing, today)
    items = dedupe(items)
    items = prune(items, today)
    items.sort(key=lambda r: (-int(r.get("total") or 0), -int(r.get("days") or -999)))

    active = [i for i in items if i.get("days", 0) >= 0]
    doc = {
        "meta": {
            "generated_at": now.isoformat(timespec="seconds"),
            "stamp_fa": stamp_fa(now),
            "sources": sources,
            "total": len(items),
            "active": len(active),
            "new_this_run": added,
            "top": max([int(i.get("total") or 0) for i in items], default=0),
        },
        "items": items,
    }
    json.dump(doc, open(here, "w", encoding="utf-8"), ensure_ascii=False, indent=0)
    print("items=%d active=%d new=%d top=%d" % (len(items), len(active), added, doc["meta"]["top"]))
    for s in sources:
        print("  [%s] %s" % (s["status"], s["name"][:60]))
    try:
        sha = publish(doc)
        if sha:
            print("published data.json sha=%s" % sha)
        else:
            print("local only (no GITHUB_TOKEN/REPO)")
    except Exception as e:
        print("publish FAILED: %s %s" % (type(e).__name__, e))
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
