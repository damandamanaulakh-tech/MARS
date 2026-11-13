"""Independent public identity refresh; no portfolio lock or trading work."""
import json
import threading
from trading import sector_identity as si
from trading.quote_evidence import utc
from . import db

_lock = threading.Lock()


def refresh(now=None, fetcher=None):
    now = utc(now)
    if not _lock.acquire(blocking=False):
        return {'status': 'already_running'}
    try:
        if not db.is_connected():
            return {'status': 'store_unavailable', 'at': now.isoformat()}
        # Do not replace an unreadable prior snapshot with an empty one.
        previous = db.load_sector_identity()
        if previous is None:
            previous = json.loads(si.SEED.read_text())
        today = now.date().isoformat()
        # Retry failed sources at the next scheduled slot, even the same day.
        attempts = previous.get('attempts') or {}
        if len(attempts) == len(si.FILES) and all(
                a.get('status') == 'observed' and a.get('at', '').startswith(today) for a in attempts.values()):
            return {'status': 'already_observed', 'at': now.isoformat()}
        fresh = si.refresh(previous, now, fetcher)
        if not db.save_sector_identity(fresh):
            return {'status': 'write_unacknowledged', 'at': now.isoformat()}
        good = sum(a['status'] == 'observed' for a in fresh['attempts'].values())
        return {'status': 'stored' if good == len(si.FILES) else 'partial',
                'observed_sources': good, 'required_sources': len(si.FILES), 'at': now.isoformat()}
    except Exception as exc:
        return {'status': 'failed_retained', 'error_type': type(exc).__name__, 'at': now.isoformat()}
    finally:
        _lock.release()
