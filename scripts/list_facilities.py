#!/usr/bin/env python3
"""List EPA CAMD facilities from the EASEY API.

Reads the API key from the EPA_API_KEY environment variable.
Prints a table: facilityId, facilityName, state, number of units, unit ids,
fuel types, and whether CO2 is measured or calculated.

Usage:
    python scripts/list_facilities.py --state TX

Note: The EPA EASEY facilities endpoint returns facility and unit metadata.
CO2 measurement method (measured vs calculated) is included when available.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from typing import Any

# EPA EASEY API base URL
API_BASE = "https://api.epa.gov/easey"

# Facilities endpoint (confirmed from EPA EASEY OpenAPI docs)
FACILITIES_ENDPOINT = "/camd/facilities"

# Units endpoint for detailed unit info
UNITS_ENDPOINT = "/camd/units"


def _get_api_key() -> str:
    """Read API key from environment variable."""
    key = os.environ.get("EPA_API_KEY")
    if not key:
        print("Error: EPA_API_KEY environment variable not set", file=sys.stderr)
        sys.exit(1)
    return key


def _make_request(
    url: str, params: dict[str, str], api_key: str, max_retries: int = 3
) -> list[dict[str, Any]] | None:
    """Make a request with retry logic for 429 and 5xx errors."""
    headers = {
        "Accept": "application/json",
        "X-Api-Key": api_key,
    }

    query = urllib.parse.urlencode(params)
    full_url = f"{url}?{query}"

    for attempt in range(max_retries):
        req = urllib.request.Request(full_url, headers=headers)
        try:
            with urllib.request.urlopen(req, timeout=60) as response:
                if response.status == 200:
                    data = json.loads(response.read().decode("utf-8"))
                    return data.get("data", [])
                else:
                    print(f"  Unexpected status: {response.status}", file=sys.stderr)
        except urllib.error.HTTPError as e:
            if e.code in (429, 500, 502, 503, 504):
                wait = 2**attempt
                print(f"  HTTP {e.code}, retrying in {wait}s...", file=sys.stderr)
                time.sleep(wait)
                continue
            elif e.code == 401:
                print("  Authentication failed (401): check EPA_API_KEY", file=sys.stderr)
                return None
            elif e.code == 404:
                print("  Not found (404)", file=sys.stderr)
                return []
            else:
                print(f"  HTTP {e.code}: {e.reason}", file=sys.stderr)
                return None
        except urllib.error.URLError as e:
            print(f"  Network error: {e.reason}, retrying...", file=sys.stderr)
            time.sleep(2**attempt)
            continue
        except Exception as e:
            print(f"  Unexpected error: {e}", file=sys.stderr)
            return None

    print("  Max retries exceeded", file=sys.stderr)
    return None


def _fetch_facilities(state: str, api_key: str) -> list[dict[str, Any]]:
    """Fetch all facilities for a state."""
    all_facilities: list[dict[str, Any]] = []
    page = 1
    page_size = 1000

    while True:
        params = {
            "state": state,
            "page": str(page),
            "per_page": str(page_size),
        }
        url = API_BASE + FACILITIES_ENDPOINT

        print(f"  Fetching facilities page {page}...", file=sys.stderr)
        facilities = _make_request(url, params, api_key)
        if facilities is None:
            return None
        if not facilities:
            break

        all_facilities.extend(facilities)
        if len(facilities) < page_size:
            break
        page += 1

    return all_facilities


def _fetch_units_for_facility(facility_id: str, api_key: str) -> list[dict[str, Any]]:
    """Fetch all units for a facility."""
    all_units: list[dict[str, Any]] = []
    page = 1
    page_size = 1000

    while True:
        params = {
            "facility_id": facility_id,
            "page": str(page),
            "per_page": str(page_size),
        }
        url = API_BASE + UNITS_ENDPOINT

        print(f"  Fetching units for facility {facility_id} page {page}...", file=sys.stderr)
        units = _make_request(url, params, api_key)
        if units is None:
            return None
        if not units:
            break

        all_units.extend(units)
        if len(units) < page_size:
            break
        page += 1

    return all_units


def _co2_method(unit: dict[str, Any]) -> str:
    """Determine CO2 measurement method from unit data."""
    # EPA EASEY may include co2_monitoring_method or similar
    # Check common field names
    for field in ("co2_monitoring_method", "co2_method", "monitoring_method", "co2_monitor_type"):
        val = unit.get(field)
        if val:
            val_lower = str(val).lower()
            if "measured" in val_lower or "cem" in val_lower:
                return "measured"
            if "calculated" in val_lower or "fuel" in val_lower or "factor" in val_lower:
                return "calculated"
    return "unknown"


def main() -> None:
    parser = argparse.ArgumentParser(
        description="List EPA CAMD facilities and their units for a given state."
    )
    parser.add_argument(
        "--state", required=True, help="Two-letter state code (e.g., TX, CA, OH)"
    )
    args = parser.parse_args()

    if len(args.state) != 2:
        print("Error: --state must be a two-letter state code", file=sys.stderr)
        sys.exit(1)

    state = args.state.upper()
    api_key = _get_api_key()

    print(f"Fetching facilities for state {state}...", file=sys.stderr)
    facilities = _fetch_facilities(state, api_key)
    if facilities is None:
        print("Failed to fetch facilities", file=sys.stderr)
        sys.exit(1)

    if not facilities:
        print(f"No facilities found for state {state}")
        sys.exit(0)

    print(f"Found {len(facilities)} facilities. Fetching units...", file=sys.stderr)

    # Collect data for each facility
    rows: list[dict[str, Any]] = []
    for facility in facilities:
        facility_id = facility.get("facility_id") or facility.get("orispl_code")
        facility_name = facility.get("facility_name") or facility.get("name")

        if not facility_id:
            continue

        units = _fetch_units_for_facility(str(facility_id), api_key)
        if units is None:
            print(f"  Failed to fetch units for facility {facility_id}", file=sys.stderr)
            continue

        unit_ids = [
            str(u.get("unit_id") or u.get("unitid"))
            for u in units
            if u.get("unit_id") or u.get("unitid")
        ]
        fuel_types = set()
        co2_methods = set()

        for unit in units:
            fuel = unit.get("fuel_type") or unit.get("primary_fuel") or unit.get("fuel")
            if fuel:
                fuel_types.add(str(fuel))
            co2_methods.add(_co2_method(unit))

        co2_status = ""
        if "measured" in co2_methods and "calculated" in co2_methods:
            co2_status = "mixed"
        elif "measured" in co2_methods:
            co2_status = "measured"
        elif "calculated" in co2_methods:
            co2_status = "calculated"
        else:
            co2_status = "unknown"

        rows.append({
            "facilityId": facility_id,
            "facilityName": facility_name,
            "state": state,
            "numUnits": len(units),
            "unitIds": ",".join(unit_ids) if unit_ids else "",
            "fuelTypes": ",".join(sorted(fuel_types)) if fuel_types else "unknown",
            "co2Method": co2_status,
        })

    # Print table
    headers = [
        "facilityId",
        "facilityName",
        "state",
        "numUnits",
        "unitIds",
        "fuelTypes",
        "co2Method",
    ]
    col_widths = {h: len(h) for h in headers}
    for row in rows:
        for h in headers:
            col_widths[h] = max(col_widths[h], len(str(row.get(h, ""))))

    # Print header
    header_line = " | ".join(h.ljust(col_widths[h]) for h in headers)
    print(header_line)
    print("-" * len(header_line))

    # Print rows
    for row in rows:
        print(" | ".join(str(row.get(h, "")).ljust(col_widths[h]) for h in headers))


if __name__ == "__main__":
    main()


