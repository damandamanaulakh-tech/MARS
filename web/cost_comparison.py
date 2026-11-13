"""Read-only counterfactual fees; never restates the financial ledger (SEQ-05)."""
from __future__ import annotations

from collections import defaultdict
from copy import deepcopy
from types import SimpleNamespace
import math
import hashlib

from trading import fees
from . import db

ASSUMPTION = (
    'Selected Upstox Basic tariff observed 12 Nov 2026, applied to historical orders '
    'with dated NSE statutory rates. This is a counterfactual paper estimate, not proof '
    'of the account tariff on those dates. One recorded partial exit = one sell order. '
    'DP is allocated once per scrip/IST day among included sales. Missing archive rows '
    'can change entry and DP allocations. Rounding: half-up per component to paise. '
    'No cash, trade, return, risk or stored backtest is rewritten.'
)
MODES = {'intraday', 'short', 'swing', 'positional', 'nifty200_momentum', 'ocean'}


def _minimal(value):
    """Copy just fee inputs; never copy thousands of large signal snapshots."""
    get = value.get if isinstance(value, dict) else lambda k: getattr(value, k, None)
    d = {k: get(k) for k in ('ticker','qty','entry_date','exit_date','entry_price','exit_price',
         'gross_pnl','costs','net_pnl','mode','sector','reason','ca_applied','cost_evidence')}
    for key in ('entry_date','exit_date'):
        if hasattr(d[key], 'isoformat'): d[key] = d[key].isoformat()
    ev = get('trade_evidence') or {}
    d['trade_evidence'] = {'trade_id':ev.get('trade_id'),
        'entry':{'fill':{'qty':((ev.get('entry') or {}).get('fill') or {}).get('qty')}},
        'amendments':[{'reason':a.get('reason')} for a in ev.get('amendments',[])]}
    return deepcopy(d)


def _group(d):
    ident = (d.get('trade_evidence') or {}).get('trade_id')
    # Old entries have no order identity. Equal timestamp/price is only an estimate.
    return (d.get('ticker'), ident or (str(d.get('entry_date')), d.get('entry_price'), d.get('mode')))


def _finite(value):
    value = float(value)
    if not math.isfinite(value):
        raise ValueError('nonfinite_ledger_value')
    return value


def _display_number(value):
    try: return _finite(value)
    except (ValueError, TypeError): return None


