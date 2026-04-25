"""Byte-stable parity tests for the JSON field injector.

These outputs match what the Swift implementation produces today. If any
assertion starts failing after a refactor, prompt-caching on Anthropic will
break for real users, so treat a red test here as a production issue.
"""

from __future__ import annotations

from droidproxy.injector import (
    apply_fast_mode,
    apply_thinking_injection,
    inject_json_field,
    replace_json_field_value,
    replace_or_inject_json_field,
    rewrite_model_value,
)
from droidproxy.prefs import Preferences


def _prefs(**overrides) -> Preferences:
    base = Preferences()
    for key, value in overrides.items():
        setattr(base, key, value)
    return base


def test_inject_after_string_value() -> None:
    body = '{"model":"claude-opus-4-7","messages":[]}'
    result = inject_json_field(
        body, after_key="model", field_name="stream", field_value="true"
    )
    assert result == '{"model":"claude-opus-4-7","stream":true,"messages":[]}'


def test_inject_after_numeric_value() -> None:
    body = '{"max_tokens":4096,"messages":[]}'
    result = inject_json_field(
        body,
        after_key="max_tokens",
        field_name="thinking",
        field_value='{"type":"enabled","budget_tokens":63999}',
    )
    assert result == (
        '{"max_tokens":4096,"thinking":{"type":"enabled","budget_tokens":63999},"messages":[]}'
    )


def test_inject_missing_key_is_noop() -> None:
    body = '{"messages":[]}'
    result = inject_json_field(
        body, after_key="model", field_name="stream", field_value="true"
    )
    assert result == body


def test_replace_scalar_value() -> None:
    body = '{"model":"x","thinking":"placeholder","foo":1}'
    result = replace_json_field_value(body, field_name="thinking", new_value='{"type":"adaptive"}')
    assert result == '{"model":"x","thinking":{"type":"adaptive"},"foo":1}'


def test_replace_object_value() -> None:
    body = '{"thinking":{"type":"enabled","budget_tokens":1024}}'
    result = replace_json_field_value(body, field_name="thinking", new_value='{"type":"adaptive"}')
    assert result == '{"thinking":{"type":"adaptive"}}'


def test_replace_or_inject_dispatches_on_exists_flag() -> None:
    without = '{"model":"gpt-5.5"}'
    with_field = '{"model":"gpt-5.5","reasoning":"old"}'

    injected = replace_or_inject_json_field(
        without,
        after_key="model",
        field_name="reasoning",
        field_value='{"effort":"high"}',
        exists=False,
    )
    replaced = replace_or_inject_json_field(
        with_field,
        after_key="model",
        field_name="reasoning",
        field_value='{"effort":"high"}',
        exists=True,
    )

    assert injected == '{"model":"gpt-5.5","reasoning":{"effort":"high"}}'
    assert replaced == '{"model":"gpt-5.5","reasoning":{"effort":"high"}}'


def test_rewrite_model_value() -> None:
    body = '{"model":"gemini-3-flash-preview","contents":[]}'
    result = rewrite_model_value(
        body, old_model="gemini-3-flash-preview", new_model="gemini-3-flash"
    )
    assert result == '{"model":"gemini-3-flash","contents":[]}'


# --- high-level apply_thinking_injection snapshots ---------------------------


def test_opus_47_adaptive_injection() -> None:
    body = '{"model":"claude-opus-4-7","messages":[]}'
    outcome = apply_thinking_injection(body, _prefs(opus47_thinking_effort="xhigh"))
    assert outcome.kind == "claude_adaptive"
    assert outcome.body == (
        '{"model":"claude-opus-4-7","thinking":{"type":"adaptive"},'
        '"output_config":{"effort":"xhigh"},"stream":true,"messages":[]}'
    )


def test_sonnet_46_adaptive_injection_default_effort() -> None:
    body = '{"model":"claude-sonnet-4-6","messages":[]}'
    outcome = apply_thinking_injection(body, _prefs(sonnet46_thinking_effort="high"))
    assert outcome.kind == "claude_adaptive"
    assert outcome.body == (
        '{"model":"claude-sonnet-4-6","thinking":{"type":"adaptive"},'
        '"output_config":{"effort":"high"},"stream":true,"messages":[]}'
    )


def test_sonnet_46_max_budget_mode() -> None:
    body = '{"model":"claude-sonnet-4-6","messages":[]}'
    outcome = apply_thinking_injection(
        body, _prefs(claude_max_budget_mode=True, sonnet46_thinking_effort="high")
    )
    assert outcome.kind == "claude_max_budget"
    assert outcome.body == (
        '{"model":"claude-sonnet-4-6","max_tokens":64000,'
        '"thinking":{"type":"enabled","budget_tokens":63999},'
        '"output_config":{"effort":"max"},"stream":true,"messages":[]}'
    )


