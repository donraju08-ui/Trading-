#!/usr/bin/env python3
"""Market switch for the trader-* skills: US (NYSE / Nasdaq) or ASX. Standard library only.

    python3 market.py                 # which market is active, its local time, open or closed
    python3 market.py use asx         # switch every skill to the ASX (writes "market" in trader/config.json)
    python3 market.py use us          # switch back to NYSE / Nasdaq
    python3 market.py show asx        # hours, holidays and defaults of a market

How a skill picks its market, first match wins:
  1. --market us|asx on its own command line (removed before the skill parses its flags)
  2. the TRADER_MARKET environment variable
  3. "market" in trader/config.json
  4. us

Each skill calls install(globals()) once, after its own definitions. For the US
it changes nothing. For another market it swaps, in that skill's globals:
  - the clock: eastern_offset / eastern_to_utc (and so to_eastern) become the
    market's local time (Sydney for the ASX, AEST / AEDT from the date)
  - the session: OPEN, CLOSE, EARLY, and the holiday and early-close tables
    (NYSE_HOLIDAYS / NYSE_EARLY_CLOSE keep their names but hold the market's dates)
  - tickers: yahoo_symbol / to_yahoo map BHP -> BHP.AX
  - config: trader/config.json's "asx": {...} block is laid over the top-level keys
  - words: what the skill prints says Sydney, ASX and A$ instead of ET, New York,
    NYSE and $ (an output filter on stdout / stderr, so prices print as A$)
Anything a skill needs beyond that (timemachine's session length, broker's
order format, news feeds) it reads from M, the active market profile.
"""

import datetime
import io
import json
import os
import re
import sys

CONFIG = "trader/config.json"


# ---------------------------------------------------------------- clocks

def _nth_sunday(year, month, n):
    d = datetime.date(year, month, 1)
    d += datetime.timedelta(days=(6 - d.weekday()) % 7)
    return d + datetime.timedelta(weeks=n - 1)


def new_york_offset(utc):
    """-4 in daylight time (2nd Sunday of March to 1st Sunday of November), -5 otherwise."""
    y = utc.year
    start = datetime.datetime.combine(_nth_sunday(y, 3, 2), datetime.time(7, 0))
    end = datetime.datetime.combine(_nth_sunday(y, 11, 1), datetime.time(6, 0))
    naive = utc.replace(tzinfo=None)
    return -4 if start <= naive < end else -5


def sydney_offset(utc):
    """+11 in daylight time (AEDT), +10 otherwise (AEST). DST ends 3:00 a.m. AEDT on the
    first Sunday of April and starts 2:00 a.m. AEST on the first Sunday of October:
    both are 16:00 UTC on the Saturday before."""
    y = utc.year
    end = datetime.datetime.combine(_nth_sunday(y, 4, 1) - datetime.timedelta(days=1), datetime.time(16, 0))
    start = datetime.datetime.combine(_nth_sunday(y, 10, 1) - datetime.timedelta(days=1), datetime.time(16, 0))
    naive = utc.replace(tzinfo=None)
    return 10 if end <= naive < start else 11


# ---------------------------------------------------------------- markets

NYSE_HOLIDAYS = {
    "2026-01-01": "New Year's Day", "2026-01-19": "Martin Luther King, Jr. Day",
    "2026-02-16": "Washington's Birthday", "2026-04-03": "Good Friday", "2026-05-25": "Memorial Day",
    "2026-06-19": "Juneteenth National Independence Day", "2026-07-03": "Independence Day (observed)",
    "2026-09-07": "Labor Day", "2026-11-26": "Thanksgiving Day", "2026-12-25": "Christmas Day",
    "2027-01-01": "New Year's Day", "2027-01-18": "Martin Luther King, Jr. Day",
    "2027-02-15": "Washington's Birthday", "2027-03-26": "Good Friday", "2027-05-31": "Memorial Day",
    "2027-06-18": "Juneteenth National Independence Day (observed)",
    "2027-07-05": "Independence Day (observed)", "2027-09-06": "Labor Day",
    "2027-11-25": "Thanksgiving Day", "2027-12-24": "Christmas Day (observed)",
}
NYSE_EARLY_CLOSE = {"2026-11-27": "day after Thanksgiving", "2026-12-24": "Christmas Eve",
                    "2027-11-26": "day after Thanksgiving"}

