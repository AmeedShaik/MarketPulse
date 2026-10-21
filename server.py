"""
MarketPulse - Live BTCUSD / XAUUSD News & Levels Dashboard
-----------------------------------------------------------
Pure Python standard library (no pip install needed). Python 3.9+.

Run:  python server.py      -> opens http://localhost:8765
"""
import gzip
import html
import json
import math
import os
import re
import threading
import time
import webbrowser
import xml.etree.ElementTree as ET
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone, timedelta
from email.utils import parsedate_to_datetime
from http.server import ThreadingHTTPServer, BaseHTTPRequestHandler
from urllib.parse import quote_plus, urlparse
from urllib.request import Request, urlopen

# =====================================================================
# CONFIG
# =====================================================================
PORT = 8765
NEWS_REFRESH_SEC = 30          # news feeds poll interval
PRICE_REFRESH_SEC = 15         # BTC / Gold price poll interval
MACRO_REFRESH_SEC = 60         # DXY, US10Y, Oil, Silver, S&P
CAL_REFRESH_SEC = 900          # economic calendar (source asks for light polling)
DERIV_REFRESH_SEC = 60         # BTC funding / OI / Fear & Greed
NEWS_MAX_AGE_HOURS = 36        # keep headlines newer than this
SENTIMENT_WINDOW_HOURS = 24    # headlines used for today's sentiment
OPEN_BROWSER = True

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
CAL_CACHE = os.path.join(BASE_DIR, "calendar_cache.json")
UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/124.0 Safari/537.36")

# Each asset tries its sources in order until one works
ASSETS = {
    "XAU": {"name": "Gold (XAUUSD / GC Futures)",
            "sources": [("yahoo", "GC=F"), ("binance", "PAXGUSDT")]},
    "BTC": {"name": "Bitcoin (BTCUSD)",
            "sources": [("binance", "BTCUSDT"), ("yahoo", "BTC-USD")]},
}
MACRO = {
    "DXY": "DX-Y.NYB", "US10Y": "^TNX", "WTI": "CL=F",
    "SILVER": "SI=F", "SPX_FUT": "ES=F", "NQ_FUT": "NQ=F", "VIX": "^VIX",
}

GN = "https://news.google.com/rss/search?q={}&hl=en-US&gl=US&ceid=US:en"
# (source label, url, default tag)  tag AUTO = classify by keywords, drop if irrelevant
FEEDS = [
    ("Google News", GN.format(quote_plus('gold price OR XAUUSD OR "gold futures" OR bullion when:1d')), "XAU"),
    ("Google News", GN.format(quote_plus('bitcoin OR BTC price OR "bitcoin ETF" when:1d')), "BTC"),
    ("Google News", GN.format(quote_plus('Fed OR FOMC OR Powell OR CPI OR PMI OR payrolls OR "Treasury yields" when:1d')), "MACRO"),
    ("CoinDesk", "https://www.coindesk.com/arc/outboundfeeds/rss/", "AUTO"),
    ("Cointelegraph", "https://cointelegraph.com/rss", "AUTO"),
    ("Decrypt", "https://decrypt.co/feed", "AUTO"),
    ("FXStreet", "https://www.fxstreet.com/rss/news", "AUTO"),
    ("ForexLive", "https://www.forexlive.com/feed/news", "AUTO"),
    ("Investing.com", "https://www.investing.com/rss/news_11.rss", "AUTO"),
    ("Investing.com", "https://www.investing.com/rss/news_14.rss", "AUTO"),
    ("CNBC Economy", "https://www.cnbc.com/id/20910258/device/rss/rss.html", "AUTO"),
    ("Federal Reserve", "https://www.federalreserve.gov/feeds/press_all.xml", "MACRO"),
    ("Fed Speeches", "https://www.federalreserve.gov/feeds/speeches.xml", "MACRO"),
]
CAL_URL = "https://nfs.faireconomy.media/ff_calendar_thisweek.json"

