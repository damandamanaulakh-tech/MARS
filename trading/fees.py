"""Versioned Upstox Basic cash-equity paper charges (SEQ-05).

Published rates verified 2026-11-12; these are estimates, not contract notes.
One paper entry/partial exit is one executed order. Amounts are rounded half-up
per component to paise; broker contract-note/day tax rounding may differ.
Sources and effective dates: docs/repairs/evidence/2026-11-12-cost-sources.json.
"""
from __future__ import annotations

from copy import deepcopy
from datetime import date, datetime, timezone
from decimal import Decimal, ROUND_HALF_UP
from uuid import uuid4
from zoneinfo import ZoneInfo

MODEL = 'upstox-basic-equity-2026-11-12-v1'
CHECKED_ON = '2026-11-12'
IST = ZoneInfo('Asia/Kolkata')
D = lambda x: Decimal(str(x))


def money(value):
    return float(D(value).quantize(D('.01'), rounding=ROUND_HALF_UP))


def day(value):
    if isinstance(value, str):
        value = datetime.fromisoformat(value.replace('Z', '+00:00'))
    if isinstance(value, datetime):
        return (value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value).astimezone(IST).date()
    if isinstance(value, date):
        return value
    raise ValueError('costs_date_unavailable')


def product(mode):
    return 'intraday' if mode == 'intraday' else 'delivery'


def check_instrument(ticker, sector=""):
    """The app's cash-share model cannot certify fund or BSE-group taxes."""
    symbol = str(ticker).upper()
    if not symbol or symbol.startswith('^') or ('.' in symbol and not symbol.endswith('.NS')):
        raise ValueError('costs_exchange_schedule_unavailable')
    if any(x in symbol for x in ('ETF', 'BEES')) or str(sector).upper() in ('ETF', 'FUND', 'REIT', 'INVIT'):
        raise ValueError('costs_asset_schedule_unavailable')


def charge(qty, price, side, product, when, ticker, *, dp=False, delivery_qty=None):
    """Pure single-order charge estimate. NSE equity shares only.

    delivery_qty permits a sell with both same-day and delivered quantities;
    brokerage is still capped once for the whole executed order.
    """
    q, px = D(qty), D(price)
    if not q.is_finite() or not px.is_finite() or q <= 0 or px <= 0 or q != q.to_integral_value():
        raise ValueError('costs_invalid_quantity_or_price')
    if side not in ('BUY', 'SELL') or product not in ('intraday', 'delivery'):
        raise ValueError('costs_invalid_side_or_product')
    check_instrument(ticker)
    trade_day = day(when)
    if trade_day < date(2024, 10, 1):
        raise ValueError('costs_date_before_verified_exchange_schedule')
    turnover = q * px
    exchange, ipft = (D('306.99'), D('.01')) if trade_day >= date(2026, 3, 1) else (D('297'), D('10'))
    brokerage = min(D('20'), turnover * (D('.001') if product == 'intraday' else D('.025')))
    if side == 'BUY':
        stt = turnover * (D('0') if product == 'intraday' else D('.001'))
    else:
        delivered = D(delivery_qty) if delivery_qty is not None else (q if product == 'delivery' else D(0))
        if not delivered.is_finite() or not D(0) <= delivered <= q:
            raise ValueError('costs_invalid_delivery_quantity')
        stt = px * (delivered * D('.001') + (q-delivered) * D('.00025'))
    raw = {'brokerage': brokerage, 'stt': stt,
           'exchange': turnover * exchange / D('1e7'),
           'ipft': turnover * ipft / D('1e7'), 'sebi': turnover * D('10') / D('1e7'),
           'stamp': turnover * (D('.00003') if product == 'intraday' else D('.00015')) if side == 'BUY' else D(0),
           'dp': D('20') if side == 'SELL' and delivered > 0 and dp else D(0)}
    values = {k: money(v) for k, v in raw.items()}
    values['gst'] = money(sum(D(values[k]) for k in ('brokerage', 'exchange', 'ipft', 'sebi', 'dp')) * D('.18'))
    return {'model': MODEL, 'date_ist': trade_day.isoformat(), 'side': side,
            'product': product, 'exchange': 'NSE', 'asset_assumption': 'ordinary_equity_share',
            'qty': int(q), 'delivery_qty': float(delivered) if side == 'SELL' else None, 'turnover': money(turnover),
            'components': values, 'total': money(sum(D(v) for v in values.values())),
            'exchange_schedule': '2026-03-01' if trade_day >= date(2026, 3, 1) else '2024-10-01',
            'tariff_checked_on': CHECKED_ON, 'status': 'paper_estimate'}