def compare(trades, positions=(), *, coverage=None):
    """Pure comparison over supplied copies; unsupported rows remain in the table."""
    docs = [_minimal(t) for t in trades]
    held = [_minimal(p) for p in positions]
    groups = defaultdict(list)
    for d in docs: groups[_group(d)].append(d)
    open_qty = defaultdict(int)
    for p in held: open_qty[_group(p)] += int(p.get('qty') or 0)
    books = {}; group_errors = {}; identity = {}
    for key, rows in groups.items():
        first = rows[0]
        if all((r.get('cost_evidence') or {}).get('model') == fees.MODEL for r in rows):
            continue
        try:
            if first.get('mode') not in MODES:
                raise ValueError('segment_unavailable')
            if any(r.get('ca_applied') or any(a.get('reason') == 'PYRAMID_ADD' for a in (r.get('trade_evidence') or {}).get('amendments', [])) for r in rows):
                raise ValueError('historical_order_or_corporate_action_allocation_unavailable')
            observed = sum(int(r['qty']) for r in rows) + open_qty[key]
            fill = ((first.get('trade_evidence') or {}).get('entry') or {}).get('fill') or {}
            original = int(fill.get('qty') or observed)
            if original < observed or original <= 0:
                raise ValueError('historical_entry_quantity_inconsistent')
            basis = fees.new_basis(original, _finite(first['entry_price']), first['mode'], first['entry_date'], first['ticker'], sector=first.get('sector',''))
            basis['orders'][0]['id'] = 'comparison:' + hashlib.sha256(repr(key).encode()).hexdigest()[:24]
            books[key] = SimpleNamespace(ticker=first['ticker'], qty=original,
                entry_price=first['entry_price'], entry_date=first['entry_date'],
                mode=first['mode'], sector=first.get('sector',''), cost_basis=basis)
            identity[key] = ('Saved original entry quantity' if fill.get('qty') else 'Entry quantity inferred from included exits and current holding')
            if not (first.get('trade_evidence') or {}).get('trade_id'):
                identity[key] += '; order grouping inferred from ticker, entry timestamp, price and segment'
        except (ValueError, TypeError, KeyError) as exc:
            group_errors[key] = str(exc)
    # Stable timestamp order makes the DP attribution deterministic. No row is dropped.
    docs.sort(key=lambda d: str(d.get('exit_date') or ''))
    portfolio = SimpleNamespace(cost_dp_days={})
    out=[]
    for d in docs:
        row = {k:d.get(k) for k in ('ticker','mode','qty','entry_date','exit_date','reason')}
        row.update(stored_costs=_display_number(d.get('costs')), stored_net=_display_number(d.get('net_pnl')),
                   modeled_costs=None, modeled_net=None, cost_delta=None,
                   status='unavailable', note='', details={})
        key = _group(d)
        try:
            for f in ('costs','net_pnl','gross_pnl','entry_price','exit_price'):
                _finite(d[f])
            if key in group_errors: raise ValueError(group_errors[key])
            ev = d.get('cost_evidence') or {}
            if ev.get('model') == fees.MODEL:
                # Entry order snapshots include pyramids/splits/refunds. Reuse their
                # allocation; never manufacture a new buy order per partial sale.
                delivered = ev['sell']['delivery_qty']
                if delivered is None: raise ValueError('delivery_quantity_unavailable')
                dp_key = fees.day(d['exit_date']).isoformat()+'|'+d['ticker']
                sell = fees.charge(d['qty'],d['exit_price'],'SELL',ev['sell']['product'],d['exit_date'],d['ticker'],
                                   delivery_qty=delivered,dp=delivered>0 and dp_key not in portfolio.cost_dp_days)
                total = fees.money(ev['entry_allocated']+sell['total'])
                detail = {'entry_allocated':ev['entry_allocated'],'sell':sell,'total':total}
                if sell['components']['dp']: portfolio.cost_dp_days[dp_key]=True
                status = ev.get('status','paper_estimate')
                note = 'Saved entry allocation; selected-tariff sell estimate. '+ev.get('note','')
            else:
                pos = books[key]
                if fees.day(d['exit_date']) < fees.day(d['entry_date']):
                    raise ValueError('exit_precedes_entry')
                detail, basis, dp_change = fees.prepare_exit(portfolio,pos,int(d['qty']),d['exit_price'],d['exit_date'],None)
                if detail['status'] == 'unavailable': raise ValueError(detail['reason'])
                fees.commit_exit_state(portfolio,pos,basis,dp_change); pos.qty -= int(d['qty'])
                total = detail['total']; status = 'historical_estimate'
                note = identity[key]+'. '+detail.get('note','')
                if detail['status'] == 'overnight_intraday_estimate': status = detail['status']
            row.update(modeled_costs=total,modeled_net=fees.money(d['gross_pnl']-total),
                       cost_delta=fees.money(total-d['costs']),status=status,note=note,details=detail)
        except (ValueError,TypeError,KeyError,AttributeError) as exc:
            row['note'] = str(exc)
        out.append(row)
    modeled = [r for r in out if r['modeled_costs'] is not None]
    return {'model':fees.MODEL,'tariff_checked_on':fees.CHECKED_ON,'assumption':ASSUMPTION,
            'coverage':coverage or {'archive_status':'not_requested','scope':'supplied rows only'},
            'rows':out, 'summary':{'rows':len(out),'modeled':len(modeled),'unavailable':len(out)-len(modeled),
                'stored_costs_comparable':fees.money(sum(r['stored_costs'] for r in modeled)),
                'modeled_costs':fees.money(sum(r['modeled_costs'] for r in modeled)),
                'stored_net_comparable':fees.money(sum(r['stored_net'] for r in modeled)),
                'modeled_net':fees.money(sum(r['modeled_net'] for r in modeled))}}


def view(portfolio):
    archived = db.cost_comparison_archive()
    # Match the archive's existing stable identity. Prefer retained data when a
    # save/trim retry leaves the same trade in both stores. Never dedup within one.
    retained = [_minimal(t) for t in list(portfolio.trades)]
    keys = {db._trade_key(d) for d in retained}
    rows = [d for d in archived['rows'] if db._trade_key(d) not in keys]+retained
    coverage = {k:v for k,v in archived.items() if k!='rows'}
    coverage.update(retained=len(retained),included=len(rows),scope='Retained and bounded archive, including pre-MARS')
    return compare(rows,list(portfolio.positions.values()),coverage=coverage)