# =====================================================================
# KEYWORDS / LEXICONS
# =====================================================================
KW_BTC = r"\b(bitcoin|btc|crypto|cryptocurrenc|ibit|spot etf|saylor|microstrategy|strategy inc|coinbase|binance|blackrock.*bitcoin|halving|miners?|stablecoin|ether(eum)?)\b"
KW_XAU = r"\b(gold|xau|bullion|precious metals?|comex|silver|spdr gold|gld|central bank buying)\b"
KW_MACRO = (r"\b(fed|fomc|powell|federal reserve|rate cut|rate hike|interest rates?|cpi|ppi|pce|inflation|"
            r"pmi|ism|payrolls?|nfp|non-?farm|jobless|unemployment|gdp|retail sales|treasury|yields?|"
            r"dollar|dxy|tariffs?|ecb|boj|boe|recession|geopolitic|war|missile|sanction|opec|crude|oil|"
            r"jobs report|beige book|minutes|waller|williams|jefferson|bowman|barr|goolsbee|daly|logan|"
            r"hammack|musalem|schmid|kashkari|bostic|harker|collins|cook|kugler|miran|warsh|hassett)\b")

HIGH_IMPACT = (r"\b(fomc|powell|rate decision|rate cut|rate hike|cpi|nfp|non-?farm|payrolls|pce|pmi|ism|gdp|"
               r"jobs report|sec |etf approv|tariff|war|attack|missile|sanction|emergency|liquidat|hack|"
               r"crash|plunge|soar|surge|record high|all-time high|ath|default|shutdown|fed chair)\b")
MED_IMPACT = (r"\b(fed|yield|dollar|inflation|jobless|retail sales|etf|inflows?|outflows?|central bank|"
              r"treasury|opec|minutes|speech|speaks|testimony|ecb|boj)\b")

GEN_BULL = [r"\brises?\b", r"\brose\b", r"\bgains?\b", r"\bjumps?\b", r"\bsurges?\b", r"\bsoars?\b", r"\brall(y|ies)\b",
            r"\bclimbs?\b", r"\bhigher\b", r"\brecord\b", r"\ball-time high\b", r"\bbullish\b", r"\brebounds?\b",
            r"\brecover", r"\bedges? up\b", r"\bedges? higher\b", r"\bupside\b", r"\bbreak(s|out)? above\b",
            r"\bbuy(ing)? pressure\b", r"\bdemand\b", r"\bstrong\b", r"\bboost"]
GEN_BEAR = [r"\bfalls?\b", r"\bfell\b", r"\bslips?\b", r"\bdrops?\b", r"\bdeclines?\b", r"\bplunges?\b", r"\bslides?\b",
            r"\blosses\b", r"\blower\b", r"\bbearish\b", r"\bsell-?off\b", r"\bdips?\b", r"\beases?\b", r"\btumbles?\b",
            r"\bsinks?\b", r"\bslumps?\b", r"\bretreats?\b", r"\bweak(ens|er)?\b", r"\bpressure\b", r"\bdownside\b",
            r"\bbreak(s|down)? below\b", r"\bselling\b"]
# asset specific (pattern, score)
LEX_XAU = [(r"rate cuts?|dovish|easing|cut rates", 2), (r"rate hikes?|hawkish|tighten|higher for longer", -2),
           (r"safe.?haven|geopolitic|war|missile|conflict|tension|uncertainty|crisis", 1.5),
           (r"central bank (gold )?buying|gold etf inflows", 1.5), (r"(dollar|dxy) (rises|gains|strengthens|jumps|firms)", -1.5),
           (r"(dollar|dxy) (falls|slips|weakens|drops|eases)", 1.5), (r"yields? (rise|climb|jump|surge|higher)", -1.5),
           (r"yields? (fall|drop|slip|ease|lower|cool)", 1.5), (r"weak(er)? (jobs|data|pmi|payrolls)", 1),
           (r"strong(er)? (jobs|data|pmi|payrolls)", -1), (r"hot(ter)? (cpi|inflation)", -1), (r"profit.?taking", -1)]