# ASX Trade calendar (asx.com.au, read 2026-11-12). The ASX follows NSW public holidays.
ASX_HOLIDAYS = {
    "2026-01-01": "New Year's Day", "2026-01-26": "Australia Day", "2026-04-03": "Good Friday",
    "2026-04-06": "Easter Monday", "2026-06-08": "King's Birthday", "2026-12-25": "Christmas Day",
    "2026-12-28": "Boxing Day (observed)",
    "2027-01-01": "New Year's Day", "2027-01-26": "Australia Day", "2027-03-26": "Good Friday",
    "2027-03-29": "Easter Monday", "2027-06-14": "King's Birthday",
    "2027-12-27": "Christmas Day (observed)", "2027-12-28": "Boxing Day (observed)",
}
ASX_EARLY_CLOSE = {"2026-12-24": "Christmas Eve", "2026-12-31": "New Year's Eve",
                   "2027-12-24": "Christmas Eve", "2027-12-31": "New Year's Eve"}

T = datetime.time
MARKETS = {
    "us": {
        "key": "us", "name": "NYSE / Nasdaq", "exchange": "NYSE", "tz": "ET", "city": "New York",
        "country": "US", "offset": new_york_offset, "offsets": (-4, -5),
        "open": T(9, 30), "close": T(16, 0), "early": T(13, 0),
        "holidays": NYSE_HOLIDAYS, "early_close": NYSE_EARLY_CLOSE,
        "suffix": "", "cur": "$", "currency": "USD",
        "defaults": {"no_entries_before": "09:45", "no_entries_after": "15:30", "flat_by": "15:55"},
        "broker": "alpaca", "news_region": "US", "news_lang": "en-US", "filings": "sec",
    },
    "asx": {
        "key": "asx", "name": "ASX", "exchange": "ASX", "tz": "Sydney", "city": "Sydney",
        "country": "AU", "offset": sydney_offset, "offsets": (11, 10),
        # Normal trading 10:00 to 4:00 PM; the opening auction staggers the open by
        # ticker from 10:00 to about 10:09; the closing auction runs at about 4:10 PM.
        # Early closes: normal trading ends 2:00 PM, closing auction about 2:10 PM.
        "open": T(10, 0), "close": T(16, 0), "early": T(14, 0),
        "holidays": ASX_HOLIDAYS, "early_close": ASX_EARLY_CLOSE,
        "suffix": ".AX", "cur": "A$", "currency": "AUD",
        "defaults": {"no_entries_before": "10:15", "no_entries_after": "15:30", "flat_by": "15:55"},
        "broker": "ibkr", "news_region": "AU", "news_lang": "en-AU", "filings": "asx",
    },
}
M = MARKETS["us"]
KEY = "us"


def read_config(path=CONFIG):
    try:
        with open(path, encoding="utf-8") as fh:
            doc = json.load(fh)
        return doc if isinstance(doc, dict) else {}
    except (OSError, ValueError):
        return {}


def _pop_market_flag(argv):
    """Find and remove --market X / --market=X from argv. -> X or None."""
    out, val, i = [], None, 0
    while i < len(argv):
        a = argv[i]
        if a == "--market" and i + 1 < len(argv):
            val, i = argv[i + 1], i + 2
            continue
        if a.startswith("--market="):
            val, i = a.split("=", 1)[1], i + 1
            continue
        out.append(a)
        i += 1
    argv[:] = out
    return val


def choose(argv=None, config=CONFIG):
    """The active market key, from --market, TRADER_MARKET, trader/config.json, or us."""
    flag = _pop_market_flag(argv) if argv is not None else None
    key = (flag or os.environ.get("TRADER_MARKET") or read_config(config).get("market") or "us")
    key = str(key).strip().lower()
    if key not in MARKETS:
        sys.stderr.write("market: unknown market %r (use one of: %s)\n" % (key, ", ".join(sorted(MARKETS))))
        sys.exit(2)
    return key


