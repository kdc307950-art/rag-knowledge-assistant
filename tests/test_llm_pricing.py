"""Unit tests for explicit, non-billing LLM price estimates."""

from __future__ import annotations

from datetime import date, datetime
import json


def _write_prices(tmp_path, prices):
    path = tmp_path / "prices.json"
    path.write_text(
        json.dumps({"schema_version": 1, "prices": prices}), encoding="utf-8"
    )
    return path


def test_exact_model_price_uses_latest_effective_entry(tmp_path):
    from enterprise_rag.llm.pricing import estimate_cost

    path = _write_prices(
        tmp_path,
        [
            {
                "provider": "dashscope",
                "model": "qwen-test",
                "currency": "CNY",
                "effective_date": "2026-01-01",
                "input_per_million": 1,
                "output_per_million": 2,
            },
            {
                "provider": "dashscope",
                "model": "qwen-test",
                "currency": "CNY",
                "effective_date": "2026-08-01",
                "input_per_million": 3,
                "output_per_million": 5,
            },
        ],
    )

    result = estimate_cost(
        {"input": 1_000_000, "output": 2_000_000},
        provider="dashscope",
        model="qwen-test",
        as_of=date(2026, 8, 18),
        table_path=path,
    )

    assert result.amount == 13
    assert result.currency == "CNY"
    assert result.confidence == "exact"
    assert result.effective_date == "2026-08-01"


def test_unknown_model_uses_same_provider_average_and_marks_estimated(tmp_path):
    from enterprise_rag.llm.pricing import estimate_cost

    path = _write_prices(
        tmp_path,
        [
            {
                "provider": "dashscope",
                "model": "a",
                "currency": "CNY",
                "effective_date": "2026-01-01",
                "input_per_million": 2,
                "output_per_million": 4,
            },
            {
                "provider": "dashscope",
                "model": "b",
                "currency": "CNY",
                "effective_date": "2026-01-01",
                "input_per_million": 4,
                "output_per_million": 8,
            },
        ],
    )

    result = estimate_cost(
        {"input": 1_000_000, "output": 1_000_000},
        provider="dashscope",
        model="unlisted",
        table_path=path,
        as_of=date(2026, 8, 18),
    )

    assert result.amount == 9
    assert result.currency == "CNY"
    assert result.confidence == "estimated"


def test_fallback_uses_each_listed_models_current_price_not_historical_average(tmp_path):
    from enterprise_rag.llm.pricing import estimate_cost

    path = _write_prices(
        tmp_path,
        [
            {
                "provider": "dashscope", "model": "a", "currency": "CNY",
                "effective_date": "2026-01-01", "input_per_million": 1, "output_per_million": 1,
            },
            {
                "provider": "dashscope", "model": "a", "currency": "CNY",
                "effective_date": "2026-08-01", "input_per_million": 5, "output_per_million": 5,
            },
            {
                "provider": "dashscope", "model": "b", "currency": "CNY",
                "effective_date": "2026-01-01", "input_per_million": 3, "output_per_million": 3,
            },
        ],
    )

    result = estimate_cost(
        {"input": 1_000_000}, provider="dashscope", model="unlisted",
        as_of=date(2026, 8, 18), table_path=path,
    )

    assert result.amount == 4


def test_empty_future_or_mixed_currency_tables_do_not_invent_a_cost(tmp_path):
    from enterprise_rag.llm.pricing import estimate_cost

    empty = _write_prices(tmp_path, [])
    assert estimate_cost({"input": 1}, table_path=empty).amount is None

    future = _write_prices(
        tmp_path,
        [
            {
                "provider": "dashscope",
                "model": "a",
                "currency": "CNY",
                "effective_date": "2099-01-01",
                "input_per_million": 1,
                "output_per_million": 1,
            }
        ],
    )
    assert estimate_cost({"input": 1}, table_path=future).amount is None

    mixed = _write_prices(
        tmp_path,
        [
            {
                "provider": "dashscope",
                "model": "a",
                "currency": "CNY",
                "effective_date": "2026-01-01",
                "input_per_million": 1,
                "output_per_million": 1,
            },
            {
                "provider": "dashscope",
                "model": "b",
                "currency": "USD",
                "effective_date": "2026-01-01",
                "input_per_million": 1,
                "output_per_million": 1,
            },
        ],
    )
    result = estimate_cost(
        {"input": 1}, provider="dashscope", model="unlisted", table_path=mixed
    )
    assert result.amount is None
    assert result.confidence == "unknown"


def test_provider_detection_recognizes_deepseek_openai_endpoint():
    from enterprise_rag.llm.pricing import provider_from_base_url

    assert provider_from_base_url("https://api.deepseek.com") == "deepseek"


def test_deepseek_tariff_uses_cache_split_and_beijing_peak_window(tmp_path):
    from enterprise_rag.llm.pricing import estimate_cost

    path = _write_prices(
        tmp_path,
        [{
            "provider": "deepseek", "model": "deepseek-v4-flash",
            "currency": "CNY", "effective_date": "2026-08-18",
            "tariff": {
                "timezone": "Asia/Shanghai",
                "cache_hit_offpeak_per_million": 0.05,
                "cache_hit_peak_per_million": 0.1,
                "input_offpeak_per_million": 1.5,
                "input_peak_per_million": 3.0,
                "output_offpeak_per_million": 4.5,
                "output_peak_per_million": 9.0,
            },
        }],
    )
    usage = {"input": 1_000_000, "cached_input": 200_000, "uncached_input": 800_000, "output": 1_000_000}

    peak = estimate_cost(
        usage, provider="deepseek", model="deepseek-v4-flash", table_path=path,
        at=datetime.fromisoformat("2026-08-18T10:00:00+08:00"),
    )
    offpeak = estimate_cost(
        usage, provider="deepseek", model="deepseek-v4-flash", table_path=path,
        at=datetime.fromisoformat("2026-08-18T13:00:00+08:00"),
    )

    assert peak.amount == 11.42
    assert offpeak.amount == 5.71
    assert peak.confidence == "estimated"


def test_deepseek_tariff_uses_cache_miss_price_when_cache_split_is_missing(tmp_path):
    from enterprise_rag.llm.pricing import estimate_cost

    path = _write_prices(
        tmp_path,
        [{
            "provider": "deepseek", "model": "deepseek-v4-flash",
            "currency": "CNY", "effective_date": "2026-08-18",
            "tariff": {
                "timezone": "Asia/Shanghai",
                "cache_hit_offpeak_per_million": 0.05,
                "cache_hit_peak_per_million": 0.1,
                "input_offpeak_per_million": 1.5,
                "input_peak_per_million": 3.0,
                "output_offpeak_per_million": 4.5,
                "output_peak_per_million": 9.0,
            },
        }],
    )

    result = estimate_cost(
        {"input": 1_000_000, "output": 1_000_000},
        provider="deepseek", model="deepseek-v4-flash", table_path=path,
        at=datetime.fromisoformat("2026-08-18T13:00:00+08:00"),
    )

    assert result.amount == 6
    assert result.confidence == "estimated"
