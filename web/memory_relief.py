"""Give freed memory back to the operating system (owner "go with memory
fix", 2026-10-05).

WHY. On 2026-10-05 the first two five-category rotations over the 1,205-name
universe ran on the 512 MiB Render box:
  09:20  peak 478 MiB during the scans, then the process HELD 353 MiB while
         idle; the trigger layer loaded its panels on top and Render killed
         the process at 511 MiB (10:09 IST).
  14:30  the process climbed to 494 MiB and HELD it, peak 501.
The scan frees its frames (the rotation already calls gc.collect() after
every category), but glibc keeps freed memory inside the process, spread
over one arena per thread, instead of returning it to the OS. The resident
number therefore ratchets up job after job until something tips it over.

TWO FIXES, neither touching any trading rule:
  1. limit_arenas(): cap glibc's arenas (mallopt M_ARENA_MAX). Every
     scheduler thread otherwise grows its own heap and fragments it. The
     same effect as the MALLOC_ARENA_MAX env var, but set in code so it does
     not depend on a Render dashboard setting being present.
  2. release(label): gc.collect() then malloc_trim(0) — hands the freed
     pages back. Called after every category pass, before the trigger layer
     and after the other heavy jobs.

Both are no-ops off glibc (macOS, musl) and never raise: memory housekeeping
must never be the reason a scan or an exit pass fails.
"""
from __future__ import annotations

import ctypes
import ctypes.util
import gc
import logging
import os
from typing import Optional

log = logging.getLogger(__name__)

M_ARENA_MAX = -8               # glibc mallopt() parameter number
DEFAULT_ARENAS = 2

_LIBC = None
_LIBC_TRIED = False


def _libc():
    """glibc handle, or None where it does not exist (cached)."""
    global _LIBC, _LIBC_TRIED
    if not _LIBC_TRIED:
        _LIBC_TRIED = True
        try:
            lib = ctypes.CDLL(ctypes.util.find_library("c") or "libc.so.6")
            # Only glibc has both; musl/macOS libc lack malloc_trim.
            lib.malloc_trim  # noqa: B018
            lib.mallopt      # noqa: B018
            _LIBC = lib
        except (OSError, AttributeError):
            _LIBC = None
    return _LIBC


def rss_mb() -> Optional[float]:
    """Resident memory of this process in MiB (Linux), else None."""
    try:
        with open("/proc/self/statm") as fh:
            return int(fh.read().split()[1]) * os.sysconf("SC_PAGE_SIZE") / 2 ** 20
    except (OSError, ValueError, IndexError):
        return None


def limit_arenas(n: Optional[int] = None) -> bool:
    """Cap glibc malloc arenas. SB_MALLOC_ARENA_MAX overrides the count;
    0 or a negative value leaves glibc's default alone. True when applied."""
    try:
        if n is None:
            n = int(os.environ.get("SB_MALLOC_ARENA_MAX", DEFAULT_ARENAS))
    except ValueError:
        n = DEFAULT_ARENAS
    if n <= 0:
        return False
    lib = _libc()
    if lib is None:
        return False
    try:
        ok = lib.mallopt(M_ARENA_MAX, int(n)) == 1
    except Exception:                                      # noqa: BLE001
        return False
    if ok:
        log.info("memory: glibc arenas capped at %d", n)
    return ok


def release(label: str = "") -> dict:
    """gc.collect() then malloc_trim(0). Returns {label, before_mb,
    after_mb, trimmed}; never raises."""
    before = rss_mb()
    try:
        gc.collect()
    except Exception:                                      # noqa: BLE001
        pass
    trimmed = False
    lib = _libc()
    if lib is not None:
        try:
            trimmed = bool(lib.malloc_trim(0))
        except Exception:                                  # noqa: BLE001
            trimmed = False
    after = rss_mb()
    if before is not None and after is not None:
        log.info("memory[%s]: %.0f -> %.0f MiB", label, before, after)
    return {"label": label, "before_mb": before, "after_mb": after,
            "trimmed": trimmed}