LEX_BTC = [(r"etf inflows?|net inflows?|inflows", 2), (r"etf outflows?|net outflows?|outflows", -2),
           (r"rate cuts?|dovish|easing", 1.5), (r"rate hikes?|hawkish|tighten", -1.5),
           (r"adoption|approv|treasury (reserve|buy)|strategic reserve|accumulat|purchases?", 1.5),
           (r"hack|exploit|ban|crackdown|lawsuit|sec sues|fraud|shut ?down", -2),
           (r"liquidations?", -1), (r"short squeeze", 1.5), (r"risk.?on", 1), (r"risk.?off", -1),
           (r"(dollar|dxy) (rises|gains|strengthens)", -1), (r"yields? (fall|drop|cool|ease)", 1)]

ASSET_PAT = {"XAU": re.compile(KW_XAU, re.I), "BTC": re.compile(KW_BTC, re.I)}
MACRO_PAT = re.compile(KW_MACRO, re.I)
HIGH_PAT = re.compile(HIGH_IMPACT, re.I)
MED_PAT = re.compile(MED_IMPACT, re.I)
BULL = [re.compile(p, re.I) for p in GEN_BULL]
BEAR = [re.compile(p, re.I) for p in GEN_BEAR]
LEX = {"XAU": [(re.compile(p, re.I), s) for p, s in LEX_XAU],
       "BTC": [(re.compile(p, re.I), s) for p, s in LEX_BTC]}
TAG_RE = re.compile(r"<[^>]+>")

# =====================================================================
# STATE
# =====================================================================
STATE = {
    "news": [], "feed_health": {}, "prices": {}, "macro": {}, "levels": {}, "sentiment": {},
    "calendar": [], "calendar_updated": None, "derivs": {}, "updated": None, "version": 0,
}
LOCK = threading.Lock()
SEEN = {}  # key -> first_seen epoch


def log(*a):
    print(datetime.now().strftime("%H:%M:%S"), *a, flush=True)


def http_get(url, timeout=8):
    req = Request(url, headers={"User-Agent": UA, "Accept": "*/*", "Accept-Encoding": "gzip",
                                "Accept-Language": "en-US,en;q=0.9"})
    with urlopen(req, timeout=timeout) as r:
        data = r.read()
        if r.headers.get("Content-Encoding") == "gzip" or data[:2] == b"\x1f\x8b":
            data = gzip.decompress(data)
        return data


def get_json(url, timeout=8):
    return json.loads(http_get(url, timeout).decode("utf-8", "replace"))


def bump():
    with LOCK:
        STATE["version"] += 1
        STATE["updated"] = datetime.now(timezone.utc).isoformat()


# =====================================================================
# NEWS
# =====================================================================
def parse_date(s):
    if not s:
        return None
    s = s.strip()
    try:
        d = parsedate_to_datetime(s)
    except Exception:
        try:
            d = datetime.fromisoformat(s.replace("Z", "+00:00"))
        except Exception:
            return None
    if d.tzinfo is None:
        d = d.replace(tzinfo=timezone.utc)
    return d.astimezone(timezone.utc)


def clean(t):
    return re.sub(r"\s+", " ", html.unescape(TAG_RE.sub(" ", t or ""))).strip()


def parse_feed(raw):
    root = ET.fromstring(raw)
    items = []
    for it in root.iter():
        tag = it.tag.split("}")[-1]
        if tag not in ("item", "entry"):
            continue
        d = {}
        for ch in it:
            ct = ch.tag.split("}")[-1].lower()
            if ct == "title":
                d["title"] = clean(ch.text)
            elif ct == "link":
                d["link"] = (ch.text or ch.attrib.get("href") or "").strip()
            elif ct in ("pubdate", "published", "updated", "date") and "date" not in d:
                d["date"] = ch.text
            elif ct in ("description", "summary") and "desc" not in d:
                d["desc"] = clean(ch.text)[:300]
            elif ct == "source":
                d["src"] = clean(ch.text)
        if d.get("title"):
            items.append(d)
    return items


