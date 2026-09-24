"""Trading-test defaults.

The market-hours trade gate (owner order 2026-09-10) blocks entries and exits
outside 09:15-15:30 IST. Unit tests construct their own clocks and run at
whatever hour CI wakes up, so the gate is off here and ON in production —
test_market_hours.py pins that the shipped default is gated.
"""

import os

os.environ.setdefault("SB_IGNORE_MARKET_HOURS", "1")
os.environ.setdefault("SB_CHAIN_PACE_S", "0")      # no sleeps between chain names in tests
