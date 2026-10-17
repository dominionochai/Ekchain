"""Climate forensics MCP tools for EPA CEMS data analysis.

Tools for parsing, validating, and analyzing hourly CEMS emissions data
from EPA's Clean Air Markets program.
"""

from __future__ import annotations

import csv
import logging
import time
from collections import defaultdict
from pathlib import Path
from typing import Any

from ekchain.path_policy import PathPolicyError, resolve_allowed_path
from ekchain.server.app import get_ctx, mcp
from ekchain.server.extract_helpers import extract_and_index
from ekchain.server.helpers import hash_output, make_tool_call_id
from ekchain.server.tool_access import EXECUTORS, tool_access

logger = logging.getLogger(__name__)

_REQUIRED_COLUMNS = (
    "facilityId",
    "facilityName",
    "unitId",
    "date",
    "hour",
    "grossLoad",
    "heatInput",
    "co2Mass",
    "so2Mass",
    "noxMass",
)

_OPTIONAL_COLUMNS = (
    "stateCode",
    "unitType",
    "primaryFuelInfo",
    "secondaryFuelInfo",
    "heatInputMeasureFlg",
    "co2MassMeasureFlg",
    "so2MassMeasureFlg",
    "noxMassMeasureFlg",
    "opTime",
    "unitType",
)


def _resolve_evidence_file(path: str) -> Path:
    """Resolve a path within the active evidence roots."""
    from ekchain.server.app import get_cfg, get_ctx

    cfg = get_cfg()
    allowed_roots = [Path(cfg.db_dir)]
    ctx = get_ctx()
    meta = ctx.db.get_case_metadata()
    if meta and meta.evidence_root:
        allowed_roots.append(Path(meta.evidence_root))
    return resolve_allowed_path(Path(path), allowed_roots)


def _read_cems_csv(file_path: Path) -> list[dict[str, str]]:
    """Read CEMS CSV file and return rows as list of dicts."""
    rows: list[dict[str, str]] = []
    with open(file_path, encoding="utf-8", newline="") as f:
        reader = csv.DictReader(f)
        for row in reader:
            rows.append(row)
    return rows


def _validate_cems_rows(rows: list[dict[str, str]]) -> tuple[list[str], list[str]]:
    """Validate that all required columns exist. Returns (missing, extra)."""
    if not rows:
        return list(_REQUIRED_COLUMNS), []
    present = set(rows[0].keys())
    required = set(_REQUIRED_COLUMNS)
    missing = sorted(required - present)
    extra = sorted(present - required)
    return missing, extra


def _to_float(val: str | None) -> float | None:
    """Convert string to float, returning None for empty/invalid."""
    if val is None or val == "":
        return None
    try:
        return float(val)
    except ValueError:
        return None


def _parse_date_hour(date_str: str, hour_str: str) -> str | None:
    """Parse date and hour into ISO datetime string."""
    try:
        from datetime import datetime

        hour = int(hour_str)
        if hour < 0 or hour > 23:
            return None
        dt = datetime.strptime(date_str, "%Y-%m-%d")
        dt = dt.replace(hour=hour)
        return dt.isoformat()
    except Exception:
        return None


def _sort_key(row: dict[str, str]) -> tuple[str, int]:
    """Sort key for CEMS rows by date and hour."""
    hour_str = row.get("hour", "")
    hour = int(hour_str) if hour_str.isdigit() else 0
    return (row.get("date", ""), hour)


