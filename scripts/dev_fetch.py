#!/usr/bin/env python3
"""Fetch Anthropic Admin analytics data outside Home Assistant for development."""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
from datetime import date

from aiohttp import ClientSession

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from custom_components.anthropic_usage_monitor.api import AnthropicAdminClient  # noqa: E402


async def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--start", type=date.fromisoformat, required=True, help="Start date YYYY-MM-DD")
    parser.add_argument("--end", type=date.fromisoformat, required=True, help="End date YYYY-MM-DD")
    args = parser.parse_args()

    key = os.environ.get("ANTHROPIC_ADMIN_KEY")
    if not key:
        print("Set ANTHROPIC_ADMIN_KEY in the environment.", file=sys.stderr)
        return 2

    async with ClientSession() as session:
        client = AnthropicAdminClient(session, key)
        costs = await client.fetch_cost_report(starting_at=args.start, ending_at=args.end)
        usage = await client.fetch_usage_report(starting_at=args.start, ending_at=args.end)
    print(json.dumps({"costs": costs, "usage": usage}, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
