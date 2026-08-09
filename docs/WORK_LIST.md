# AM07 WORK LIST — pending / done

> **FROZEN BOARD (2026-08-09 audit):** this list stopped being updated on 2026-07-27 and six shipping sessions are missing. B3 (weekly universe/data renewal) SHIPPED 2026-07-27. For live status read CLAUDE.md and docs/AUDIT_2026-08-09.md — not this file.


Owner's list, owner's words. Updated every session. This file is the board:
open it to see what is done and what is not. Nothing here is built without
the owner's explicit yes.

Last updated: 2026-07-27 (A2-A5 statuses corrected — they shipped in a185d94)

---

## A · CANDLES (owner, 2026-07-26, with screenshots)

| # | Item | Status | Note |
|---|---|---|---|
| A1 | 8 candle patterns in code | **DONE** | breakout · bullish engulfing · URR · hammer · morning star · piercing line · tweezer bottom · three white soldiers (`trading/candles.py`) |
| A2 | "10-12 candle structures as parameters for stock selection" | **DONE** (a185d94) | 18 structures live: original 8 + 6 bearish + 2 neutral + 2 bullish |
| A3 | Bearish/exit candle family | **DONE** (a185d94) | 6 bearish structures added, read as warnings (never trigger buys) |
| A4 | Each candle structure = its own tap-able parameter | **DONE** (a185d94) | Every structure = own piano lane + own page with pass/fail list |
| A5 | Dashboard shows NIFTY candle — owner does not want it | **DONE** (a185d94) | Big chart = a held share (or top pick), one-tap switching; NIFTY fetch gone |
| A6 | Per-stock candle in position detail | **DONE** | Works (MANAPPURAM screenshot, LAST 20 SESSIONS) |

---

## B · OWNER'S NUMBERED LIST (2026-07-26)

| # | Item (owner's words) | Status | Needed from owner |
|---|---|---|---|
| B0 | Close this session properly — "old things are coming up now" | **PENDING** | Say go; then open a new session |
| B1 | "Mobile app kind of app for routine so I can do login n all from there n must see whatever is there on web" | **PARTIAL** | Installed + drawer + phone layout live. Owner to verify screen by screen and report what is wrong |
| B2 | "existing app web page all parameters reviews and its work" | **PENDING** | — |
| B3 | "every week full new universe" | **PENDING** | Confirm: full price-history rebuild weekly, or just the stock list? (list already refreshes daily) |
| B4 | "shortlist apps which will show shortlisting date and todays P&L" | **PENDING** | Confirm: momentum picks only, or all modes? |
| B5 | "many more changes on dashboard, candle, signals, engine, etc big list" | **PENDING** | Owner's list |
| B6 | "work on parameters" | **PENDING** | Owner's picks after seeing suggestions |
| B7 | Leave this session / fix old-things-resurfacing | **PENDING** | = B0 |
| B8 | "ur trust, which i cant see anywhere" | **ANSWERED** | Standing rule: every number ships with its script + data file, in git |
| B9 | "ur use, as i chat, project & code" | **ANSWERED** | Claude Code = runs code, reads repo, pushes git, hits Upstox. Chat = talk only. Project = files + rules, no execution |
| B10 | "reasoning better here than chat?" | **ANSWERED** | Yes for this work — answers computed from real data, not memory |
| B11 | "ur suggestions for parameters, never heard" | **DELIVERED, UNTESTED** | ATR trail · structure stop (below swing low) · time-stop · tranche entry · regime-scaled size |
| B12 | "ur unwanted logics changes my wording and use some shit excuse" | **BINDING RULE** | Use owner's words. No examples unless asked. No justifying |

---

## C · WIRED THIS SESSION (2026-07-26, owner-approved)

| Item | Status |
|---|---|
| Stop −5% (owner order; UNTESTED number) | **DONE** |
| Target +12% kept | **DONE** |
| N7 up-day-only volume expansion | **DONE** |
| N1 BE/T2T series-watch shadow list | **DONE** |
| Flags bundle (cascade · sector-rout · F1 turn-confirm) | **DONE** |
| Filings feed v1 (09:05, BSE) | **DONE — feed returns empty to cloud IP, needs tuning** |
| Complete research universe (2,392 stocks × 7.5y) | **DONE** |
| Phone app: install, drawer nav, 14-template layout pass | **DONE** |

---

## D · PARKED (no action without owner's word)

- Ocean engine crash-guard redesign
- Filings feed source decision (free BSE tuning vs paid feed)
- Actual-family parameters (A/B/C/D families) — all wait on the feed
- Go-live: 60-day proof + owner's word, then real orders small + kill switch
