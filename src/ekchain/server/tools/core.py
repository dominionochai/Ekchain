"""Core read-only MCP tool implementations for Ekchain.

Every tool in this module is a pure query; no destructive operations exist
in the Ekchain MCP surface.  Evidence integrity is enforced by the API design,
not by prompts.
"""

from __future__ import annotations

import json
import logging
import time
from pathlib import Path
from typing import Any

from sqlalchemy import select as sa_select

from ekchain.db import windows_t
from ekchain.server.app import get_ctx, mcp
from ekchain.server.helpers import (
    hash_output,
    make_tool_call_id,
    truncate_raw_text,
)
from ekchain.server.tool_access import Role, tool_access

logger = logging.getLogger(__name__)

_RAW_TEXT_CORRELATE_CAP = 200
_CORRELATE_WINDOW_CAP = 20
_TIMELINE_TEXT_CAP = 500


def _truncated_window(w: Any, cap: int = _RAW_TEXT_CORRELATE_CAP) -> dict[str, object]:
    """Serialize a window with raw_text truncated for compact output."""
    d: dict[str, Any] = w.model_dump() if hasattr(w, "model_dump") else dict(w)
    truncate_raw_text(d, cap)
    return d


@mcp.tool()
@tool_access(Role.CROSS_EXECUTOR | Role.NARRATIVE_EXECUTOR)
def correlate_across_sources(
    t_start: str,
    t_end: str,
    sources: list[str] | None = None,
) -> dict[str, object]:
    """Cross-reference all evidence sources within a time window.

    Call during cross-system analysis to answer: "what did each artifact
    type observe during this time period?" Requires multiple sources to
    have timestamped data indexed.

    Returns windows grouped by source, with counts of sources with and
    without data in the range. Use get_raw_output() for full text of
    interesting windows.
    """
    ctx = get_ctx()
    tc_id = make_tool_call_id()
    t0 = time.monotonic()

    correlation = ctx.correlator.correlate_across_sources(
        time_start=t_start,
        time_end=t_end,
        sources=sources,
    )

    sources_with_data = sorted(correlation.windows_by_source.keys())
    sources_without_data = sorted(set(correlation.sources_queried) - set(sources_with_data))
    all_source_names = [s.source_name for s in ctx.db.get_sources()]
    unindexed = sorted(set(all_source_names) - set(correlation.sources_queried))

    n_with = len(sources_with_data)
    n_queried = len(correlation.sources_queried)
    hint_parts = [f"{n_with} of {n_queried} indexed sources had data in this time window."]
    if sources_without_data:
        hint_parts.append(f"Sources without data in range: {sources_without_data[:10]}.")
    if unindexed:
        hint_parts.append(
            f"{len(unindexed)} other source(s) exist but were not queried. "
            "Consider running additional extractions to fill gaps."
        )
    hint = " ".join(hint_parts)

    slimmed_by_source: dict[str, Any] = {}
    for src, wins in correlation.windows_by_source.items():
        total_for_src = len(wins)
        capped = wins[:_CORRELATE_WINDOW_CAP]
        slimmed_by_source[src] = {
            "windows": [_truncated_window(w, cap=_RAW_TEXT_CORRELATE_CAP) for w in capped],
            "total_windows": total_for_src,
            "truncated": total_for_src > _CORRELATE_WINDOW_CAP,
        }

    results = {
        "time_start": correlation.time_start,
        "time_end": correlation.time_end,
        "sources_queried": correlation.sources_queried,
        "total_windows": correlation.total_windows,
        "windows_by_source": slimmed_by_source,
    }

    # Index results so later phases can search them
    from ekchain.server.extract_helpers import extract_and_index

    source_name = "composite.correlation"
    try:
        extract_and_index(
            raw_output=json.dumps(results, default=str),
            source_name=source_name,
            source_path="correlate_across_sources",
            extractor_name="composite",
        )
    except Exception:
        logger.debug("Failed to index correlation results", exc_info=True)

    elapsed = (time.monotonic() - t0) * 1000
    ctx.audit.log_tool_call(
        tool_call_id=tc_id,
        tool_name="correlate_across_sources",
        params={"t_start": t_start, "t_end": t_end, "sources": sources},
        output_hash=hash_output(results),
        duration_ms=elapsed,
    )
    return {
        "tool_call_id": tc_id,
        "status": "success",
        "results": results,
        "source": source_name,
        "result_count": correlation.total_windows,
        "sources_with_data": sources_with_data,
        "sources_without_data": sources_without_data,
        "hint": hint + " Use get_raw_output(source_name) for full evidence text.",
    }