def test_opus_47_unaffected_by_max_budget_mode() -> None:
    body = '{"model":"claude-opus-4-7","messages":[]}'
    outcome = apply_thinking_injection(body, _prefs(claude_max_budget_mode=True))
    assert outcome.kind == "claude_adaptive"
    assert "max_tokens" not in outcome.body


def test_opus_46_adaptive_injection_default_effort() -> None:
    body = '{"model":"claude-opus-4-6","messages":[]}'
    outcome = apply_thinking_injection(body, _prefs(opus46_thinking_effort="max"))
    assert outcome.kind == "claude_adaptive"
    assert outcome.body == (
        '{"model":"claude-opus-4-6","thinking":{"type":"adaptive"},'
        '"output_config":{"effort":"max"},"stream":true,"messages":[]}'
    )


def test_opus_46_max_budget_mode() -> None:
    body = '{"model":"claude-opus-4-6","messages":[]}'
    outcome = apply_thinking_injection(
        body, _prefs(claude_max_budget_mode=True, opus46_thinking_effort="high")
    )
    assert outcome.kind == "claude_max_budget"
    assert outcome.body == (
        '{"model":"claude-opus-4-6","max_tokens":64000,'
        '"thinking":{"type":"enabled","budget_tokens":63999},'
        '"output_config":{"effort":"max"},"stream":true,"messages":[]}'
    )


def test_opus_45_classic_thinking_default_effort() -> None:
    body = '{"model":"claude-opus-4-5-20251101","messages":[]}'
    outcome = apply_thinking_injection(body, _prefs(opus45_thinking_effort="high"))
    assert outcome.kind == "opus_45_classic"
    # high -> (32000, 48000)
    assert outcome.body == (
        '{"model":"claude-opus-4-5-20251101","max_tokens":48000,'
        '"thinking":{"type":"enabled","budget_tokens":32000},'
        '"stream":true,"messages":[]}'
    )


def test_opus_45_classic_thinking_max_effort() -> None:
    body = '{"model":"claude-opus-4-5","messages":[]}'
    outcome = apply_thinking_injection(body, _prefs(opus45_thinking_effort="max"))
    assert outcome.kind == "opus_45_classic"
    # max -> (48000, 64000)
    assert '"max_tokens":64000' in outcome.body
    assert '"thinking":{"type":"enabled","budget_tokens":48000}' in outcome.body
    # No output_config for classic thinking
    assert "output_config" not in outcome.body


def test_opus_45_does_not_match_opus_4_50() -> None:
    body = '{"model":"claude-opus-4-50","messages":[]}'
    outcome = apply_thinking_injection(body, _prefs())
    # Should not be classified as Opus 4.5 (no false-positive match).
    assert outcome.kind != "opus_45_classic"


def test_opus_45_matches_gemini_claude_alias() -> None:
    body = '{"model":"gemini-claude-opus-4-5","messages":[]}'
    outcome = apply_thinking_injection(body, _prefs(opus45_thinking_effort="low"))
    assert outcome.kind == "opus_45_classic"
    # low -> (4000, 16000)
    assert '"max_tokens":16000' in outcome.body
    assert '"budget_tokens":4000' in outcome.body


def test_existing_thinking_is_replaced_not_duplicated() -> None:
    body = '{"model":"claude-opus-4-7","thinking":{"type":"enabled","budget_tokens":1024}}'
    outcome = apply_thinking_injection(body, _prefs(opus47_thinking_effort="max"))
    assert outcome.kind == "claude_adaptive"
    assert outcome.body.count('"thinking"') == 1
    assert '"thinking":{"type":"adaptive"}' in outcome.body
    assert '"output_config":{"effort":"max"}' in outcome.body


def test_existing_stream_is_replaced_not_duplicated() -> None:
    body = '{"model":"claude-opus-4-7","stream":false,"messages":[]}'
    outcome = apply_thinking_injection(body, _prefs())
    assert outcome.body.count('"stream"') == 1
    assert '"stream":true' in outcome.body


def test_codex_reasoning_gpt_55() -> None:
    body = '{"model":"gpt-5.5","input":"hi"}'
    outcome = apply_thinking_injection(body, _prefs(gpt55_reasoning_effort="high"))
    assert outcome.kind == "codex_reasoning"
    assert outcome.body == '{"model":"gpt-5.5","reasoning":{"effort":"high"},"input":"hi"}'


