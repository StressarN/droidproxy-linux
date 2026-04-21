r"""Surgical JSON body transformations for the thinking proxy.

This module is a direct port of the Swift helpers in
``src/Sources/ThinkingProxy.swift``. It is intentionally written to produce
byte-identical output to the macOS version so Anthropic's prompt-cache
matching continues to work without invalidation.

The transforms operate on the raw JSON string (never re-serialised) because
``json.dumps`` would re-order keys and break cache equality with the macOS
app. We use the same value regex as Swift:

    (?:"(?:[^"\\]|\\.)*"|-?\d+(?:\.\d+)?|\{[^}]*\}|\[[^\]]*\]|true|false|null)

Note that the object branch ``\{[^}]*\}`` does not handle nested braces. This
mirrors the original Swift behaviour exactly — callers must pick
``after_key`` values whose values are scalars or single-level objects, which
is the case for every transform here.
"""

from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass
from typing import Literal

from droidproxy.prefs import Preferences

log = logging.getLogger(__name__)


_VALUE_PATTERN = (
    r'(?:"(?:[^"\\]|\\.)*"'
    r"|-?\d+(?:\.\d+)?"
    r"|\{[^}]*\}"
    r"|\[[^\]]*\]"
    r"|true|false|null)"
)


RESPONSES_API_PATHS = frozenset({"/v1/responses", "/api/v1/responses"})


def inject_json_field(
    body: str,
    *,
    after_key: str,
    field_name: str,
    field_value: str,
) -> str:
    """Insert ``,"field_name":field_value`` after the value of ``after_key``.

    Returns the original ``body`` unchanged when the key cannot be located.
    ``field_value`` must already be a serialised JSON fragment.
    """
    escaped = re.escape(after_key)
    pattern = re.compile(rf'"{escaped}"\s*:\s*{_VALUE_PATTERN}')
    match = pattern.search(body)
    if match is None:
        log.warning("Could not find key '%s' for field injection", after_key)
        return body
    insertion = f',"{field_name}":{field_value}'
    return body[: match.end()] + insertion + body[match.end():]


def replace_json_field_value(body: str, *, field_name: str, new_value: str) -> str:
    """Replace the value of ``field_name`` in-place, preserving its position."""
    escaped = re.escape(field_name)
    pattern = re.compile(rf'("{escaped}"\s*:\s*){_VALUE_PATTERN}')
    match = pattern.search(body)
    if match is None:
        log.warning("Could not find key '%s' for value replacement", field_name)
        return body
    prefix = match.group(1)
    return body[: match.start()] + f"{prefix}{new_value}" + body[match.end():]


def replace_or_inject_json_field(
    body: str,
    *,
    after_key: str,
    field_name: str,
    field_value: str,
    exists: bool,
) -> str:
    if exists:
        return replace_json_field_value(body, field_name=field_name, new_value=field_value)
    return inject_json_field(
        body, after_key=after_key, field_name=field_name, field_value=field_value
    )


def rewrite_model_value(body: str, *, old_model: str, new_model: str) -> str:
    """Rewrite the JSON ``model`` value (used when rewriting Gemini paths)."""
    escaped = re.escape(old_model)
    pattern = re.compile(rf'("model"\s*:\s*"){escaped}(")')
    match = pattern.search(body)
    if match is None:
        log.warning("Could not find model value '%s' for rewrite", old_model)
        return body
    return body[: match.start()] + f'"model":"{new_model}"' + body[match.end():]


def _codex_reasoning_effort(model: str, prefs: Preferences) -> str | None:
    if model == "gpt-5.3-codex":
        return prefs.gpt53_codex_reasoning_effort
    if model == "gpt-5.4":
        return prefs.gpt54_reasoning_effort
    return None


def _gemini_thinking_level(model: str, prefs: Preferences) -> str | None:
    if model == "gemini-3.1-pro-preview":
        return prefs.gemini31_pro_thinking_level
    if model == "gemini-3-flash-preview":
        return prefs.gemini3_flash_thinking_level
    return None


