# Sourceborn AM07 — automatic NSE paper-trading engine

Scans the full NSE cash market every trading day, opens and manages
**paper** positions (₹25L practice capital) by tested momentum rules,
and journals every decision it makes — append-only, nothing hidden.
FastAPI + MongoDB Atlas + APScheduler, deployed on Render
(`render.yaml`, always-on 512 MB).

**Paper only.** A hard lock in `trading/brokers/upstox.py` means no
real order can ever be placed without the owner's explicit go-live
approval.

- **Live app:** https://sourceborn-stocks.onrender.com
- **Owner's manual (simple language):** `docs/APP_BRIEF.md`
- **Session brain / working notes:** `CLAUDE.md`
- **Evidence behind every strategy choice:** `docs/PARAM_SEARCH_V1..V4.md`

## Daily rhythm (IST, all automatic)

08:50 universe refresh from Upstox's public master · 09:20 multi-mode
scan (momentum first) · 14:30 top-up · 15:25 intraday square-off +
limit-order expiry · 15:35 mark + exits. The only manual step is the
owner's daily Upstox login (token dies 03:30).

## Develop

```bash
./install.sh                                   # venv + deps
.venv/bin/uvicorn web.main:app --reload        # local app
SB_DISABLE_CATCHUP=1 python -m pytest -q       # test suite
```
