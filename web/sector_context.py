"""Independent public identity refresh; no portfolio lock or trading work."""
import json
import threading
from copy import deepcopy
from trading import sector_identity as si
from trading.quote_evidence import utc
from . import db

_lock = threading.Lock()
_last_result = None


def status():
    """No IO: a failed database cannot be used to persist its own failure.

    An atomic reference replacement keeps readers independent of the long
    refresh lock. A restart resets the observation to unknown, never success.
    """
    result = deepcopy(_last_result)
    return {'scope': 'current_process', 'last_attempt': result,
            'durability': ('acknowledged' if result['status'] in
                           ('stored', 'partial', 'already_observed') else 'not_confirmed')
                          if result is not None else 'unknown'}


def _record(result):
    global _last_result
    _last_result = deepcopy(result)
    return result


def refresh(now=None, fetcher=None):
    now = utc(now)
    if not _lock.acquire(blocking=False):
        return {'status': 'already_running'}
    try:
        if not db.is_connected():
            return _record({'status': 'store_unavailable', 'at': now.isoformat()})
        # Do not replace an unreadable prior snapshot with an empty one.
        previous = db.load_sector_identity()
        persisted = previous is not None
        if previous is None:
            previous = json.loads(si.SEED.read_text())
        today = now.date().isoformat()
        # Retry failed sources at the next scheduled slot, even the same day.
        attempts = previous.get('attempts') or {}
        if persisted and len(attempts) == len(si.FILES) and all(
                a.get('status') == 'observed' and a.get('at', '').startswith(today) for a in attempts.values()):
            return _record({'status': 'already_observed', 'at': now.isoformat()})
        fresh = si.refresh(previous, now, fetcher)
        if not db.save_sector_identity(fresh):
            return _record({'status': 'write_unacknowledged', 'at': now.isoformat(),
                            'write': db.sector_identity_write_status()})
        good = sum(a['status'] == 'observed' for a in fresh['attempts'].values())
        return _record({'status': 'stored' if good == len(si.FILES) else 'partial',
                'observed_sources': good, 'required_sources': len(si.FILES), 'at': now.isoformat()})
    except Exception as exc:
        return _record({'status': 'failed_retained', 'error_type': type(exc).__name__, 'at': now.isoformat()})
    finally:
        _lock.release()