@mcp.tool()
@tool_access(EXECUTORS)
def parse_cems_hourly(source_path: str) -> dict[str, object]:
    """Parse EPA CEMS hourly CSV and index into the case database.

    Expected columns: facilityId, facilityName, unitId, date, hour,
    grossLoad, heatInput, co2Mass, so2Mass, noxMass. Extra columns are
    preserved. Units: co2Mass in SHORT TONS, heatInput in mmBtu, grossLoad in MW.

    Optional EPA CEMS columns read when present: stateCode, unitType,
    primaryFuelInfo, secondaryFuelInfo, heatInputMeasureFlg,
    co2MassMeasureFlg, so2MassMeasureFlg, noxMassMeasureFlg, opTime.

    For each unit, reports a count of hours per co2MassMeasureFlg value
    (e.g. "Measured", "Calculated", "Substitute", "Measured and Substitute").

    Args:
        source_path: Path to the CEMS hourly CSV file.

    Returns:
        Summary with row count, per-unit time ranges, missing hours,
        null/negative values, zero-load hours, per-flag counts, and totals
        per unit.
    """
    ctx = get_ctx()
    tc_id = make_tool_call_id()
    t0 = time.monotonic()

    try:
        file_path = _resolve_evidence_file(source_path)
    except PathPolicyError as exc:
        return {
            "tool_call_id": tc_id,
            "status": "error",
            "error_message": str(exc),
        }

    if not file_path.exists():
        elapsed = (time.monotonic() - t0) * 1000
        ctx.audit.log_tool_call(
            tool_call_id=tc_id,
            tool_name="parse_cems_hourly",
            params={"source_path": source_path},
            output_hash=hash_output({"error": "not found"}),
            duration_ms=elapsed,
        )
        return {
            "tool_call_id": tc_id,
            "status": "error",
            "error_message": f"File not found: {source_path}",
        }

    rows = _read_cems_csv(file_path)
    missing, extra = _validate_cems_rows(rows)

    if missing:
        elapsed = (time.monotonic() - t0) * 1000
        ctx.audit.log_tool_call(
            tool_call_id=tc_id,
            tool_name="parse_cems_hourly",
            params={"source_path": source_path},
            output_hash=hash_output({"error": "missing_columns", "missing": missing}),
            duration_ms=elapsed,
        )
        return {
            "tool_call_id": tc_id,
            "status": "error",
            "error_message": f"Missing required columns: {missing}",
            "extra_columns": extra,
        }

    unit_data: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        unit_id = row.get("unitId", "")
        unit_data[unit_id].append(row)

    per_unit_summary: dict[str, dict[str, object]] = {}
    all_missing_hours: list[dict[str, object]] = []

    for unit_id, u_rows in unit_data.items():
        u_rows_sorted = sorted(u_rows, key=_sort_key)

        timestamps = []
        null_negative_count = 0
        zero_load_count = 0
        co2_vals: list[float] = []
        heat_vals: list[float] = []
        load_vals: list[float] = []
        co2_flag_counts: dict[str, int] = defaultdict(int)

        for r in u_rows_sorted:
            ts = _parse_date_hour(r.get("date", ""), r.get("hour", ""))
            if ts:
                timestamps.append(ts)

            co2 = _to_float(r.get("co2Mass"))
            heat = _to_float(r.get("heatInput"))
            load = _to_float(r.get("grossLoad"))

            if co2 is not None:
                co2_vals.append(co2)
                if co2 < 0:
                    null_negative_count += 1
            else:
                null_negative_count += 1

            if heat is not None:
                heat_vals.append(heat)
                if heat < 0:
                    null_negative_count += 1
            else:
                null_negative_count += 1

            if load is not None:
                load_vals.append(load)
                if load == 0:
                    zero_load_count += 1
            else:
                null_negative_count += 1

            flag_val = r.get("co2MassMeasureFlg", "")
            if flag_val:
                co2_flag_counts[flag_val] += 1

        if len(timestamps) >= 2:
            from datetime import datetime, timedelta

            dt_objs = [datetime.fromisoformat(ts) for ts in timestamps]
            expected = set()
            start = min(dt_objs)
            end = max(dt_objs)
            current = start
            while current <= end:
                expected.add(current.isoformat())
                current += timedelta(hours=1)
            actual = set(timestamps)
            missing = sorted(expected - actual)
            for m in missing:
                all_missing_hours.append({"unitId": unit_id, "missing_hour": m})

        per_unit_summary[unit_id] = {
            "row_count": len(u_rows),
            "time_range": {
                "start": min(timestamps) if timestamps else None,
                "end": max(timestamps) if timestamps else None,
            },
            "missing_hours": len(all_missing_hours),
            "null_or_negative_values": null_negative_count,
            "zero_load_hours": zero_load_count,
            "co2_measure_flag_counts": dict(co2_flag_counts),
            "totals": {
                "co2Mass_short_tons": round(sum(co2_vals), 3) if co2_vals else 0.0,
                "heatInput_mmBtu": round(sum(heat_vals), 3) if heat_vals else 0.0,
                "grossLoad_MWh": round(sum(load_vals), 3) if load_vals else 0.0,
            },
        }

    present_optional = [c for c in _OPTIONAL_COLUMNS if rows and c in rows[0]]
    index_columns = list(_REQUIRED_COLUMNS) + present_optional

    raw_output = "\n".join(
        ",".join(r.get(c, "") for c in index_columns) for r in rows
    )

    source_name = f"cems_hourly.{file_path.stem}"
    index_result = extract_and_index(
        raw_output=raw_output,
        source_name=source_name,
        source_path=str(file_path),
        extractor_name="cems_parser",
    )

    elapsed = (time.monotonic() - t0) * 1000
    ctx.audit.log_tool_call(
        tool_call_id=tc_id,
        tool_name="parse_cems_hourly",
        params={"source_path": source_path},
        output_hash=hash_output({"units": list(per_unit_summary.keys())}),
        duration_ms=elapsed,
    )

    return {
        "tool_call_id": tc_id,
        "status": "success",
        "source_path": str(file_path),
        "total_rows": len(rows),
        "extra_columns": extra,
        "optional_columns_present": present_optional,
        "per_unit": per_unit_summary,
        "indexed_source": index_result.get("source_name"),
        "windows_indexed": index_result.get("windows_indexed", 0),
    }


