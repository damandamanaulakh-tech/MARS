"""News feed — the owner's design, built on their order.

OWNER'S SPEC (2026-08-13, their words): "when i will fetch new universe for
stocks, u will run the feed that time only and it kept on that stock ... if it
value it trigger or it pass". Then, on scope: "no block just show".

So: ONE pass per daily cycle over the names the scan already filtered (plus
held names), a verdict that sticks to each stock, and each stock reading
TRIGGER or PASS. Nothing here blocks, sizes or places an order.

WHY IT CANNOT EVER BLOCK A BUY (recorded so a later session does not "fix"
this): no news archive is reachable — Google News gives roughly 7 days back —
so a news rule can never be calibrated the way the owner's price triggers were.
Their own REQUIRES_15Y_BACKTEST rule therefore keeps news out of the money
path. The verdict informs the owner; the owner decides.

HOW A HEADLINE IS JUDGED — two layers, deliberately in this order:
  1. RULES (deterministic, auditable, free). Keyword patterns tag the event
     type: results beat/miss, order win, block deal, regulatory action, rating
     change, governance/resignation, fundraise, rumour. Every tag can be
     explained by pointing at the matched phrase, and the same headline always
     produces the same tag.
  2. LEXICON sentiment on top, also deterministic.
An LLM is NOT used. It would make the verdict unreproducible and unauditable
for a gain the rules already cover; if the owner ever wants nuance, the hook is
`classify_headline` and the label would have to say which path produced it.

Boundary (test-enforced, same as the other display layers): engine.py, risk.py
and orders.py must never import this module.
"""
from __future__ import annotations

import html
import logging
import re
import time
import urllib.parse
import urllib.request
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Optional

log = logging.getLogger(__name__)

_DATA = Path(__file__).resolve().parents[1] / "data"
_UNIVERSE_CSV = _DATA / "upstox_nse_equities.csv"
UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/122.0 Safari/537.36")

LOOKBACK_DAYS = 7          # what Google News actually serves

#: Aggregator/quote-page titles that carry no event. Measured against a live
#: pull: RELIANCE's top three "headlines" were a live-price page, a chart page
#: and a weekly-outlook template. Dropping them is not censorship — none of
#: them describes anything that happened.
NOISE_PATTERNS = (
    r"share price (?:today|live)", r"live nse", r"stock price & chart",
    r"\bstock analysis\b", r"outlook for the week", r"share price target",
    r"\bmoving averages?\b", r"technical (?:analysis|levels) today",
    r"^\w+ share price$", r"stock price nse/bse", r"\bwhich (?:stock|share)\b",
)
MAX_HEADLINES = 12         # kept per stock, newest first
FETCH_TIMEOUT = 20

# --------------------------------------------------------------------------
# Event vocabulary. Each entry: (tag, side, weight, patterns).
# side RISK = reasons to look again before holding; OPP = constructive.
# The weights are ASSUMED — no calibration exists for news and none can, so
# they are a reading aid, not measured edge.
# --------------------------------------------------------------------------

RISK = "RISK"
OPP = "OPP"

