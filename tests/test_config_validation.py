"""Validation tests for numeric environment settings."""

from collections.abc import Callable

import pytest

from agent import config as cfg


@pytest.mark.parametrize(
    ("name", "value", "reader"),
    [
        ("LLM_MAX_RETRIES", "-1", cfg.llm_kwargs),
        ("BUDGET_USD_PER_THREAD", "-0.1", cfg.budget_usd_per_thread),
        ("KNOWLEDGE_MIN_SCORE", "1.1", cfg.knowledge_min_score),
        ("CONFLUENCE_TIMEOUT_S", "0", cfg.confluence_timeout_s),
        ("API_MAX_REQUEST_BYTES", "100", cfg.api_max_request_bytes),
    ],
)
def test_out_of_range_numeric_settings_are_rejected(
    monkeypatch: pytest.MonkeyPatch,
    name: str,
    value: str,
    reader: Callable[[], object],
) -> None:
    monkeypatch.setenv(name, value)

    with pytest.raises(cfg.ConfigError, match=name):
        reader()


@pytest.mark.parametrize("value", ["nan", "inf", "-inf"])
def test_non_finite_float_settings_are_rejected(
    monkeypatch: pytest.MonkeyPatch,
    value: str,
) -> None:
    monkeypatch.setenv("BUDGET_USD_PER_THREAD", value)

    with pytest.raises(cfg.ConfigError, match="конечное"):
        cfg.budget_usd_per_thread()