def _order(qty, price, mode, when, ticker, *, paid=True):
    p = product(mode)
    fee = charge(qty, price, 'BUY', p, when, ticker)
    alternate = charge(qty, price, 'BUY', 'intraday', when, ticker) if p == 'delivery' else fee
    return {'id': uuid4().hex, 'qty': qty, 'remaining_qty': float(qty),
            'price': price, 'date_ist': day(when).isoformat(), 'product': p,
            'fee': fee, 'remaining_fee': fee['total'],
            'intraday_fee': alternate['total'], 'remaining_intraday_fee': alternate['total'],
            'cash_paid': paid}


def new_basis(qty, price, mode, when, ticker, *, sector=""):
    check_instrument(ticker, sector)
    return {'model': MODEL, 'status': 'paper_estimate', 'orders': [_order(qty, price, mode, when, ticker)]}


def entry_total(qty, price, mode, when, ticker):
    return float(D(qty)*D(price) + D(charge(qty, price, 'BUY', product(mode), when, ticker)['total']))


def affordable_qty(wanted, price, cash, mode, when, ticker):
    """Fee-inclusive cash ceiling; does not change strategy size/risk floors."""
    lo, hi = 0, max(0, min(int(wanted), int(float(cash)//float(price))))
    while lo < hi:
        mid = (lo+hi+1)//2
        if entry_total(mid, price, mode, when, ticker) <= cash + 1e-8:
            lo = mid
        else:
            hi = mid-1
    return lo


def basis_for(pos):
    existing = getattr(pos, 'cost_basis', {}) or {}
    if existing:
        return deepcopy(existing)
    check_instrument(pos.ticker, getattr(pos, 'sector', ''))
    # No old cash or closed-trade record is rewritten. Old entries deferred
    # charges to close. Estimate only the remaining entry allocation, explicitly.
    entry = ((getattr(pos, 'trade_evidence', {}) or {}).get('entry') or {}).get('fill') or {}
    original = entry.get('qty') or getattr(pos, 'orig_qty', 0) or pos.qty
    if getattr(pos, 'pyramid_adds', 0) or original < pos.qty:
        original = pos.qty
    order = _order(original, pos.entry_price, pos.mode, pos.entry_date, pos.ticker, paid=False)
    ratio = D(pos.qty)/D(original)
    order.update(remaining_qty=float(pos.qty), remaining_fee=money(D(order['fee']['total'])*ratio),
                 remaining_intraday_fee=money(D(order['intraday_fee'])*ratio))
    return {'model': MODEL, 'status': 'legacy_entry_estimate', 'orders': [order],
            'note': 'Remaining historical entry estimated under the selected Basic tariff; prior charged trades/cash are preserved. Missing order history is not reconstructed.'}


def add_basis(pos, qty, price, when):
    basis = basis_for(pos)
    remaining = sum(float(o['remaining_qty']) for o in basis['orders'])
    if remaining <= 0: raise ValueError('costs_entry_allocation_exhausted')
    # Reconcile any split before appending a differently priced new order.
    for order in basis['orders']:
        order['remaining_qty'] *= float(pos.qty) / remaining
    basis['orders'].append(_order(qty, price, pos.mode, when, pos.ticker))
    return basis


def _legacy_exit(pos, qty, price, cfg, why):
    """A missing tariff must not disable a protective sale. Expose the estimate.

    Only legacy/unsupported holdings can reach this path; new entries require
    a supported schedule. Historical comparison reports these as unavailable.
    """
    from .costs import trade_costs
    estimate = trade_costs(qty, pos.entry_price, price, cfg)
    basis = deepcopy(getattr(pos, 'cost_basis', {}) or {})
    allocated_paid = 0.0
    for order in basis.get('orders', []):
        fraction = D(qty) / D(pos.qty)
        allocation = money(D(order.get('remaining_fee', 0)) * fraction)
        if order.get('cash_paid'): allocated_paid += allocation
        order['remaining_fee'] = money(D(order.get('remaining_fee', 0)) - D(allocation))
        order['remaining_intraday_fee'] = money(D(order.get('remaining_intraday_fee', 0)) * (1-fraction))
        order['remaining_qty'] *= float(1-fraction)
    return {'model': 'legacy_delivery_estimate', 'status': 'unavailable', 'reason': why,
            'entry_allocated': None, 'sell': None, 'total': estimate,
            'cash_credit': qty*price-estimate+allocated_paid,
            'previously_paid_entry_allocation': money(allocated_paid)}, basis, None


def prepare_exit(portfolio, pos, qty, price, when, cfg):
    """Prepare accounting without mutating cash, holdings, DP state or ledger."""
    if not isinstance(qty, int) or qty <= 0 or qty > pos.qty:
        raise ValueError('costs_invalid_exit_quantity')
    try:
        basis = basis_for(pos)
    except ValueError as exc:
        return _legacy_exit(pos, qty, price, cfg, str(exc))
    orders = basis['orders']
    remaining = sum(float(o['remaining_qty']) for o in orders)
    if remaining <= 0:
        raise ValueError('costs_entry_allocation_exhausted')
    # Split/bonus quantity changes preserve the already booked rupee charge.
    scale = float(pos.qty)/remaining
    for o in orders:
        o['remaining_qty'] *= scale
    left = float(qty); allocations=[]; delivered=0.; deferred=0.; refund=0.; entry_cost=0.
    exit_day = day(when).isoformat()
    overnight = False
    for o in orders:
        if left <= 1e-8: break
        available = o['remaining_qty']
        if available <= 1e-8: continue
        take = min(left, available); ratio = D(take)/D(available)
        booked = o['remaining_fee'] if take == available else money(D(o['remaining_fee'])*ratio)
        intraday = o['remaining_intraday_fee'] if take == available else money(D(o['remaining_intraday_fee'])*ratio)
        same_day = o['date_ist'] == exit_day
        effective = intraday if same_day else booked
        if o['product'] == 'delivery' and not same_day: delivered += take
        if o['product'] == 'intraday' and not same_day: overnight=True
        if o['cash_paid']: refund += booked-effective
        else: deferred += effective
        entry_cost += effective
        allocations.append({'order_id':o['id'], 'qty':take, 'entry_date_ist':o['date_ist'],
                            'booked_allocation':booked,'effective_allocation':effective,
                            'cash_paid_at_buy':o['cash_paid'], 'same_day':same_day})
        o['remaining_qty'] = max(0., available-take)
        o['remaining_fee'] = money(D(o['remaining_fee'])-D(booked))
        o['remaining_intraday_fee'] = money(D(o['remaining_intraday_fee'])-D(intraday))
        left -= take
    dp_key = exit_day+'|'+pos.ticker
    dp_paid = getattr(portfolio, 'cost_dp_days', {}) or {}
    try:
        sell = charge(qty, price, 'SELL', 'delivery' if delivered > 1e-8 else 'intraday',
                      when, pos.ticker, dp=delivered > 1e-8 and dp_key not in dp_paid,
                      delivery_qty=delivered)
    except ValueError as exc:
        return _legacy_exit(pos, qty, price, cfg, str(exc))
    ev = {'model':MODEL, 'status':'overnight_intraday_estimate' if overnight else basis['status'],
          'entry_allocated':money(entry_cost), 'entry_allocations':allocations,
          'entry_refund':money(refund), 'deferred_entry_charge':money(deferred),
          'sell':sell, 'total':money(D(entry_cost)+D(sell['total'])),
          'cash_credit':float(D(qty)*D(price)-D(sell['total'])-D(deferred)+D(refund)),
          'dp_key':dp_key, 'note':basis.get('note','')}
    if overnight:
        ev['note'] += ' Intraday held overnight: product conversion, forced-square-off and penalty charges are unknown; this is an intraday-tariff estimate.'
    dp_change = (dp_key, {'model':MODEL,'date_ist':exit_day,'ticker':pos.ticker,'amount':sell['components']['dp']}) if sell['components']['dp'] else None
    return ev, basis, dp_change


def commit_exit_state(portfolio, pos, basis, dp_change):
    pos.cost_basis = basis
    if dp_change:
        key, value = dp_change
        portfolio.cost_dp_days[key] = value