def score_item(text, title, asset):
    s = 0.0
    for p, w in LEX[asset]:
        if p.search(text):
            s += w
    # generic up/down verbs: use the first one within ~6 words after the asset is named
    # ("Gold dips as oil rally..." -> bearish gold, ignores the oil rally)
    m = ASSET_PAT[asset].search(title)
    if m:
        window = " ".join(title[m.end():].split()[:6])
        hits = []
        for p in BULL:
            x = p.search(window)
            if x: hits.append((x.start(), 1.5))
        for p in BEAR:
            x = p.search(window)
            if x: hits.append((x.start(), -1.5))
        if hits:
            s += min(hits)[1]
    return max(-3.0, min(3.0, s))


def classify(title, desc, default):
    text = f"{title} {desc}"
    tags = set()
    for a, p in ASSET_PAT.items():
        if p.search(text):
            tags.add(a)
    if MACRO_PAT.search(text):
        tags.add("MACRO")
    if default in ("XAU", "BTC", "MACRO"):
        tags.add(default)
    if not tags:
        return None
    impact = "HIGH" if HIGH_PAT.search(title) else ("MED" if MED_PAT.search(text) else "LOW")
    fed = bool(re.search(r"\b(fed|fomc|powell|federal reserve)\b", text, re.I))
    # macro news drives both; asset-specific news only drives its own asset
    other = {"XAU": "BTC", "BTC": "XAU"}
    affects = [a for a in ("XAU", "BTC") if a in tags or ("MACRO" in tags and other[a] not in tags)]
    scores = {a: score_item(text, title, a) for a in affects}
    return {"tags": sorted(tags), "impact": impact, "fed": fed, "scores": scores}


def fetch_feed(feed):
    label, url, default = feed
    t0 = time.time()
    try:
        raw = http_get(url, timeout=8)
        items = parse_feed(raw)
        out = []
        now = datetime.now(timezone.utc)
        for d in items:
            title = d["title"]
            src = d.get("src") or label
            if label == "Google News" and " - " in title:
                title, src = title.rsplit(" - ", 1)
            dt = parse_date(d.get("date")) or now
            if (now - dt).total_seconds() > NEWS_MAX_AGE_HOURS * 3600:
                continue
            c = classify(title, d.get("desc", ""), default)
            if not c:
                continue
            out.append({"title": title, "link": d.get("link", ""), "source": src, "feed": label,
                        "ts": dt.timestamp(), "desc": d.get("desc", ""), **c})
        return label, url, out, None, round((time.time() - t0) * 1000)
    except Exception as e:
        return label, url, [], str(e)[:120], round((time.time() - t0) * 1000)


def norm_key(t):
    return re.sub(r"[^a-z0-9]", "", t.lower())[:70]


def refresh_news():
    with ThreadPoolExecutor(max_workers=len(FEEDS)) as ex:
        results = list(ex.map(fetch_feed, FEEDS))
    merged, health = {}, {}
    now = time.time()
    for label, url, items, err, ms in results:
        host = urlparse(url).netloc
        health[f"{label} ({host})"] = {"ok": err is None, "count": len(items), "ms": ms, "error": err}
        for it in items:
            k = norm_key(it["title"])
            if k in merged:  # duplicate: merge tags, keep earliest time
                m = merged[k]
                m["tags"] = sorted(set(m["tags"]) | set(it["tags"]))
                m["ts"] = min(m["ts"], it["ts"])
                continue
            if k not in SEEN:
                SEEN[k] = now
            it["first_seen"] = SEEN[k]
            it["id"] = k
            merged[k] = it
    news = sorted(merged.values(), key=lambda x: x["ts"], reverse=True)[:400]
    with LOCK:
        STATE["news"] = news
        STATE["feed_health"] = health
    compute_sentiment()
    bump()
    ok = sum(1 for h in health.values() if h["ok"])
    log(f"news: {len(news)} items from {ok}/{len(health)} feeds")