def test_codex_reasoning_gpt_54_is_no_longer_targeted() -> None:
    body = '{"model":"gpt-5.4","input":"hi"}'
    outcome = apply_thinking_injection(body, _prefs(gpt55_reasoning_effort="high"))
    assert outcome.kind == "none"
    assert outcome.body == body


def test_codex_reasoning_gpt_53_codex() -> None:
    body = '{"model":"gpt-5.3-codex","input":"hi"}'
    outcome = apply_thinking_injection(body, _prefs(gpt53_codex_reasoning_effort="medium"))
    assert outcome.kind == "codex_reasoning"
    assert outcome.body == (
        '{"model":"gpt-5.3-codex","reasoning":{"effort":"medium"},"input":"hi"}'
    )


def test_gemini_31_pro_thinking() -> None:
    body = '{"model":"gemini-3.1-pro-preview","contents":[]}'
    outcome = apply_thinking_injection(body, _prefs(gemini31_pro_thinking_level="high"))
    assert outcome.kind == "gemini_thinking"
    assert outcome.body == (
        '{"model":"gemini-3.1-pro-preview",'
        '"generationConfig":{"thinkingConfig":{"thinking_level":"high"}},"contents":[]}'
    )


def test_gemini_3_flash_thinking_minimal() -> None:
    body = '{"model":"gemini-3-flash-preview","contents":[]}'
    outcome = apply_thinking_injection(body, _prefs(gemini3_flash_thinking_level="minimal"))
    assert outcome.kind == "gemini_thinking"
    assert outcome.body == (
        '{"model":"gemini-3-flash-preview",'
        '"generationConfig":{"thinkingConfig":{"thinking_level":"minimal"}},"contents":[]}'
    )


def test_gemini_claude_alias_routes_to_claude_adaptive() -> None:
    body = '{"model":"gemini-claude-opus-4-7","messages":[]}'
    outcome = apply_thinking_injection(body, _prefs())
    assert outcome.kind == "claude_adaptive"


def test_unsupported_model_leaves_body_untouched() -> None:
    body = '{"model":"claude-haiku-3-5","messages":[]}'
    outcome = apply_thinking_injection(body, _prefs())
    assert outcome.kind == "none"
    assert outcome.body == body


def test_invalid_json_is_noop() -> None:
    body = "not-json"
    outcome = apply_thinking_injection(body, _prefs())
    assert outcome.kind == "none"
    assert outcome.body == body


def test_non_ascii_body_preserved() -> None:
    body = '{"model":"claude-opus-4-7","messages":[{"role":"user","content":"日本語"}]}'
    outcome = apply_thinking_injection(body, _prefs())
    assert outcome.kind == "claude_adaptive"
    assert "日本語" in outcome.body


# --- fast-mode branch --------------------------------------------------------


def test_fast_mode_injected_for_gpt_55_on_responses_path() -> None:
    body = '{"model":"gpt-5.5","input":"hi"}'
    result = apply_fast_mode(body, "/v1/responses", _prefs(gpt55_fast_mode=True))
    assert result == '{"model":"gpt-5.5","service_tier":"priority","input":"hi"}'


def test_fast_mode_honours_query_string_in_path() -> None:
    body = '{"model":"gpt-5.5","input":"hi"}'
    result = apply_fast_mode(body, "/v1/responses?stream=true", _prefs(gpt55_fast_mode=True))
    assert result == '{"model":"gpt-5.5","service_tier":"priority","input":"hi"}'


def test_fast_mode_injected_for_gpt_53_codex() -> None:
    body = '{"model":"gpt-5.3-codex","input":"hi"}'
    result = apply_fast_mode(body, "/api/v1/responses", _prefs(gpt53_codex_fast_mode=True))
    assert result == (
        '{"model":"gpt-5.3-codex","service_tier":"priority","input":"hi"}'
    )


def test_fast_mode_skipped_when_preference_off() -> None:
    body = '{"model":"gpt-5.5","input":"hi"}'
    assert apply_fast_mode(body, "/v1/responses", _prefs(gpt55_fast_mode=False)) is None


def test_fast_mode_skipped_on_non_responses_path() -> None:
    body = '{"model":"gpt-5.5","input":"hi"}'
    assert apply_fast_mode(body, "/v1/chat/completions", _prefs(gpt55_fast_mode=True)) is None


def test_fast_mode_skipped_when_service_tier_already_present() -> None:
    body = '{"model":"gpt-5.5","service_tier":"priority","input":"hi"}'
    assert apply_fast_mode(body, "/v1/responses", _prefs(gpt55_fast_mode=True)) is None
