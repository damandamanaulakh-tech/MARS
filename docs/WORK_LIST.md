# AM07 WORK LIST — pending / done

Owner's list, owner's words. Updated every session. This file is the board:
open it to see what is done and what is not. Nothing here is built without
the owner's explicit yes.

Last updated: 2026-07-26

---

## A · CANDLES (owner, 2026-07-26, with screenshots)

| # | Item | Status | Note |
|---|---|---|---|
| A1 | 8 candle patterns in code | **DONE** | breakout · bullish engulfing · URR · hammer · morning star · piercing line · tweezer bottom · three white soldiers (`trading/candles.py`) |
| A2 | "10-12 candle structures as parameters for stock selection" | **PENDING** | Missing to reach 12: doji · shooting star · bearish engulfing · evening star · dark cloud cover · tweezer top · three black crows · harami · inverted hammer · marubozu |
| A3 | Bearish/exit candle family | **PENDING — GAP** | All 8 existing patterns are bullish-only. The engine cannot read a topping pattern or use candles for exits at all |
| A4 | Each candle structure = its own tap-able parameter | **PENDING** | Today all 8 collapse into ONE piano lane "candle confirm". Owner wants each structure as a separate parameter with its own pass/fail stock list |
| A5 | Dashboard shows NIFTY candle — owner does not want it | **PENDING** | "why do i need CANDLE OF NIFTY, I NEED SHARE CANDLE" — replace with share candles |
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
