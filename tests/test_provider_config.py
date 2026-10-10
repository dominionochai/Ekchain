"""Offline unit tests for LiteLLM provider config and check_provider helpers."""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

import yaml

from ekchain.orchestrator.proxy import _MASTER_KEY, is_proxy_model

# Ensure repo root is on sys.path so scripts.check_provider can be imported
_REPO_ROOT = Path(__file__).resolve().parent.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from scripts.check_provider import (  # noqa: E402
    detect_tool_call,
    extract_text_response,
    extract_usage,
    find_required_env_vars,
)


def test_gemini_litellm_config_structure() -> None:
    """Verify config/gemini.litellm.yaml contents, structure, and master key."""
    config_path = _REPO_ROOT / "config" / "gemini.litellm.yaml"
    assert config_path.is_file(), f"Config file not found at {config_path}"

    raw_text = config_path.read_text(encoding="utf-8")
    config = yaml.safe_load(raw_text)
    assert isinstance(config, dict)

    # Validate model_list
    model_list = config.get("model_list")
    assert isinstance(model_list, list)
    assert len(model_list) >= 1

    first_model = model_list[0]
    model_name = first_model.get("model_name")
    assert model_name == "openai/gemini-3.8-flash"

    # Verify is_proxy_model recognizes this model name
    assert is_proxy_model(model_name) is True

    litellm_params = first_model.get("litellm_params")
    assert isinstance(litellm_params, dict)
    assert litellm_params.get("model") == "gemini/gemini-3.8-flash"
    assert litellm_params.get("api_key") == "os.environ/GEMINI_API_KEY"
    assert litellm_params.get("max_tokens") == 8192

    # Validate litellm_settings
    settings = config.get("litellm_settings")
    assert isinstance(settings, dict)
    assert settings.get("drop_params") is True
    assert settings.get("num_retries") == 5
    assert settings.get("modify_params") is True

    # Validate general_settings and master_key against proxy.py's constant
    general = config.get("general_settings")
    assert isinstance(general, dict)
    assert general.get("master_key") == _MASTER_KEY


def test_find_required_env_vars() -> None:
    """Verify extraction of os.environ/<VAR> from config data."""
    sample_config: dict[str, Any] = {
        "model_list": [
            {
                "model_name": "openai/gemini-3.8-flash",
                "litellm_params": {
                    "api_key": "os.environ/GEMINI_API_KEY",
                },
            }
        ],
        "other": ["os.environ/EXTRA_KEY", "plain_value"],
    }
    vars_found = find_required_env_vars(sample_config)
    assert "GEMINI_API_KEY" in vars_found
    assert "EXTRA_KEY" in vars_found
    assert len(vars_found) == 2

    # Empty case
    assert find_required_env_vars({"key": "val"}) == []


def test_extract_text_response_anthropic() -> None:
    """Verify extracting text from Anthropic Messages response format."""
    canned_anthropic = {
        "id": "msg_01",
        "type": "message",
        "role": "assistant",
        "content": [
            {"type": "text", "text": "Hello from Gemini!"},
        ],
    }
    extracted = extract_text_response(canned_anthropic)
    assert extracted == "Hello from Gemini!"


def test_extract_text_response_multi_block() -> None:
    """Verify extracting text from multiple content blocks."""
    canned_multi = {
        "content": [
            {"type": "text", "text": "Part 1."},
            {"type": "tool_use", "name": "noop"},
            {"type": "text", "text": "Part 2."},
        ]
    }
    assert extract_text_response(canned_multi) == "Part 1.\nPart 2."


def test_extract_text_response_openai_fallback() -> None:
    """Verify fallback to OpenAI choices format."""
    canned_openai = {
        "choices": [
            {"message": {"role": "assistant", "content": "OpenAI text response"}}
        ]
    }
    assert extract_text_response(canned_openai) == "OpenAI text response"


def test_extract_text_response_empty() -> None:
    """Verify empty string when no text content exists."""
    assert extract_text_response({}) == ""
    assert extract_text_response({"content": []}) == ""


def test_detect_tool_call_anthropic() -> None:
    """Verify detecting tool call in Anthropic Messages format."""
    canned_tool_use = {
        "content": [
            {"type": "text", "text": "Calling tool..."},
            {"type": "tool_use", "id": "tu_01", "name": "get_status", "input": {}},
        ]
    }
    assert detect_tool_call(canned_tool_use, "get_status") is True
    assert detect_tool_call(canned_tool_use, "other_tool") is False


def test_detect_tool_call_openai_fallback() -> None:
    """Verify detecting tool call in OpenAI format."""
    canned_openai_tool = {
        "choices": [
            {
                "message": {
                    "tool_calls": [
                        {
                            "id": "call_01",
                            "type": "function",
                            "function": {"name": "get_status", "arguments": "{}"},
                        }
                    ]
                }
            }
        ]
    }
    assert detect_tool_call(canned_openai_tool, "get_status") is True
    assert detect_tool_call(canned_openai_tool, "different_tool") is False


def test_detect_tool_call_negative() -> None:
    """Verify negative detection when no tool call exists."""
    canned_no_tool = {
        "content": [{"type": "text", "text": "No tools here."}]
    }
    assert detect_tool_call(canned_no_tool, "get_status") is False
    assert detect_tool_call({}, "get_status") is False


def test_extract_usage_anthropic() -> None:
    """Verify extracting token usage from Anthropic format."""
    canned_usage = {
        "usage": {
            "input_tokens": 128,
            "output_tokens": 64,
        }
    }
    usage = extract_usage(canned_usage)
    assert usage == {"input_tokens": 128, "output_tokens": 64}


def test_extract_usage_openai() -> None:
    """Verify extracting token usage from OpenAI format."""
    canned_usage = {
        "usage": {
            "prompt_tokens": 100,
            "completion_tokens": 50,
            "total_tokens": 150,
        }
    }
    usage = extract_usage(canned_usage)
    assert usage == {"prompt_tokens": 100, "completion_tokens": 50, "total_tokens": 150}


def test_extract_usage_empty() -> None:
    """Verify None when usage is absent or empty."""
    assert extract_usage({}) is None
    assert extract_usage({"usage": {}}) is None