EVENT_RULES: list[tuple[str, str, float, tuple[str, ...]]] = [
    ("results_miss", RISK, 2.0,
     (r"\bprofit (?:falls|drops|declines|slumps|plunges)", r"\bloss widens",
      r"\bmisses? estimates?", r"\bbelow estimates?", r"\bq[1-4] (?:profit|net) down",
      r"\brevenue (?:falls|declines|drops)", r"\bdisappoints?")),
    ("regulatory", RISK, 2.5,
     (r"\bsebi\b", r"\bshow[- ]cause", r"\bpenalt(?:y|ies)", r"\bfined?\b",
      r"\binvestigat(?:ion|ing|es)", r"\braid(?:s|ed)?\b", r"\bprobe\b",
      r"\bnotice from\b", r"\bcbi\b", r"\bed (?:raids?|summons)",
      r"\binsolvency", r"\bnclt\b", r"\bban(?:s|ned)?\b",
      # Owner ask 2026-08-25 ("US FDA data and ongoing sanctions"): the
      # pharma regulator's own vocabulary + sanctions, so these headlines
      # tag as regulatory risk instead of sliding through untagged.
      r"\bus\s?fda\b", r"\bform 483\b", r"\bwarning letter",
      r"\bimport alert", r"\bsanction(?:s|ed)?\b")),
    ("governance", RISK, 2.5,
     (r"\bresign(?:s|ed|ation)", r"\bsteps down",
      r"\bauditor (?:resigns|flags|qualif)",
      r"\b(?:cfo|ceo|md|chairman|chairperson|president|managing director|"
      r"whole[- ]time director|company secretary|cio|cro)\b[^.;]{0,40}"
      r"\b(?:quits|resigns|exits|steps down|departs)",
      # "fraud detection/prevention" is the OPPOSITE of a fraud event — an RBI
      # headline about AI fraud detection tagged a bank as a governance risk
      # before this negative lookahead was added.
      r"\bfraud\b(?!\s*(?:detection|prevention|management|analytics|control))",
      r"\bmisappropriat", r"\bwhistleblower",
      r"\bpledges? (?:more )?shares", r"\bstake sale by promoter")),
    ("downgrade", RISK, 1.5,
     (r"\bdowngrade[sd]?\b", r"\bcuts? target", r"\blowers? target",
      r"\bcut to (?:sell|reduce|underperform)", r"\bunderweight\b")),
    ("rumour", RISK, 1.0,
     (r"\brumou?r", r"\bunconfirmed report", r"\bspeculation\b",
      r"\bdenies? report", r"\bclarifies?\b")),
    ("results_beat", OPP, 2.0,
     (r"\bprofit (?:rises|jumps|surges|soars|up \d)", r"\bbeats? estimates?",
      r"\babove estimates?", r"\bresults? beat", r"\brevenue (?:jumps|surges|rises)",
      r"\brecord (?:profit|revenue)")),
    # NOTE the bounded gaps: Indian headlines routinely read "bags Rs 2,400
    # crore order from Indian Railways", so "bags ... order" needs to match
    # across the deal size. Gaps are capped so the words stay related.
    ("order_win", OPP, 2.5,
     (r"\bwins?\b[^.;]{0,45}\b(?:order|contract|deal|bid|tender)",
      r"\bbags?\b[^.;]{0,45}\b(?:order|contract|deal)",
      r"\bsecures?\b[^.;]{0,45}\b(?:order|contract|deal)",
      r"\bawarded\b[^.;]{0,45}\b(?:order|contract)",
      r"\border (?:win|book|inflow)", r"\blo[ai] (?:received|awarded)")),
    ("upgrade", OPP, 1.5,
     (r"\bupgrade[sd]?\b", r"\braises? target", r"\bhikes? target",
      r"\binitiates? coverage with (?:a )?buy", r"\boverweight\b",
      r"\btop pick", r"\bcut to (?:buy|add|outperform)")),
    ("expansion", OPP, 1.5,
     (r"\bcapacity expansion", r"\bnew plant", r"\bacquires?\b", r"\bacquisition\b",
      r"\bjoint venture", r"\bfundrais", r"\bqip\b", r"\bboard approves")),
    ("block_deal", OPP, 1.0,
     (r"\bblock deal", r"\bbulk deal", r"\bstake (?:buy|purchase)")),
]

# Sentiment lexicon (deterministic). Applied only to words NOT already
# accounted for by an event tag, so a tagged headline is not double counted.
POSITIVE = ("gain", "gains", "rises", "surge", "surges", "jumps", "rally",
            "rallies", "outperform", "strong", "growth", "expands", "profit",
            "record", "high", "buy", "optimistic", "upbeat", "recovery")
