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

CONNECTION_ERRORS = (
    aiohttp.ClientError,
    asyncio.TimeoutError,
    json.JSONDecodeError,
    ValueError,
)
