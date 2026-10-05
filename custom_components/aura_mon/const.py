"""Constants for the AuraMon integration."""
from __future__ import annotations

import asyncio
import json

import aiohttp

DOMAIN = "aura_mon"

DEFAULT_TIMEOUT = 10

CONF_WEBHOOK_ID = "webhook_id"

# Live readings arrive via a push webhook (see WEBHOOK_INGESTION.md), not polling. GET
# /status is only used for device identity/firmware metadata, refreshed on: initial setup,
# an unknown device name appearing in a webhook payload, and the two timers below.

# Unconditional periodic /status refresh, so firmware/version/network changes are picked up
# even while webhook payloads keep flowing normally and the known device set never changes.
STATUS_REFRESH_INTERVAL = 1800

# How often the staleness watchdog checks for a missing webhook payload.
STALE_CHECK_INTERVAL = 60
# If no webhook payload has landed for this long, trigger a /status refresh (reachability
# check/diagnostics) and mark entities unavailable, regardless of that refresh's outcome.
STALE_THRESHOLD = 300

CONNECTION_ERRORS = (
    aiohttp.ClientError,
    asyncio.TimeoutError,
    json.JSONDecodeError,
    ValueError,
)