NEGATIVE = ("falls", "drops", "slump", "slumps", "plunge", "plunges", "weak",
            "decline", "declines", "loss", "losses", "concern", "concerns",
            "pressure", "sell", "cut", "cuts", "warning", "risk", "crash",
            "downturn", "underperform", "hits low", "slips")

#: A stock reads TRIGGER when its weighted risk or opportunity score reaches
#: this. ASSUMED — see the module docstring on why no calibration is possible.
TRIGGER_SCORE = 2.0


@dataclass
class Headline:
    title: str
    published: str
    source: str
    tags: tuple[str, ...] = ()
    side: str = ""                      # RISK / OPP / "" when only sentiment
    sentiment: int = 0                  # -1 / 0 / +1, lexicon


@dataclass
class NewsReading:
    """One stock's news verdict, as it sticks to that stock."""
    ticker: str
    asof: str
    verdict: str = "PASS"       # TRIGGER | PASS | NO_NEWS | UNKNOWN
    risk_score: float = 0.0
    opp_score: float = 0.0
    tags: tuple[str, ...] = ()
    headlines: list[Headline] = field(default_factory=list)
    sentiment: float = 0.0              # mean lexicon sentiment, -1..+1
    why: str = ""
    fetch_failed: bool = False

    def to_dict(self) -> dict:
        return {
            "ticker": self.ticker, "asof": self.asof, "verdict": self.verdict,
            "risk_score": round(self.risk_score, 2),
            "opp_score": round(self.opp_score, 2),
            "tags": list(self.tags), "sentiment": round(self.sentiment, 3),
            "why": self.why, "fetch_failed": self.fetch_failed,
            "headlines": [
                {"title": h.title, "published": h.published,
                 "source": h.source, "tags": list(h.tags), "side": h.side,
                 "sentiment": h.sentiment}
                for h in self.headlines],
        }


# --------------------------------------------------------------------------
# Classification — pure, deterministic, offline
# --------------------------------------------------------------------------

def classify_headline(title: str) -> tuple[tuple[str, ...], str, float, float, int]:
    """(tags, side, risk_score, opp_score, sentiment) for one headline.

    Pure function: no network, no clock, no model. The same title always
    produces the same answer, which is what makes the verdict auditable.
    """
    low = title.lower()
    tags: list[str] = []
    risk = opp = 0.0
    for tag, side, weight, patterns in EVENT_RULES:
        if any(re.search(p, low) for p in patterns):
            tags.append(tag)
            if side == RISK:
                risk += weight
            else:
                opp += weight
    sent = 0
    if not tags:            # only score sentiment when no event tag fired
        pos = sum(1 for w in POSITIVE if w in low)
        neg = sum(1 for w in NEGATIVE if w in low)
        sent = 1 if pos > neg else (-1 if neg > pos else 0)
    side = ""
    if risk or opp:
        side = RISK if risk >= opp else OPP
    return tuple(tags), side, risk, opp, sent


def judge(ticker: str, headlines: list[Headline], asof: str) -> NewsReading:
    """Turn a stock's headlines into its verdict — TRIGGER or PASS."""
    r = NewsReading(ticker=ticker, asof=asof, headlines=headlines)
    if not headlines:
        r.verdict = "NO_NEWS"
        r.why = f"no headlines in the last {LOOKBACK_DAYS} days"
        return r
    tags: list[str] = []
    sents = []
    for h in headlines:
        h.tags, h.side, hr, ho, h.sentiment = classify_headline(h.title)
        r.risk_score += hr
        r.opp_score += ho
        tags.extend(h.tags)
        sents.append(h.sentiment)
    r.tags = tuple(dict.fromkeys(tags))
    r.sentiment = sum(sents) / len(sents) if sents else 0.0
    top = max(r.risk_score, r.opp_score)
    if top >= TRIGGER_SCORE:
        r.verdict = "TRIGGER"
        which = "risk" if r.risk_score >= r.opp_score else "opportunity"
        r.why = (f"{which} events in the last {LOOKBACK_DAYS}d: "
                 f"{', '.join(r.tags)} (risk {r.risk_score:.1f} / "
                 f"opp {r.opp_score:.1f}) — DISPLAY ONLY, blocks nothing")
    else:
        r.verdict = "PASS"
        r.why = (f"{len(headlines)} headlines, nothing above the event bar "
                 f"(risk {r.risk_score:.1f} / opp {r.opp_score:.1f})")
    return r