@mcp.tool()
@tool_access(EXECUTORS)
def check_emissions_load_consistency(
    source_path: str,
    unit_id: str,
    window_hours: int = 24,
    tolerance_pct: float = 15.0,
) -> dict[str, object]:
    """Check hourly CO2/heatInput ratio stability for a unit.

    A stable co2Mass/heatInput ratio is expected ONLY when CO2 is
    measured (CEMS). If values are calculated from fuel factors, the
    ratio is stable by construction and flags prove nothing.

    Baseline and flags use ONLY hours where co2MassMeasureFlg == "Measured".
    If fewer than 70% of a unit's hours are Measured, returns status
    "inconclusive" with the measured fraction. If the co2MassMeasureFlg
    column is absent entirely, returns status "inconclusive: no measure flag".

    Compares each rolling window against the unit's own rolling median
    ratio. Flags windows beyond tolerance_pct, plus hours where emissions
    drop while heatInput/grossLoad stay flat or rise.

    Args:
        source_path: Path to the CEMS hourly CSV file.
        unit_id: Unit identifier to analyze.
        window_hours: Rolling window size in hours (default 24).
        tolerance_pct: Deviation tolerance from median ratio in percent (default 15%).

    Returns:
        Structured flags with start, end, observed ratio, baseline ratio,
        deviation %, and severity score. May return "inconclusive" status
        if insufficient Measured hours or no measure flag column.
    """
    ctx = get_ctx()
    tc_id = make_tool_call_id()
    t0 = time.monotonic()

    try:
        file_path = _resolve_evidence_file(source_path)
    except PathPolicyError as exc:
        return {
            "tool_call_id": tc_id,
            "status": "error",
            "error_message": str(exc),
        }

    if not file_path.exists():
        elapsed = (time.monotonic() - t0) * 1000
        ctx.audit.log_tool_call(
            tool_call_id=tc_id,
            tool_name="check_emissions_load_consistency",
            params={"source_path": source_path, "unit_id": unit_id},
            output_hash=hash_output({"error": "not found"}),
            duration_ms=elapsed,
        )
        return {
            "tool_call_id": tc_id,
            "status": "error",
            "error_message": f"File not found: {source_path}",
        }

    rows = _read_cems_csv(file_path)
    missing, _ = _validate_cems_rows(rows)
    if missing:
        elapsed = (time.monotonic() - t0) * 1000
        ctx.audit.log_tool_call(
            tool_call_id=tc_id,
            tool_name="check_emissions_load_consistency",
            params={"source_path": source_path, "unit_id": unit_id},
            output_hash=hash_output({"error": "missing_columns", "missing": missing}),
            duration_ms=elapsed,
        )
        return {
            "tool_call_id": tc_id,
            "status": "error",
            "error_message": f"Missing required columns: {missing}",
        }

    unit_rows = [r for r in rows if r.get("unitId") == unit_id]
    if not unit_rows:
        elapsed = (time.monotonic() - t0) * 1000
        ctx.audit.log_tool_call(
            tool_call_id=tc_id,
            tool_name="check_emissions_load_consistency",
            params={"source_path": source_path, "unit_id": unit_id},
            output_hash=hash_output({"error": "unit_not_found"}),
            duration_ms=elapsed,
        )
        return {
            "tool_call_id": tc_id,
            "status": "error",
            "error_message": f"Unit {unit_id} not found in data",
        }

    unit_rows.sort(key=_sort_key)

    # --- CO2 measurement flag gate ---
    # Baseline and flags use ONLY hours where co2MassMeasureFlg == "Measured".
    if "co2MassMeasureFlg" not in rows[0]:
        elapsed = (time.monotonic() - t0) * 1000
        note = (
            "co2MassMeasureFlg column is absent; cannot determine whether CO2 "
            "was measured (CEMS) or calculated from fuel factors. Baseline "
            "and flags are inconclusive."
        )
        ctx.audit.log_tool_call(
            tool_call_id=tc_id,
            tool_name="check_emissions_load_consistency",
            params={"source_path": source_path, "unit_id": unit_id},
            output_hash=hash_output({"status": "inconclusive_no_flag"}),
            duration_ms=elapsed,
        )
        return {
            "tool_call_id": tc_id,
            "status": "inconclusive: no measure flag",
            "unit_id": unit_id,
            "note": note,
        }

    measured_rows = [
        r for r in unit_rows if r.get("co2MassMeasureFlg") == "Measured"
    ]
    total_hour_count = len(unit_rows)
    measured_count = len(measured_rows)
    measured_fraction = (
        measured_count / total_hour_count if total_hour_count else 0.0
    )

    if measured_fraction < 0.70:
        elapsed = (time.monotonic() - t0) * 1000
        note = (
            f"Only {measured_fraction:.1%} of hours have "
            "co2MassMeasureFlg == 'Measured' (need >= 70%). "
            "Flags are inconclusive; calculated/substitute values would make "
            "the ratio artificially stable."
        )
        ctx.audit.log_tool_call(
            tool_call_id=tc_id,
            tool_name="check_emissions_load_consistency",
            params={"source_path": source_path, "unit_id": unit_id},
            output_hash=hash_output({"status": "inconclusive_low_measured"}),
            duration_ms=elapsed,
        )
        return {
            "tool_call_id": tc_id,
            "status": "inconclusive",
            "unit_id": unit_id,
            "measured_fraction": round(measured_fraction, 4),
            "hours_total": total_hour_count,
            "hours_measured": measured_count,
            "note": note,
        }

    # Use only Measured rows for baseline and flags
    unit_rows = measured_rows

    ratios: list[float | None] = []
    timestamps: list[str] = []
    heat_vals: list[float | None] = []
    load_vals: list[float | None] = []
    co2_vals: list[float | None] = []

    for r in unit_rows:
        ts = _parse_date_hour(r.get("date", ""), r.get("hour", ""))
        timestamps.append(ts if ts else "")

        co2 = _to_float(r.get("co2Mass"))
        heat = _to_float(r.get("heatInput"))
        load = _to_float(r.get("grossLoad"))

        co2_vals.append(co2)
        heat_vals.append(heat)
        load_vals.append(load)

        if co2 is not None and heat is not None and heat > 0:
            ratios.append(co2 / heat)
        else:
            ratios.append(None)

    valid_ratios = [r for r in ratios if r is not None]
    if not valid_ratios:
        elapsed = (time.monotonic() - t0) * 1000
        ctx.audit.log_tool_call(
            tool_call_id=tc_id,
            tool_name="check_emissions_load_consistency",
            params={"source_path": source_path, "unit_id": unit_id},
            output_hash=hash_output({"error": "no_valid_ratios"}),
            duration_ms=elapsed,
        )
        return {
            "tool_call_id": tc_id,
            "status": "error",
            "error_message": "No valid co2Mass/heatInput ratios (missing or zero heatInput)",
        }

    import statistics

    baseline_ratio = statistics.median(valid_ratios)

    flags: list[dict[str, object]] = []
    n = len(ratios)

    for i in range(n - window_hours + 1):
        window_ratios = [r for r in ratios[i : i + window_hours] if r is not None]
        if len(window_ratios) < window_hours // 2:
            continue

        window_median = statistics.median(window_ratios)
        if baseline_ratio > 0:
            deviation_pct = abs(window_median - baseline_ratio) / baseline_ratio * 100
        else:
            deviation_pct = 0.0

        if deviation_pct > tolerance_pct:
            start_ts = timestamps[i]
            end_ts = timestamps[i + window_hours - 1]
            flags.append(
                {
                    "type": "ratio_deviation",
                    "start": start_ts,
                    "end": end_ts,
                    "observed_ratio": round(window_median, 6),
                    "baseline_ratio": round(baseline_ratio, 6),
                    "deviation_pct": round(deviation_pct, 2),
                    "score": round(deviation_pct / tolerance_pct, 2),
                }
            )

    for i in range(1, n):
        if (
            co2_vals[i] is not None
            and co2_vals[i - 1] is not None
            and heat_vals[i] is not None
            and heat_vals[i - 1] is not None
        ):