def overlay(cfg, key=None):
    """trader/config.json with the market's own block laid over it:
    {"budget": 5000, "asx": {"budget": 8000}} -> budget 8000 on the ASX."""
    key = key or KEY
    if not isinstance(cfg, dict):
        return cfg
    out = dict(cfg)
    sub = cfg.get(key)
    if isinstance(sub, dict):
        out.update(sub)
    return out


# ---------------------------------------------------------------- helpers for a market

def to_local(utc):
    return utc.replace(tzinfo=None) + datetime.timedelta(hours=M["offset"](utc))


def local_to_utc(local):
    for off in M["offsets"]:
        utc = local - datetime.timedelta(hours=off)
        if M["offset"](utc) == off:
            return utc
    return local - datetime.timedelta(hours=M["offsets"][-1])


def session(d):
    if d.weekday() >= 5 or d.isoformat() in M["holidays"]:
        return None
    return M["open"], (M["early"] if d.isoformat() in M["early_close"] else M["close"])


def yahoo_symbol(sym):
    sym = str(sym).upper().strip()
    if M["suffix"]:
        return bare(sym) + M["suffix"]
    return sym.replace(".", "-")


def canon(sym):
    """The one spelling every skill files a ticker under: BHP / bhp.ax -> BHP.AX on the
    ASX (what Yahoo looks up); TSLA / tsla -> TSLA on the US market."""
    sym = str(sym or "").upper().strip()
    if not sym:
        return sym
    return bare(sym) + M["suffix"] if M["suffix"] else sym


def bare(sym):
    """BHP.AX -> BHP on the ASX; unchanged on the US market."""
    sym = str(sym).upper().strip()
    if M["suffix"] and sym.endswith(M["suffix"]):
        return sym[:-len(M["suffix"])]
    return sym


def money(x):
    return M["cur"] + "{:,.2f}".format(x)


# ---------------------------------------------------------------- the words on screen

class _Words(io.TextIOBase):
    """Rewrites a skill's US words for the active market as it prints."""

    def __init__(self, raw, subs):
        self.raw, self.subs = raw, subs

    def write(self, s):
        for rx, rep in self.subs:
            s = rx.sub(rep, s)
        return self.raw.write(s)

    def flush(self):
        return self.raw.flush()

    def isatty(self):
        return self.raw.isatty()

    @property
    def encoding(self):
        return getattr(self.raw, "encoding", "utf-8")

    def fileno(self):
        return self.raw.fileno()


def _subs(m):
    if m["key"] == "us":
        return []
    return [
        (re.compile(r"(?<![A-Za-z$])\$(?=[\d(<-])"), m["cur"]),      # $12.34 -> A$12.34 (not "A$" again)
        (re.compile(r"\bNew York time\b"), m["city"] + " time"),
        (re.compile(r"\bNew York\b"), m["city"]),
        (re.compile(r"\bNYSE\b"), m["exchange"]),
        (re.compile(r"(?<=\d) ET\b"), " " + m["tz"]),                 # 10:15 ET -> 10:15 Sydney
        (re.compile(r"(?<=[AP]M) ET\b"), " " + m["tz"]),
        (re.compile(r"\bET\b(?=[).,:;]|\s*$)"), m["tz"]),
    ]


# ---------------------------------------------------------------- install

def install(g, argv=None):
    """Point one skill's globals at the active market. Call once, after the skill's
    own definitions. Returns the market profile."""
    global M, KEY
    argv = sys.argv if argv is None else argv
    KEY = choose(argv)
    M = MARKETS[KEY]
    g["MARKET"] = M
    if KEY == "us":
        return M
    g["eastern_offset"] = M["offset"]
    g["eastern_to_utc"] = local_to_utc
    for name in ("OPEN", "CLOSE", "EARLY"):
        if name in g:
            g[name] = M[name.lower()]
    for name, src in (("NYSE_HOLIDAYS", "holidays"), ("NYSE_EARLY_CLOSE", "early_close")):
        if isinstance(g.get(name), dict):
            g[name].clear()                  # same dict object: anything holding it sees the change
            g[name].update(M[src])
    for name in ("DEFAULTS", "CONFIG_DEFAULTS", "DEFAULT_CONFIG"):
        d = g.get(name)
        if isinstance(d, dict):
            for k, v in M["defaults"].items():
                if k in d:
                    d[k] = v
    for name in ("yahoo_symbol", "to_yahoo"):
        if callable(g.get(name)):
            g[name] = yahoo_symbol
    for name in ("read_config", "load_config"):
        fn = g.get(name)
        if callable(fn) and not getattr(fn, "_market_overlay", False):
            def wrapped(*a, _fn=fn, **k):
                return overlay(_fn(*a, **k))
            wrapped._market_overlay = True
            g[name] = wrapped
    if not isinstance(sys.stdout, _Words):
        sys.stdout = _Words(sys.stdout, _subs(M))
        sys.stderr = _Words(sys.stderr, _subs(M))
    return M