def _claude_adaptive_thinking_effort(model: str, prefs: Preferences) -> str | None:
    if not (model.startswith("claude-") or model.startswith("gemini-claude-")):
        return None
    if "opus-4-7" in model:
        return prefs.opus47_thinking_effort
    if "opus-4-6" in model:
        return prefs.opus46_thinking_effort
    if "sonnet-4-6" in model:
        return prefs.sonnet46_thinking_effort
    return None


def _is_opus_45_model(model: str) -> bool:
    """Match Opus 4.5 without matching ``opus-4-50`` or ``opus-4-5x`` variants.

    The ``opus-4-5`` token must be at end-of-string or followed by ``-``.
    """
    if not (model.startswith("claude-") or model.startswith("gemini-claude-")):
        return False
    idx = model.find("opus-4-5")
    if idx < 0:
        return False
    suffix = model[idx + len("opus-4-5"):]
    return suffix == "" or suffix.startswith("-")


def _opus_45_classic_budget(effort: str) -> tuple[int, int]:
    """Map effort to ``(budget_tokens, max_tokens)`` for Opus 4.5 classic thinking."""
    if effort == "low":
        return (4000, 16000)
    if effort == "medium":
        return (16000, 32000)
    if effort == "high":
        return (32000, 48000)
    if effort == "max":
        return (48000, 64000)
    return (32000, 48000)


@dataclass(frozen=True)
class InjectionOutcome:
    body: str
    kind: Literal[
        "none",
        "codex_reasoning",
        "gemini_thinking",
        "claude_adaptive",
        "claude_max_budget",
        "opus_45_classic",
    ]
    details: dict[str, str]