# Type narrowing ensures these are not None here
            heat_change = heat_vals[i] - heat_vals[i - 1]  # type: ignore[operator]
            load_change = 0.0
            if load_vals[i] is not None and load_vals[i - 1] is not None:
                load_change = load_vals[i] - load_vals[i - 1]  # type: ignore[operator]

            co2_drop = co2_vals[i - 1] - co2_vals[i]  # type: ignore[operator]
            # Type narrowing ensures heat_vals[i-1] and load_vals[i-1] are not None here
            h_prev = heat_vals[i - 1]
            l_prev = load_vals[i - 1] if load_vals[i - 1] is not None else 1.0
            if co2_drop > 0 and heat_change <= 0.01 * h_prev and load_change <= 0.01 * l_prev:  # type: ignore[operator]
                prev_co2 = co2_vals[i - 1]
                score_val = co2_drop / (prev_co2 if prev_co2 is not None else 1) * 100
                flags.append(
                    {
                        "type": "emissions_drop_flat_load",
                        "start": timestamps[i - 1],
                        "end": timestamps[i],
                        "co2_drop_short_tons": round(co2_drop, 3),
                        "heat_change_mmBtu": round(heat_change, 3),
                        "load_change_MW": round(load_change, 3),
                        "score": round(score_val, 2),
                    }
                )

    raw_output_lines = [
        f"Baseline ratio: {baseline_ratio:.6f}",
        f"Flags: {len(flags)}",
    ]
    for f in flags:
        dev = f.get("deviation_pct", 0)
        raw_output_lines.append(
            f"{f['type']}: {f.get('start', '')}-{f.get('end', '')} dev={dev:.1f}%"
        )
    raw_output = "\n".join(raw_output_lines)

    source_name = f"cems_consistency.{file_path.stem}.{unit_id}"
    index_result = extract_and_index(
        raw_output=raw_output,
        source_name=source_name,
        source_path=str(file_path),
        extractor_name="cems_consistency_checker",
    )

    elapsed = (time.monotonic() - t0) * 1000
    ctx.audit.log_tool_call(
        tool_call_id=tc_id,
        tool_name="check_emissions_load_consistency",
        params={
            "source_path": source_path,
            "unit_id": unit_id,
            "window_hours": window_hours,
            "tolerance_pct": tolerance_pct,
        },
        output_hash=hash_output({"flags": len(flags)}),
        duration_ms=elapsed,
    )

    note = (
        "A stable co2Mass/heatInput ratio is expected ONLY when CO2 is "
        "measured (CEMS). If values are calculated from fuel factors, "
        "the ratio is stable by construction and flags prove nothing."
    )
    return {
        "tool_call_id": tc_id,
        "status": "success",
        "unit_id": unit_id,
        "measured_fraction": round(measured_fraction, 4),
        "hours_measured": measured_count,
        "hours_total": total_hour_count,
        "baseline_ratio": round(baseline_ratio, 6),
        "tolerance_pct": tolerance_pct,
        "window_hours": window_hours,
        "flags": flags,
        "total_windows_checked": max(0, n - window_hours + 1),
        "indexed_source": index_result.get("source_name"),
        "windows_indexed": index_result.get("windows_indexed", 0),
        "note": note,
    }