# ---------------------------------------------------------------- command line

def _status(m, now_utc=None):
    global M
    old, M = M, m
    try:
        now_utc = now_utc or datetime.datetime.now(datetime.timezone.utc).replace(tzinfo=None, microsecond=0)
        local = to_local(now_utc)
        s = session(local.date())
        if s is None:
            why = "weekend" if local.weekday() >= 5 else "%s holiday: %s" % (
                m["exchange"], m["holidays"][local.date().isoformat()])
            state = "closed today (%s)" % why
        elif local.time() < s[0]:
            state = "opens at %s" % s[0].strftime("%H:%M")
        elif local.time() < s[1]:
            state = "OPEN until %s" % s[1].strftime("%H:%M")
        else:
            state = "closed for the day (closed at %s)" % s[1].strftime("%H:%M")
        return "%s: %s %s %s, %s." % (m["name"], local.strftime("%a %Y-%m-%d %H:%M"), m["city"],
                                       "time", state)
    finally:
        M = old


def main(argv=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    if argv[:1] == ["use"] and len(argv) == 2:
        key = argv[1].lower()
        if key not in MARKETS:
            print("market.py: use one of: %s" % ", ".join(sorted(MARKETS)), file=sys.stderr)
            return 2
        cfg = read_config()
        cfg["market"] = key
        os.makedirs(os.path.dirname(CONFIG), exist_ok=True)
        with open(CONFIG, "w", encoding="utf-8") as fh:
            json.dump(cfg, fh, indent=2)
            fh.write("\n")
        m = MARKETS[key]
        print("Every trader-* skill now uses %s (%s time, %s, tickers like %s)." % (
            m["name"], m["city"], m["currency"], "BHP.AX" if m["suffix"] else "TSLA"))
        print("Wrote \"market\": \"%s\" to %s. One run on the other market: add --market %s." % (
            key, CONFIG, "us" if key != "us" else "asx"))
        print(_status(m))
        if key != "us":
            print("Orders: Alpaca does not trade the ASX; trader-broker writes an Interactive Brokers ticket.")
        return 0
    if argv[:1] == ["show"] and len(argv) == 2 and argv[1].lower() in MARKETS:
        m = MARKETS[argv[1].lower()]
        print("%s: %s to %s %s time, early close %s; currency %s; Yahoo suffix %r." % (
            m["name"], m["open"].strftime("%H:%M"), m["close"].strftime("%H:%M"), m["city"],
            m["early"].strftime("%H:%M"), m["currency"], m["suffix"]))
        print("Holidays: " + ", ".join("%s %s" % (d, n) for d, n in sorted(m["holidays"].items())))
        print("Early closes: " + ", ".join("%s %s" % (d, n) for d, n in sorted(m["early_close"].items())))
        print("Defaults: " + ", ".join("%s %s" % kv for kv in m["defaults"].items()))
        return 0
    if argv:
        print(__doc__.strip().split("\n\n")[1], file=sys.stderr)
        return 2
    key = choose()
    print("Active market: %s (from %s)." % (MARKETS[key]["name"], "TRADER_MARKET" if os.environ.get("TRADER_MARKET")
                                              else CONFIG if read_config().get("market") else "the default"))
    for k in ("us", "asx"):
        print("  " + _status(MARKETS[k]))
    return 0


if __name__ == "__main__":
    sys.exit(main())
