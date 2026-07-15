"""Upstox/Zerodha CNC equity delivery cost model. Applied at trade close."""

from .config import Config


def slippage_price(price: float, side: str, cfg: Config) -> float:
    slip = price * cfg.slippage_bps / 10_000
    return price + slip if side == "BUY" else price - slip


def trade_costs(qty: int, buy_price: float, sell_price: float, cfg: Config) -> float:
    buy_turnover = qty * buy_price
    sell_turnover = qty * sell_price

    brokerage = 0.0  # CNC delivery: zero on Upstox/Zerodha
    stt = buy_turnover * cfg.stt_buy_pct + sell_turnover * cfg.stt_sell_pct
    exchange = (buy_turnover + sell_turnover) * cfg.exchange_pct
    sebi = (buy_turnover + sell_turnover) * cfg.sebi_per_cr / 1e7
    stamp = buy_turnover * cfg.stamp_buy_pct
    gst = (brokerage + exchange + sebi) * cfg.gst_pct
    dp = cfg.dp_charges
    return brokerage + stt + exchange + sebi + stamp + gst + dp