@mcp.tool()
@tool_access(EXECUTORS)
def derive_operating_timeline(
    source_path: str,
    unit_id: str,
) -> dict[str, object]:
    """Classify each hour as ONLINE / OFFLINE / STARTUP-SHUTDOWN for a unit.

    Classification:
    - ONLINE: load or heat input >= 5% of unit's observed maximum
    - OFFLINE: load and heat input both < 5% of max
    - STARTUP-SHUTDOWN: ramping (transition between ONLINE/OFFLINE)

    When the opTime column is present, opTime == 0 (unit not burning fuel)
    is treated as OFFLINE regardless of load/heat values. When opTime is
    absent, behaviour is unchanged (load/heat thresholds only).

    Merges consecutive hours into intervals. Reports emissions during
    OFFLINE intervals. Name honestly: derived from CEMS, NOT from SCADA logs.

    Args:
        source_path: Path to the CEMS hourly CSV file.
        unit_id: Unit identifier to analyze.

    Returns:
        Timeline intervals with state, time range, and emissions during
        OFFLINE periods.
    """
    ctx = get_ctx()
    tc_id = make_tool_call_id()
    t0 = time.monotonic()

    try:
        file_path = _resolve_evidence_file(source_path)
    except PathPolicyError as exc:
        return {
            "tool_call_id": tc_id,
            "status": "error",
            "error_message": str(exc),
        }

    if not file_path.exists():
        elapsed = (time.monotonic() - t0) * 1000
        ctx.audit.log_tool_call(
            tool_call_id=tc_id,
            tool_name="derive_operating_timeline",
            params={"source_path": source_path, "unit_id": unit_id},
            output_hash=hash_output({"error": "not found"}),
            duration_ms=elapsed,
        )
        return {
            "tool_call_id": tc_id,
            "status": "error",
            "error_message": f"File not found: {source_path}",
        }

    rows = _read_cems_csv(file_path)
    missing, _ = _validate_cems_rows(rows)
    if missing:
        elapsed = (time.monotonic() - t0) * 1000
        ctx.audit.log_tool_call(
            tool_call_id=tc_id,
            tool_name="derive_operating_timeline",
            params={"source_path": source_path, "unit_id": unit_id},
            output_hash=hash_output({"error": "missing_columns", "missing": missing}),
            duration_ms=elapsed,
        )
        return {
            "tool_call_id": tc_id,
            "status": "error",
            "error_message": f"Missing required columns: {missing}",
        }

    unit_rows = [r for r in rows if r.get("unitId") == unit_id]
    if not unit_rows:
        elapsed = (time.monotonic() - t0) * 1000
        ctx.audit.log_tool_call(
            tool_call_id=tc_id,
            tool_name="derive_operating_timeline",
            params={"source_path": source_path, "unit_id": unit_id},
            output_hash=hash_output({"error": "unit_not_found"}),
            duration_ms=elapsed,
        )
        return {
            "tool_call_id": tc_id,
            "status": "error",
            "error_message": f"Unit {unit_id} not found in data",
        }

    unit_rows.sort(key=_sort_key)

    heat_vals: list[float] = []
    load_vals: list[float] = []
    timestamps: list[str] = []
    co2_vals: list[float | None] = []
    optime_vals: list[float | None] = []
    has_optime = "opTime" in rows[0]

    for r in unit_rows:
        ts = _parse_date_hour(r.get("date", ""), r.get("hour", ""))
        timestamps.append(ts if ts else "")

        heat = _to_float(r.get("heatInput"))
        load = _to_float(r.get("grossLoad"))
        co2 = _to_float(r.get("co2Mass"))

        heat_vals.append(heat if heat is not None else 0.0)
        load_vals.append(load if load is not None else 0.0)
        co2_vals.append(co2)
        if has_optime:
            optime_vals.append(_to_float(r.get("opTime")))

    max_heat = max(heat_vals) if heat_vals else 0.0
    max_load = max(load_vals) if load_vals else 0.0
    threshold_heat = max_heat * 0.05
    threshold_load = max_load * 0.05

    states: list[str] = []
    for i in range(len(unit_rows)):
        h = heat_vals[i]
        load_val = load_vals[i]
        if has_optime:
            # opTime == 0 means unit is not burning fuel -> OFFLINE
            opt = optime_vals[i]
            if opt is not None and opt == 0:
                states.append("OFFLINE")
            elif h >= threshold_heat or load_val >= threshold_load:
                states.append("ONLINE")
            else:
                states.append("OFFLINE")
        else:
            if h >= threshold_heat or load_val >= threshold_load:
                states.append("ONLINE")
            else:
                states.append("OFFLINE")

    for i in range(1, len(states) - 1):
        prev_state = states[i - 1]
        next_state = states[i + 1]
        if prev_state != next_state and states[i] != prev_state and states[i] != next_state:
            states[i] = "STARTUP-SHUTDOWN"

    intervals: list[dict[str, object]] = []
    i = 0
    while i < len(states):
        start_idx = i
        state = states[i]
        offline_co2_total = 0.0
        offline_hours = 0

        while i < len(states) and states[i] == state:
            if state == "OFFLINE" and co2_vals[i] is not None:
                offline_co2_total += co2_vals[i]  # type: ignore[operator]
                offline_hours += 1
            i += 1

        end_idx = i - 1
        intervals.append(
            {
                "state": state,
                "start": timestamps[start_idx],
                "end": timestamps[end_idx],
                "hours": end_idx - start_idx + 1,
                "offline_co2_short_tons": round(offline_co2_total, 3)
                if state == "OFFLINE"
                else 0.0,
                "offline_hours_with_emissions": offline_hours
                if state == "OFFLINE"
                else 0,
            }
        )

    raw_output_lines = [f"Unit: {unit_id}", f"Intervals: {len(intervals)}"]
    for iv in intervals:
        raw_output_lines.append(
            f"{iv['state']}: {iv['start']} to {iv['end']} "
            f"({iv['hours']}h) offline_CO2={iv['offline_co2_short_tons']}"
        )
    raw_output = "\n".join(raw_output_lines)

    source_name = f"cems_timeline.{file_path.stem}.{unit_id}"
    index_result = extract_and_index(
        raw_output=raw_output,
        source_name=source_name,
        source_path=str(file_path),
        extractor_name="cems_timeline_deriver",
    )

    elapsed = (time.monotonic() - t0) * 1000
    ctx.audit.log_tool_call(
        tool_call_id=tc_id,
        tool_name="derive_operating_timeline",
        params={"source_path": source_path, "unit_id": unit_id},
        output_hash=hash_output({"intervals": len(intervals)}),
        duration_ms=elapsed,
    )

    note = (
        "Derived from CEMS hourly data (load/heatInput thresholds"
        + (", opTime" if has_optime else "")
        + "). NOT from SCADA logs. OFFLINE emissions may indicate "
        "measurement artifacts or residual combustion."
    )
    return {
        "tool_call_id": tc_id,
        "status": "success",
        "unit_id": unit_id,
        "thresholds": {
            "heat_mmBtu": round(threshold_heat, 3),
            "load_MW": round(threshold_load, 3),
        },
        "opTime_used": has_optime,
        "intervals": intervals,
        "indexed_source": index_result.get("source_name"),
        "windows_indexed": index_result.get("windows_indexed", 0),
        "note": note,
    }