@mcp.tool()
@tool_access(Role.CROSS_PLANNER | Role.CROSS_ANALYST | Role.REPORT)
def get_bookmarks() -> dict[str, object]:
    """Retrieve all bookmarked windows with their notes.

    Returns bookmarks created during this investigation, including the
    window content and the note explaining why it was flagged. Use this
    after context compaction to recover interesting leads. Read-only.
    """
    ctx = get_ctx()
    tc_id = make_tool_call_id()
    t0 = time.monotonic()

    bookmarks = ctx.db.get_bookmarks()

    enriched: list[dict[str, object]] = []
    window_ids = [int(str(bm["window_id"])) for bm in bookmarks]
    window_map: dict[int, Any] = {}
    if window_ids:
        with ctx.db._engine.connect() as conn:
            rows = conn.execute(
                sa_select(windows_t).where(windows_t.c.window_id.in_(window_ids))
            ).fetchall()
        window_map = {row.window_id: row for row in rows}

    for bm in bookmarks:
        entry: dict[str, object] = dict(bm)
        wid = int(str(bm["window_id"]))
        row = window_map.get(wid)
        if row:
            raw = row.raw_text
            if len(raw) > _TIMELINE_TEXT_CAP:
                raw = raw[:_TIMELINE_TEXT_CAP] + "..."
            entry["raw_text"] = raw
            entry["event_time"] = row.event_time
        enriched.append(entry)

    elapsed = (time.monotonic() - t0) * 1000
    ctx.audit.log_tool_call(
        tool_call_id=tc_id,
        tool_name="get_bookmarks",
        params={},
        output_hash=hash_output(enriched),
        duration_ms=elapsed,
    )
    return {
        "tool_call_id": tc_id,
        "status": "success",
        "results": enriched,
        "result_count": len(enriched),
    }


@mcp.tool()
@tool_access(Role.EXTRACT_ANALYST | Role.CROSS_ANALYST | Role.NARRATIVE_ANALYST)
def remove_bookmark(bookmark_id: int) -> dict[str, object]:
    """Remove a bookmark by ID.

    Args:
        bookmark_id: The bookmark ID to remove.
    """
    ctx = get_ctx()
    removed = ctx.db.remove_bookmark(bookmark_id)

    return {
        "status": "success" if removed else "not_found",
        "bookmark_id": bookmark_id,
        "removed": removed,
    }


_TOOL_GUIDE_PATH = Path(__file__).resolve().parent.parent / "data" / "tool_guide.json"

_ToolGuide = dict[str, list[dict[str, str | list[str]]]]


def _load_tool_guide() -> _ToolGuide:
    """Load the tool reference guide from the JSON data file."""
    with open(_TOOL_GUIDE_PATH) as f:
        guide: _ToolGuide = json.load(f)
    return guide


_TOOL_GUIDE = _load_tool_guide()
_VALID_CATEGORIES = frozenset(_TOOL_GUIDE.keys())


@mcp.tool()
@tool_access(Role.CROSS_PLANNER)
def get_tool_guide(category: str = "all") -> dict[str, object]:
    """Return a reference guide of available forensic tools and their relationships.

    Call this when you need to decide which tools to run next for a
    given evidence type, or to understand dependencies between tools.

    Args:
        category: Filter by category. Options: "all", "case_management",
            "evidence_browsing", "memory", "disk", "windows", "network",
            "browser_forensics", "mobile", "macos", "malware_analysis",
            "encryption", "composite", "reporting", "post_extraction",
            "reference".
    """
    if category == "all":
        return {
            "status": "success",
            "categories": list(_TOOL_GUIDE.keys()),
            "guide": _TOOL_GUIDE,
        }

    if category not in _VALID_CATEGORIES:
        return {
            "status": "error",
            "error_message": (
                f"Unknown category: {category!r}. "
                f"Valid options: 'all', {sorted(_VALID_CATEGORIES)}"
            ),
        }

    return {
        "status": "success",
        "category": category,
        "tools": _TOOL_GUIDE[category],
    }