# --------------------------------------------------------------------------
# Fetch (the only part that touches the network)
# --------------------------------------------------------------------------

_names: Optional[dict[str, str]] = None


def company_name(ticker: str) -> Optional[str]:
    """'TATASTEEL.NS' -> 'TATA STEEL LTD', from the committed universe file."""
    global _names
    if _names is None:
        _names = {}
        try:
            import csv
            with open(_UNIVERSE_CSV, newline="", encoding="utf-8") as f:
                for row in csv.DictReader(f):
                    t = (row.get("ticker") or "").strip()
                    n = (row.get("name") or "").strip()
                    if t and n:
                        _names[t.upper()] = n
        except OSError as e:
            log.warning("news: universe file unreadable: %s", e)
    return _names.get(ticker.upper())


# Words that say a headline is about a listed company rather than a person
# or a place that happens to share its name. A single name token ("balkrishna",
# "suzlon") is accepted ONLY next to one of these — see _weak_token.
_MARKET_CONTEXT = re.compile(
    r"\b(shares?|stocks?|nse|bse|sensex|nifty|q[1-4]|fy ?\d{2,4}|results?|"
    r"quarter(?:ly)?|orders?|profit|revenue|ebitda|margins?|dividend|market cap|"
    r"ltd|limited|promoters?|ipo|qip|ncds?|debentures?|rating|target price|"
    r"brokerage|buyback|agm|board|listed|investors?|upper circuit|lower circuit)\b"
)


def _strong_phrases(ticker: str) -> tuple[str, ...]:
    """Phrases that by themselves mean the headline is about this stock: the
    trading symbol as a word, or the company's core name (suffixes stripped).
    Google News answers a company query with sector round-ups and unrelated
    pieces — an RBI headline about AI fraud detection was tagging a bank as a
    governance risk until this filter existed.
    """
    sym = ticker.split(".")[0].lower()
    out = [sym]
    core = _core_name(ticker)
    if core:
        out.append(core)
    return tuple(dict.fromkeys(p for p in out if p))


def _core_name(ticker: str) -> str:
    name = (company_name(ticker) or "").lower()
    core = re.sub(r"\b(ltd|limited|india|corp|corporation|company|the)\b\.?",
                  " ", name)
    core = re.sub(r"[^a-z0-9 ]+", " ", core)
    return re.sub(r"\s+", " ", core).strip()


def _weak_token(ticker: str) -> Optional[str]:
    """The company's first name token alone ("suzlon", "balkrishna") — enough
    when prose uses the short name, but NOT on its own.

    Found live 2026-09-08: "CBI court acquits Acharya Balkrishna in passport
    forgery case" (a Patanjali executive) tagged Balkrishna INDUSTRIES with a
    regulatory risk, because the surname alone cleared this rule. A single
    token now counts only when the headline also carries market context
    (share/stock/results/order/...), which a court story about a person does
    not. A short or generic token is never accepted ("yes", "tata" would drag
    in siblings).
    """
    core = _core_name(ticker)
    if not core:
        return None
    first = core.split()[0]
    if first == core:            # one-word company name: already a strong phrase
        return None
    if len(first) >= 6 and first not in ("general", "national", "central"):
        return first
    return None


def _accept_phrases(ticker: str) -> tuple[str, ...]:
    """All phrases the matcher knows for this stock (strong first, then the
    context-gated single token). Kept for callers that only list them."""
    weak = _weak_token(ticker)
    return _strong_phrases(ticker) + ((weak,) if weak else ())