def apply_thinking_injection(body: str, prefs: Preferences) -> InjectionOutcome:
    """Return the body with thinking/reasoning/generationConfig injected.

    Preserves original byte ordering. ``kind`` identifies which branch
    executed so callers (and tests) can assert routing.
    """
    try:
        parsed = json.loads(body)
    except ValueError:
        return InjectionOutcome(body=body, kind="none", details={})

    if not isinstance(parsed, dict):
        return InjectionOutcome(body=body, kind="none", details={})

    model = parsed.get("model")
    if not isinstance(model, str):
        return InjectionOutcome(body=body, kind="none", details={})

    codex_effort = _codex_reasoning_effort(model, prefs)
    if codex_effort is not None:
        new_body = inject_json_field(
            body,
            after_key="model",
            field_name="reasoning",
            field_value=f'{{"effort":"{codex_effort}"}}',
        )
        return InjectionOutcome(
            body=new_body,
            kind="codex_reasoning",
            details={"model": model, "effort": codex_effort},
        )

    gemini_level = _gemini_thinking_level(model, prefs)
    if gemini_level is not None:
        new_body = inject_json_field(
            body,
            after_key="model",
            field_name="generationConfig",
            field_value=f'{{"thinkingConfig":{{"thinking_level":"{gemini_level}"}}}}',
        )
        return InjectionOutcome(
            body=new_body,
            kind="gemini_thinking",
            details={"model": model, "level": gemini_level},
        )

    # Opus 4.5 does not accept adaptive thinking; use classic extended thinking
    # (``thinking: {type: "enabled", budget_tokens: N}`` with ``budget_tokens < max_tokens``).
    if _is_opus_45_model(model):
        effort = prefs.opus45_thinking_effort
        budget_tokens, max_tokens = _opus_45_classic_budget(effort)
        has_stream_ = "stream" in parsed
        has_thinking_ = "thinking" in parsed
        has_max_tokens_ = "max_tokens" in parsed

        new_body = body
        new_body = replace_or_inject_json_field(
            new_body,
            after_key="model",
            field_name="stream",
            field_value="true",
            exists=has_stream_,
        )
        new_body = replace_or_inject_json_field(
            new_body,
            after_key="model",
            field_name="max_tokens",
            field_value=str(max_tokens),
            exists=has_max_tokens_,
        )
        new_body = replace_or_inject_json_field(
            new_body,
            after_key="max_tokens",
            field_name="thinking",
            field_value=f'{{"type":"enabled","budget_tokens":{budget_tokens}}}',
            exists=has_thinking_,
        )
        return InjectionOutcome(
            body=new_body,
            kind="opus_45_classic",
            details={
                "model": model,
                "effort": effort,
                "max_tokens": str(max_tokens),
                "budget_tokens": str(budget_tokens),
            },
        )

    claude_effort = _claude_adaptive_thinking_effort(model, prefs)
    if claude_effort is None:
        return InjectionOutcome(body=body, kind="none", details={})

    has_stream = "stream" in parsed
    has_thinking = "thinking" in parsed
    has_output_config = "output_config" in parsed
    has_max_tokens = "max_tokens" in parsed

    new_body = body
    new_body = replace_or_inject_json_field(
        new_body,
        after_key="model",
        field_name="stream",
        field_value="true",
        exists=has_stream,
    )

    if prefs.claude_max_budget_mode and ("sonnet-4-6" in model or "opus-4-6" in model):
        max_tokens = 64000
        budget_tokens = max_tokens - 1
        new_body = replace_or_inject_json_field(
            new_body,
            after_key="model",
            field_name="max_tokens",
            field_value=str(max_tokens),
            exists=has_max_tokens,
        )
        new_body = replace_or_inject_json_field(
            new_body,
            after_key="max_tokens",
            field_name="thinking",
            field_value=f'{{"type":"enabled","budget_tokens":{budget_tokens}}}',
            exists=has_thinking,
        )
        new_body = replace_or_inject_json_field(
            new_body,
            after_key="thinking",
            field_name="output_config",
            field_value='{"effort":"max"}',
            exists=has_output_config,
        )
        return InjectionOutcome(
            body=new_body,
            kind="claude_max_budget",
            details={
                "model": model,
                "max_tokens": str(max_tokens),
                "budget_tokens": str(budget_tokens),
            },
        )

    new_body = replace_or_inject_json_field(
        new_body,
        after_key="model",
        field_name="thinking",
        field_value='{"type":"adaptive"}',
        exists=has_thinking,
    )
    new_body = replace_or_inject_json_field(
        new_body,
        after_key="thinking",
        field_name="output_config",
        field_value=f'{{"effort":"{claude_effort}"}}',
        exists=has_output_config,
    )
    return InjectionOutcome(
        body=new_body,
        kind="claude_adaptive",
        details={"model": model, "effort": claude_effort},
    )


def apply_fast_mode(body: str, path: str, prefs: Preferences) -> str | None:
    """Inject ``"service_tier":"priority"`` for eligible models on /v1/responses.

    Returns the modified body, or ``None`` if no change was made.
    """
    normalized = path.split("?", 1)[0]
    if normalized not in RESPONSES_API_PATHS:
        return None
    try:
        parsed = json.loads(body)
    except ValueError:
        return None
    if not isinstance(parsed, dict):
        return None
    model = parsed.get("model")
    if model == "gpt-5.4":
        if not prefs.gpt54_fast_mode:
            return None
    elif model == "gpt-5.3-codex":
        if not prefs.gpt53_codex_fast_mode:
            return None
    else:
        return None
    if parsed.get("service_tier") is not None:
        return None
    return inject_json_field(
        body,
        after_key="model",
        field_name="service_tier",
        field_value='"priority"',
    )


def is_gemini_model(body: str) -> bool:
    """Returns True if the body's ``model`` field starts with ``gemini-``."""
    try:
        parsed = json.loads(body)
    except ValueError:
        return False
    model = parsed.get("model") if isinstance(parsed, dict) else None
    return isinstance(model, str) and model.startswith("gemini-")
