"""Constants for the AuraMon integration."""
from __future__ import annotations

import asyncio
import json

import aiohttp

DOMAIN = "aura_mon"

DEFAULT_TIMEOUT = 10
# Lower bound on how often we poll the device, regardless of its datalog interval.
MIN_UPDATE_INTERVAL = 5
# Upper bound so a misbehaving/huge datalog interval doesn't stall updates for too long.
MAX_UPDATE_INTERVAL = 60
# Fallback used if the device hasn't reported a datalog interval yet.
DEFAULT_UPDATE_INTERVAL = 30

# If our last synced datalog timestamp is further behind the device's current one than
# this, don't bother trying to catch up row-by-row - just skip ahead. Losing some history
# is fine; we don't want long/expensive catch-up reads for no reason.
MAX_CATCH_UP_AGE = 3600
# When skipping ahead (see MAX_CATCH_UP_AGE), resume this far behind the device's current
# datalog timestamp instead of jumping all the way to it, so a snapshot of very recent
# energy deltas isn't lost.
CATCH_UP_RESYNC_OFFSET = 600

CONNECTION_ERRORS = (
    aiohttp.ClientError,
    asyncio.TimeoutError,
    json.JSONDecodeError,
    ValueError,
)
