#!/usr/bin/env python3
"""Connectivity and capability checker for LiteLLM-routed model providers.

Reuses ekchain.orchestrator.proxy.ProxyManager for subprocess lifecycle,
health polling, and Anthropic API proxy configuration.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

import yaml

# Ensure project root is in sys.path when script is run directly
_ROOT = Path(__file__).resolve().parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from ekchain.orchestrator.proxy import _MASTER_KEY, ProxyManager  # noqa: E402


def find_required_env_vars(config_data: Any) -> list[str]:
    """Extract environment variable names referenced as os.environ/<VAR>."""
    vars_found: list[str] = []
    if isinstance(config_data, dict):
        for v in config_data.values():
            vars_found.extend(find_required_env_vars(v))
    elif isinstance(config_data, list):
        for item in config_data:
            vars_found.extend(find_required_env_vars(item))
    elif isinstance(config_data, str) and config_data.startswith("os.environ/"):
        parts = config_data.split("/", 1)
        if len(parts) == 2 and parts[1]:
            vars_found.append(parts[1])
    return vars_found


def extract_text_response(data: dict[str, Any]) -> str:
    """Extract text from Anthropic Messages or OpenAI-style response dict."""
    texts: list[str] = []
    content = data.get("content")
    if isinstance(content, list):
        for block in content:
            if isinstance(block, dict) and block.get("type") == "text":
                text = block.get("text", "")
                if text:
                    texts.append(text)
    elif isinstance(content, str):
        texts.append(content)

    # Fallback to OpenAI choices format if present
    choices = data.get("choices")
    if isinstance(choices, list) and choices:
        first = choices[0]
        if isinstance(first, dict):
            msg = first.get("message")
            if isinstance(msg, dict):
                msg_content = msg.get("content")
                if isinstance(msg_content, str):
                    texts.append(msg_content)

    return "\n".join(texts).strip()


def detect_tool_call(data: dict[str, Any], tool_name: str = "get_status") -> bool:
    """Detect if the response contains a tool call for the specified tool name."""
    # Anthropic Messages format: content list with type == "tool_use"
    content = data.get("content")
    if isinstance(content, list):
        for block in content:
            if (
                isinstance(block, dict)
                and block.get("type") == "tool_use"
                and block.get("name") == tool_name
            ):
                return True

    # OpenAI format: choices[0].message.tool_calls
    choices = data.get("choices")
    if isinstance(choices, list) and choices:
        first = choices[0]
        if isinstance(first, dict):
            msg = first.get("message")
            if isinstance(msg, dict):
                tool_calls = msg.get("tool_calls")
                if isinstance(tool_calls, list):
                    for call in tool_calls:
                        if isinstance(call, dict):
                            fn = call.get("function")
                            if isinstance(fn, dict) and fn.get("name") == tool_name:
                                return True

    return False


def extract_usage(data: dict[str, Any]) -> dict[str, int] | None:
    """Extract token usage counts from response dict."""
    usage = data.get("usage")
    if isinstance(usage, dict) and usage:
        result: dict[str, int] = {}
        for k, v in usage.items():
            if isinstance(v, (int, float)):
                result[k] = int(v)
        return result or None
    return None


def send_messages_request(
    port: int,
    master_key: str,
    payload: dict[str, Any],
    timeout: float = 30.0,
) -> dict[str, Any]:
    """Send an Anthropic Messages API request to the running LiteLLM proxy."""
    url = f"http://localhost:{port}/v1/messages"
    body = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(
        url,
        data=body,
        headers={
            "Content-Type": "application/json",
            "x-api-key": master_key,
            "Authorization": f"Bearer {master_key}",
            "anthropic-version": "2023-06-01",
        },
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        response_bytes = resp.read()
        return json.loads(response_bytes.decode("utf-8"))


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Verify connectivity and capabilities of a LiteLLM model provider"
    )
    parser.add_argument(
        "--proxy-config",
        default="config/gemini.litellm.yaml",
        help="Path to LiteLLM proxy config YAML file (default: config/gemini.litellm.yaml)",
    )
    parser.add_argument(
        "--model",
        default="openai/gemini-3.8-flash",
        help="Model identifier (default: openai/gemini-3.8-flash)",
    )
    parser.add_argument(
        "--port",
        type=int,
        default=None,
        help="Port number for proxy (default: 4000 or EKCHAIN_PROXY_PORT)",
    )
    args = parser.parse_args()

    config_path = Path(args.proxy_config)
    if not config_path.is_file():
        print(f"Error: Proxy config file not found: {config_path}", file=sys.stderr)
        return 2

    # Parse YAML to inspect required environment variables
    try:
        raw_config = yaml.safe_load(config_path.read_text(encoding="utf-8"))
    except Exception as exc:
        print(f"Error parsing config {config_path}: {exc}", file=sys.stderr)
        return 2

    required_vars = find_required_env_vars(raw_config)
    missing_vars = [v for v in required_vars if not os.environ.get(v)]
    if missing_vars:
        for mv in missing_vars:
            print(f"Error: Missing required environment variable: {mv}", file=sys.stderr)
        return 2

    # Initialize proxy manager
    pm = ProxyManager(
        models=[args.model],
        port=args.port,
        config_path=str(config_path),
        thinking=False,
    )

    try:
        # Step 1: Proxy startup & health check
        t0 = time.monotonic()
        try:
            pm.start()
            elapsed = time.monotonic() - t0
            print(f"[PASS] Step 1: Proxy started and passed health check ({elapsed:.2f}s)")
        except Exception as exc:
            elapsed = time.monotonic() - t0
            print(
                f"[FAIL] Step 1: Proxy failed to start or pass health check "
                f"({elapsed:.2f}s): {exc}"
            )
            return 1

        # Step 2: Plain message round trip
        t0 = time.monotonic()
        plain_resp: dict[str, Any] = {}
        try:
            plain_payload = {
                "model": args.model,
                "max_tokens": 1024,
                "messages": [
                    {"role": "user", "content": "Respond with 'hello' and nothing else."}
                ],
            }
            plain_resp = send_messages_request(pm.port, _MASTER_KEY, plain_payload)
            text = extract_text_response(plain_resp)
            elapsed = time.monotonic() - t0
            if text:
                print(
                    f"[PASS] Step 2: Plain message round trip returned non-empty text "
                    f"({elapsed:.2f}s)"
                )
            else:
                print(
                    f"[FAIL] Step 2: Plain message round trip returned empty text "
                    f"({elapsed:.2f}s)"
                )
                return 1
        except Exception as exc:
            elapsed = time.monotonic() - t0
            print(f"[FAIL] Step 2: Plain message request failed ({elapsed:.2f}s): {exc}")
            return 1

        # Step 3: Tool calling round trip
        t0 = time.monotonic()
        tool_resp: dict[str, Any] = {}
        try:
            tool_payload = {
                "model": args.model,
                "max_tokens": 1024,
                "tools": [
                    {
                        "name": "get_status",
                        "description": "Get current system status",
                        "input_schema": {
                            "type": "object",
                            "properties": {},
                            "required": [],
                        },
                    }
                ],
                "messages": [
                    {
                        "role": "user",
                        "content": "You must call the get_status tool right now.",
                    }
                ],
            }
            tool_resp = send_messages_request(pm.port, _MASTER_KEY, tool_payload)
            has_tool_call = detect_tool_call(tool_resp, "get_status")
            elapsed = time.monotonic() - t0
            if has_tool_call:
                print(
                    f"[PASS] Step 3: Tool call 'get_status' received in response "
                    f"({elapsed:.2f}s)"
                )
            else:
                print(
                    f"[FAIL] Step 3: Response did not contain a tool call for get_status "
                    f"({elapsed:.2f}s)"
                )
                return 1
        except Exception as exc:
            elapsed = time.monotonic() - t0
            print(f"[FAIL] Step 3: Tool calling request failed ({elapsed:.2f}s): {exc}")
            return 1

        # Step 4: Print token usage
        usage = extract_usage(plain_resp) or extract_usage(tool_resp)
        if usage:
            usage_str = ", ".join(f"{k}={v}" for k, v in usage.items())
            print(f"Token usage: {usage_str}")
        else:
            print("Token usage: Not reported in response")

        return 0

    finally:
        pm.stop()


if __name__ == "__main__":
    sys.exit(main())