# =====================================================================
# PRICES / LEVELS
# =====================================================================
def yahoo_candles(sym, rng="6mo"):
    url = f"https://query1.finance.yahoo.com/v8/finance/chart/{quote_plus(sym)}?interval=1d&range={rng}"
    j = get_json(url)["chart"]["result"][0]
    q = j["indicators"]["quote"][0]
    meta = j["meta"]
    c = []
    for i, t in enumerate(j["timestamp"]):
        o, h, l, cl = q["open"][i], q["high"][i], q["low"][i], q["close"][i]
        if None in (o, h, l, cl):
            continue
        c.append({"t": t, "o": o, "h": h, "l": l, "c": cl})
    price = meta.get("regularMarketPrice") or c[-1]["c"]
    prev = meta.get("chartPreviousClose") if len(c) < 2 else c[-2]["c"]
    return c, price, prev


def binance_candles(sym):
    url = f"https://api.binance.com/api/v3/klines?symbol={sym}&interval=1d&limit=180"
    k = get_json(url)
    c = [{"t": r[0] // 1000, "o": float(r[1]), "h": float(r[2]), "l": float(r[3]), "c": float(r[4])} for r in k]
    return c, c[-1]["c"], c[-2]["c"]


def ema(vals, n):
    k, e, out = 2 / (n + 1), vals[0], []
    for v in vals:
        e = v * k + e * (1 - k)
        out.append(e)
    return out


def rsi(vals, n=14):
    if len(vals) <= n:
        return 50.0
    g = l = 0.0
    for i in range(1, n + 1):
        d = vals[i] - vals[i - 1]
        g += max(d, 0); l += max(-d, 0)
    g /= n; l /= n
    for i in range(n + 1, len(vals)):
        d = vals[i] - vals[i - 1]
        g = (g * (n - 1) + max(d, 0)) / n
        l = (l * (n - 1) + max(-d, 0)) / n
    return 100.0 if l == 0 else 100 - 100 / (1 + g / l)


def atr(c, n=14):
    trs = [max(x["h"] - x["l"], abs(x["h"] - c[i - 1]["c"]), abs(x["l"] - c[i - 1]["c"]))
           for i, x in enumerate(c) if i > 0]
    return sum(trs[-n:]) / min(n, len(trs)) if trs else 0


def pivots(h, l, c):
    p = (h + l + c) / 3
    return {"R3": h + 2 * (p - l), "R2": p + (h - l), "R1": 2 * p - l, "P": p,
            "S1": 2 * p - h, "S2": p - (h - l), "S3": l - 2 * (h - p)}


def weekly(c):
    weeks = {}
    for x in c:
        d = datetime.fromtimestamp(x["t"], timezone.utc)
        k = d.isocalendar()[:2]
        w = weeks.setdefault(k, {"h": x["h"], "l": x["l"], "o": x["o"], "c": x["c"]})
        w["h"], w["l"], w["c"] = max(w["h"], x["h"]), min(w["l"], x["l"]), x["c"]
    return list(weeks.values())


def swing_levels(c, price, a):
    n = len(c)
    pts = []
    for i in range(2, n - 2):
        if c[i]["h"] >= max(c[j]["h"] for j in range(i - 2, i + 3)):
            pts.append(c[i]["h"])
        if c[i]["l"] <= min(c[j]["l"] for j in range(i - 2, i + 3)):
            pts.append(c[i]["l"])
    pts.sort()
    tol = max(a * 0.5, price * 0.002)
    clusters = []
    for p in pts:
        if clusters and p - clusters[-1][-1] <= tol:
            clusters[-1].append(p)
        else:
            clusters.append([p])
    lv = [{"price": sum(x) / len(x), "touches": len(x)} for x in clusters]
    res = sorted([x for x in lv if x["price"] > price], key=lambda x: x["price"])[:4]
    sup = sorted([x for x in lv if x["price"] < price], key=lambda x: -x["price"])[:4]
    return sup, res


def build_levels(c, price, prev_close):
    closes = [x["c"] for x in c]
    a = atr(c)
    pd = c[-2]
    wk = weekly(c)
    pw = wk[-2] if len(wk) > 1 else wk[-1]
    e20, e50 = ema(closes, 20)[-1], ema(closes, 50)[-1]
    e200 = ema(closes, 200)[-1] if len(closes) >= 200 else None
    r = rsi(closes)
    sup, res = swing_levels(c[:-1], price, a)
    dp, wp = pivots(pd["h"], pd["l"], pd["c"]), pivots(pw["h"], pw["l"], pw["c"])
    key = []
    for k, v in dp.items():
        key.append({"label": f"Daily {k}", "price": v})
    for k, v in wp.items():
        if k in ("R1", "R2", "P", "S1", "S2"):
            key.append({"label": f"Weekly {k}", "price": v})
    key += [{"label": "Prev Day High", "price": pd["h"]}, {"label": "Prev Day Low", "price": pd["l"]},
            {"label": "Prev Week High", "price": pw["h"]}, {"label": "Prev Week Low", "price": pw["l"]},
            {"label": "EMA 20 (D)", "price": e20}, {"label": "EMA 50 (D)", "price": e50}]
    if e200:
        key.append({"label": "EMA 200 (D)", "price": e200})
    for x in sup:
        key.append({"label": f"Swing Support ({x['touches']}x)", "price": x["price"]})
    for x in res:
        key.append({"label": f"Swing Resistance ({x['touches']}x)", "price": x["price"]})
    for k in key:
        k["side"] = "R" if k["price"] > price else "S"
        k["dist_pct"] = (k["price"] - price) / price * 100
    key.sort(key=lambda x: -x["price"])
    above = [k for k in key if k["side"] == "R"]
    below = [k for k in key if k["side"] == "S"]
    today = c[-1]
    return {
        "price": price, "prev_close": prev_close, "chg": price - prev_close,
        "chg_pct": (price - prev_close) / prev_close * 100 if prev_close else 0,
        "day_high": today["h"], "day_low": today["l"], "day_open": today["o"],
        "atr": a, "exp_high": today["o"] + a, "exp_low": today["o"] - a,
        "range_used_pct": (today["h"] - today["l"]) / a * 100 if a else 0,
        "rsi": r, "ema20": e20, "ema50": e50, "ema200": e200,
        "daily_pivots": dp, "weekly_pivots": wp, "key_levels": key,
        "nearest_res": above[-1] if above else None, "nearest_sup": below[0] if below else None,
        "spark": closes[-60:],
    }


def refresh_asset(code):
    cfg = ASSETS[code]
    for src, sym in cfg["sources"]:
        try:
            c, price, prev = (yahoo_candles(sym) if src == "yahoo" else binance_candles(sym))
            if len(c) < 30:
                raise ValueError("not enough candles")
            lv = build_levels(c, price, prev)
            lv["source"] = f"{src}:{sym}"
            with LOCK:
                STATE["levels"][code] = lv
            return True
        except Exception as e:
            log(f"price {code} via {src}:{sym} failed: {str(e)[:80]}")
    return False


def refresh_prices():
    with ThreadPoolExecutor(max_workers=2) as ex:
        list(ex.map(refresh_asset, ASSETS.keys()))
    compute_sentiment()
    bump()


def refresh_macro():
    def one(item):
        k, sym = item
        try:
            c, price, prev = yahoo_candles(sym, "1mo")
            return k, {"sym": sym, "price": price, "chg_pct": (price - prev) / prev * 100 if prev else 0}
        except Exception as e:
            return k, {"sym": sym, "error": str(e)[:60]}
    with ThreadPoolExecutor(max_workers=len(MACRO)) as ex:
        res = dict(ex.map(one, MACRO.items()))
    with LOCK:
        STATE["macro"] = res
        g, s = STATE["levels"].get("XAU", {}).get("price"), res.get("SILVER", {}).get("price")
        if g and s:
            STATE["macro"]["GSR"] = {"sym": "Gold/Silver", "price": g / s, "chg_pct": 0}
    compute_sentiment()
    bump()


def refresh_derivs():
    d = {}
    for key, url, fn in [
        ("funding", "https://fapi.binance.com/fapi/v1/premiumIndex?symbol=BTCUSDT",
         lambda j: float(j["lastFundingRate"]) * 100),
        ("open_interest", "https://fapi.binance.com/fapi/v1/openInterest?symbol=BTCUSDT",
         lambda j: float(j["openInterest"])),
        ("fng", "https://api.alternative.me/fng/?limit=2",
         lambda j: {"value": int(j["data"][0]["value"]), "label": j["data"][0]["value_classification"],
                    "prev": int(j["data"][1]["value"])}),
    ]:
        try:
            d[key] = fn(get_json(url))
        except Exception as e:
            d[key] = None
            log(f"derivs {key} failed: {str(e)[:80]}")
    with LOCK:
        STATE["derivs"] = d
    bump()


# =====================================================================
# ECONOMIC CALENDAR
# =====================================================================
def refresh_calendar():
    data = None
    try:
        data = get_json(CAL_URL, timeout=10)
        with open(CAL_CACHE, "w", encoding="utf-8") as f:
            json.dump(data, f)
    except Exception as e:
        log(f"calendar fetch failed ({str(e)[:60]}), using cache")
        if os.path.exists(CAL_CACHE):
            with open(CAL_CACHE, encoding="utf-8") as f:
                data = json.load(f)
    if data is None:
        return
    ev = []
    for x in data:
        country, impact, title = x.get("country", ""), x.get("impact", ""), x.get("title", "")
        if impact in ("Holiday", "Non-Economic") and country != "USD":
            continue
        if country != "USD" and impact != "High":
            continue
        dt = parse_date(x.get("date"))
        t = title.lower()
        cat = ("FED" if re.search(r"fomc|fed|powell|federal funds|beige", t) else
               "PMI" if "pmi" in t or "ism" in t else
               "INFLATION" if re.search(r"cpi|ppi|pce|inflation", t) else
               "JOBS" if re.search(r"payroll|employment|jobless|jolts|unemployment|adp|earnings", t) else
               "GDP" if "gdp" in t else "OTHER")
        ev.append({"title": title, "country": country, "impact": impact, "ts": dt.timestamp() if dt else None,
                   "forecast": x.get("forecast", ""), "previous": x.get("previous", ""),
                   "actual": x.get("actual", ""), "cat": cat,
                   "speech": bool(re.search(r"speaks|testifies|press conference|speech", t))})
    ev.sort(key=lambda e: e["ts"] or 0)
    with LOCK:
        STATE["calendar"] = ev
        STATE["calendar_updated"] = datetime.now(timezone.utc).isoformat()
    bump()
    log(f"calendar: {len(ev)} events")


# =====================================================================
# SENTIMENT
# =====================================================================
def label_for(s):
    if s >= 35: return "Strong Bullish"
    if s >= 12: return "Bullish"
    if s > -12: return "Neutral"
    if s > -35: return "Bearish"
    return "Strong Bearish"


def compute_sentiment():
    with LOCK:
        news = list(STATE["news"]); levels = dict(STATE["levels"]); macro = dict(STATE["macro"])
    now = time.time()
    out = {}
    for a in ("XAU", "BTC"):
        wsum = ssum = 0.0
        bull = bear = neu = 0
        drivers = []
        for n in news:
            if a not in n["scores"]:
                continue
            age_h = (now - n["ts"]) / 3600
            if age_h > SENTIMENT_WINDOW_HOURS:
                continue
            s = n["scores"][a]
            w = math.exp(-age_h / 8) * (2 if n["impact"] == "HIGH" else 1.3 if n["impact"] == "MED" else 1)
            if a in n["tags"]:
                w *= 1.5
            wsum += w; ssum += w * s
            if s > 0: bull += 1
            elif s < 0: bear += 1
            else: neu += 1
            if s != 0:
                drivers.append({"title": n["title"], "score": s, "w": w * abs(s), "link": n["link"], "source": n["source"], "ts": n["ts"]})
        news_score = max(-100, min(100, (ssum / wsum) / 1.5 * 100)) if wsum else 0
        tech, tnotes = 0.0, []
        lv = levels.get(a)
        if lv:
            p = lv["price"]
            tech += 25 if p > lv["ema20"] else -25; tnotes.append(f"Price {'above' if p > lv['ema20'] else 'below'} EMA20")
            tech += 25 if lv["ema20"] > lv["ema50"] else -25; tnotes.append(f"EMA20 {'>' if lv['ema20'] > lv['ema50'] else '<'} EMA50")
            tech += max(-25, min(25, (lv["rsi"] - 50) * 1.25)); tnotes.append(f"RSI {lv['rsi']:.1f}")
            tech += max(-25, min(25, lv["chg"] / lv["atr"] * 25 if lv["atr"] else 0)); tnotes.append(f"Day {lv['chg_pct']:+.2f}%")
        macro_score, mnotes = 0.0, []
        dxy, tnx = macro.get("DXY", {}).get("chg_pct"), macro.get("US10Y", {}).get("chg_pct")
        if dxy is not None:
            macro_score += -dxy * 60; mnotes.append(f"DXY {dxy:+.2f}%")
        if tnx is not None:
            macro_score += -tnx * 15; mnotes.append(f"US10Y {tnx:+.2f}%")
        if a == "BTC":
            nq = macro.get("NQ_FUT", {}).get("chg_pct")
            if nq is not None:
                macro_score += nq * 30; mnotes.append(f"Nasdaq fut {nq:+.2f}%")
        macro_score = max(-100, min(100, macro_score))
        total = 0.45 * news_score + 0.40 * tech + 0.15 * macro_score
        drivers.sort(key=lambda d: -d["w"])
        out[a] = {"score": round(total, 1), "label": label_for(total), "news_score": round(news_score, 1),
                  "tech_score": round(tech, 1), "macro_score": round(macro_score, 1),
                  "bull": bull, "bear": bear, "neutral": neu, "tech_notes": tnotes, "macro_notes": mnotes,
                  "drivers": drivers[:6]}
    with LOCK:
        STATE["sentiment"] = out


# =====================================================================
# SCHEDULER + HTTP
# =====================================================================
def loop(fn, every, name):
    def run():
        while True:
            t0 = time.time()
            try:
                fn()
            except Exception as e:
                log(f"{name} error: {e}")
            time.sleep(max(1, every - (time.time() - t0)))
    threading.Thread(target=run, daemon=True, name=name).start()


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def send(self, code, body, ctype):
        gz = "gzip" in (self.headers.get("Accept-Encoding") or "") and len(body) > 1024
        if gz:
            body = gzip.compress(body, 5)
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Cache-Control", "no-store")
        if gz:
            self.send_header("Content-Encoding", "gzip")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        path = urlparse(self.path).path
        if path in ("/", "/index.html"):
            with open(os.path.join(BASE_DIR, "index.html"), "rb") as f:
                return self.send(200, f.read(), "text/html; charset=utf-8")
        if path == "/api/version":
            return self.send(200, json.dumps({"v": STATE["version"]}).encode(), "application/json")
        if path == "/api/all":
            with LOCK:
                body = json.dumps({**STATE, "server_time": time.time()}, default=str).encode()
            return self.send(200, body, "application/json")
        if path == "/api/refresh":
            threading.Thread(target=refresh_news, daemon=True).start()
            return self.send(200, b'{"ok":true}', "application/json")
        self.send(404, b"not found", "text/plain")


def main():
    log("MarketPulse starting - loading feeds in background...")
    loop(refresh_news, NEWS_REFRESH_SEC, "news")
    loop(refresh_prices, PRICE_REFRESH_SEC, "prices")
    loop(refresh_macro, MACRO_REFRESH_SEC, "macro")
    loop(refresh_calendar, CAL_REFRESH_SEC, "calendar")
    loop(refresh_derivs, DERIV_REFRESH_SEC, "derivs")
    srv = ThreadingHTTPServer(("127.0.0.1", PORT), Handler)
    url = f"http://localhost:{PORT}"
    log(f"Dashboard running at {url}  (Ctrl+C to stop)")
    if OPEN_BROWSER:
        threading.Timer(1.5, lambda: webbrowser.open(url)).start()
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        log("stopped")


if __name__ == "__main__":
    main()
