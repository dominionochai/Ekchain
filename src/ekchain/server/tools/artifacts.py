"""MCP tools for artifact extraction and analysis.

Basic file system operations: listing directories and reading files.
"""

from __future__ import annotations

import logging
import time
from pathlib import Path

from ekchain.path_policy import PathPolicyError, resolve_allowed_path
from ekchain.server.app import get_cfg, get_ctx, mcp
from ekchain.server.helpers import hash_output, make_tool_call_id
from ekchain.server.tool_access import Role, tool_access

logger = logging.getLogger(__name__)


def _resolve_artifact_path(target: Path) -> Path:
    """Resolve *target* within the active evidence or case-storage root."""
    cfg = get_cfg()
    allowed_roots = [Path(cfg.db_dir)]
    ctx = get_ctx()
    meta = ctx.db.get_case_metadata()
    if meta and meta.evidence_root:
        allowed_roots.append(Path(meta.evidence_root))
    return resolve_allowed_path(target, allowed_roots)


@mcp.tool()
@tool_access(Role.CATALOG | Role.EXTRACT_PLANNER | Role.EXTRACT_EXECUTOR)
def list_directory(
    path: str,
    recursive: bool = False,
) -> dict[str, object]:
    """List files and directories at a given path.

    Use this instead of Bash ``ls`` to maintain the audit trail.
    Returns file names, sizes, and types for each entry.

    Args:
        path: Directory path to list.
        recursive: If True, list all files recursively.
    """
    ctx = get_ctx()
    tc_id = make_tool_call_id()
    t0 = time.monotonic()

    try:
        target = _resolve_artifact_path(Path(path))
    except PathPolicyError as exc:
        return {
            "tool_call_id": tc_id,
            "status": "error",
            "error_message": str(exc),
            "results": [],
            "result_count": 0,
        }
    if not target.exists():
        elapsed = (time.monotonic() - t0) * 1000
        ctx.audit.log_tool_call(
            tool_call_id=tc_id,
            tool_name="list_directory",
            params={"path": path, "recursive": recursive},
            output_hash=hash_output({"error": "not found"}),
            duration_ms=elapsed,
        )
        return {
            "tool_call_id": tc_id,
            "status": "error",
            "error_message": f"Path not found: {path}",
            "results": [],
            "result_count": 0,
        }

    entries: list[dict[str, object]] = []
    items = sorted(target.rglob("*")) if recursive else sorted(target.iterdir())
    for item in items:
        try:
            rel = str(item.relative_to(target))
        except ValueError:
            rel = str(item)
        entry: dict[str, object] = {
            "name": rel,
            "type": "directory" if item.is_dir() else "file",
        }
        if item.is_file():
            try:
                size = item.stat().st_size
                entry["size_bytes"] = size
                if size >= 1_073_741_824:
                    entry["size_human"] = f"{size / 1_073_741_824:.1f} GB"
                elif size >= 1_048_576:
                    entry["size_human"] = f"{size / 1_048_576:.1f} MB"
                elif size >= 1024:
                    entry["size_human"] = f"{size / 1024:.0f} KB"
                else:
                    entry["size_human"] = f"{size} B"
            except OSError:
                pass
        entries.append(entry)

    elapsed = (time.monotonic() - t0) * 1000
    ctx.audit.log_tool_call(
        tool_call_id=tc_id,
        tool_name="list_directory",
        params={"path": path, "recursive": recursive},
        output_hash=hash_output({"count": len(entries)}),
        duration_ms=elapsed,
    )
    return {
        "tool_call_id": tc_id,
        "status": "success",
        "path": path,
        "results": entries,
        "result_count": len(entries),
    }


@mcp.tool()
@tool_access(Role.EXTRACT_EXECUTOR | Role.EXTRACT_ANALYST | Role.CROSS_EXECUTOR)
def read_evidence_file(
    file_path: str = "",
    max_bytes: int = 1_048_576,
    path: str = "",
) -> dict[str, object]:
    """Read a text file from the evidence directory.

    Use this instead of shell commands to read README files, config
    files, log files, or any text file in the evidence. Files are read
    as UTF-8 with replacement for non-decodable bytes. Binary files
    return a hex preview. Capped at *max_bytes* (default 1 MB). Read-only.

    Args:
        file_path: Absolute path to the file to read.
        max_bytes: Maximum bytes to read (default 1 MB).
        path: Alias for file_path.
    """
    file_path = file_path or path
    ctx = get_ctx()
    tc_id = make_tool_call_id()
    t0 = time.monotonic()
    params = {"file_path": file_path, "max_bytes": max_bytes}

    try:
        target = _resolve_artifact_path(Path(file_path))
    except PathPolicyError as exc:
        return {
            "tool_call_id": tc_id,
            "status": "error",
            "error_message": str(exc),
        }
    if not target.exists():
        elapsed = (time.monotonic() - t0) * 1000
        ctx.audit.log_tool_call(
            tool_call_id=tc_id,
            tool_name="read_evidence_file",
            params=params,
            output_hash=hash_output({"error": "not found"}),
            duration_ms=elapsed,
        )
        return {
            "tool_call_id": tc_id,
            "status": "error",
            "error_message": f"File not found: {file_path}",
        }

    if not target.is_file():
        elapsed = (time.monotonic() - t0) * 1000
        ctx.audit.log_tool_call(
            tool_call_id=tc_id,
            tool_name="read_evidence_file",
            params=params,
            output_hash=hash_output({"error": "not a file"}),
            duration_ms=elapsed,
        )
        return {
            "tool_call_id": tc_id,
            "status": "error",
            "error_message": f"Not a file: {file_path}",
        }

    try:
        with open(target, "rb") as f:
            raw = f.read(max_bytes)
        try:
            content = raw.decode("utf-8")
            is_binary = False
        except UnicodeDecodeError:
            content = raw.decode("utf-8", errors="replace")
            is_binary = any(b < 0x20 and b not in (0x09, 0x0A, 0x0D) for b in raw[:512])
    except OSError as exc:
        logger.error("Failed to read evidence file %r: %s", file_path, exc)
        elapsed = (time.monotonic() - t0) * 1000
        ctx.audit.log_tool_call(
            tool_call_id=tc_id,
            tool_name="read_evidence_file",
            params=params,
            output_hash=hash_output({"error": "read_failed"}),
            duration_ms=elapsed,
        )
        return {"tool_call_id": tc_id, "status": "error", "error_message": "Failed to read file"}

    file_size = target.stat().st_size
    truncated = file_size > max_bytes

    result = {
        "content": content
        if not is_binary
        else content[:2000] + "\n... (binary file, showing first 2000 chars with replacements)",
        "file_size": file_size,
        "truncated": truncated,
        "is_binary": is_binary,
    }

    elapsed = (time.monotonic() - t0) * 1000
    ctx.audit.log_tool_call(
        tool_call_id=tc_id,
        tool_name="read_evidence_file",
        params=params,
        output_hash=hash_output({"size": file_size}),
        duration_ms=elapsed,
    )
    return {"tool_call_id": tc_id, "status": "success", **result}