def mentions_company(title: str, ticker: str) -> bool:
    low = title.lower()
    for p in _strong_phrases(ticker):
        if re.search(rf"\b{re.escape(p)}\b", low):
            return True
    weak = _weak_token(ticker)
    if weak and re.search(rf"\b{re.escape(weak)}\b", low):
        return _MARKET_CONTEXT.search(low) is not None
    return False


def _query_for(ticker: str) -> str:
    name = company_name(ticker) or ticker.split(".")[0]
    # trim the corporate suffix — "TATA STEEL LTD" searches worse than
    # "TATA STEEL" for news
    name = re.sub(r"\b(ltd|limited|india|corp|corporation)\b\.?", "", name,
                  flags=re.I).strip()
    return f"{name} share"


def _parse_rss(xml: str) -> list[Headline]:
    out: list[Headline] = []
    for item in re.findall(r"<item>(.*?)</item>", xml, re.S):
        t = re.search(r"<title>(.*?)</title>", item, re.S)
        d = re.search(r"<pubDate>(.*?)</pubDate>", item, re.S)
        s = re.search(r"<source[^>]*>(.*?)</source>", item, re.S)
        if not t:
            continue
        title = html.unescape(re.sub(r"<[^>]+>", "", t.group(1))).strip()
        if not title:
            continue
        if any(re.search(p, title, re.I) for p in NOISE_PATTERNS):
            continue                    # quote page, not news
        out.append(Headline(
            title=title,
            published=(d.group(1).strip()[:25] if d else ""),
            source=(html.unescape(re.sub(r"<[^>]+>", "", s.group(1))).strip()
                    if s else ""),
        ))
    return out


def fetch_headlines(ticker: str, days: int = LOOKBACK_DAYS) -> Optional[list[Headline]]:
    """Google News RSS for one stock. None = the fetch itself failed.

    None and [] mean different things and must not be conflated: None is "we
    do not know", [] is "we looked and there was nothing".
    """
    q = urllib.parse.quote(f"{_query_for(ticker)} when:{days}d")
    url = (f"https://news.google.com/rss/search?q={q}"
           "&hl=en-IN&gl=IN&ceid=IN:en")
    xml = None
    for attempt in range(3):            # Google News 503s intermittently
        try:
            req = urllib.request.Request(url, headers={"User-Agent": UA})
            with urllib.request.urlopen(req, timeout=FETCH_TIMEOUT) as resp:
                xml = resp.read().decode("utf-8", "replace")
            break
        except Exception as e:                              # noqa: BLE001
            last = e
            time.sleep(1.0 * (attempt + 1))
    if xml is None:
        log.warning("news: fetch failed for %s: %s", ticker, last)
        return None
    items = [h for h in _parse_rss(xml) if mentions_company(h.title, ticker)]
    # newest first, bounded
    return items[:MAX_HEADLINES]


def read_stock(ticker: str, asof: str = "") -> NewsReading:
    """Fetch + judge one stock. A failed fetch is marked, never silently PASS."""
    asof = asof or datetime.now(timezone.utc).date().isoformat()
    items = fetch_headlines(ticker)
    if items is None:
        # UNKNOWN, not NO_NEWS: "the fetch broke" and "there is no news" are
        # different facts and must never render as the same chip.
        return NewsReading(ticker=ticker, asof=asof, verdict="UNKNOWN",
                           fetch_failed=True,
                           why="news fetch failed — unknown, not 'no news'")
    return judge(ticker, items, asof)


def run(tickers: list[str], asof: str = "",
        limit: int = 60) -> dict[str, NewsReading]:
    """One pass over the filtered candidate list (the owner's funnel).

    `limit` bounds the pass: the owner's point is that with 40 fundable slots
    there is no sense fetching news for hundreds of names.
    """
    asof = asof or datetime.now(timezone.utc).date().isoformat()
    out: dict[str, NewsReading] = {}
    for t in list(dict.fromkeys(tickers))[:limit]:
        out[t] = read_stock(t, asof)
    return out
