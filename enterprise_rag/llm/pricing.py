"""Configurable, explicitly estimated LLM token pricing."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, time
import json
import math
from pathlib import Path
from statistics import fmean
from typing import Any
from urllib.parse import urlparse
from zoneinfo import ZoneInfo

from ..config import BASE_URL, LLM_MODEL, LLM_PRICE_TABLE_PATH


@dataclass(frozen=True)
class PriceEntry:
    provider: str
    model: str
    currency: str
    effective_date: date
    input_per_million: float | None = None
    output_per_million: float | None = None
    tariff: "TimeOfDayTariff | None" = None


@dataclass(frozen=True)
class TimeOfDayTariff:
    timezone: str
    cache_hit_offpeak_per_million: float
    cache_hit_peak_per_million: float
    input_offpeak_per_million: float
    input_peak_per_million: float
    output_offpeak_per_million: float
    output_peak_per_million: float


@dataclass(frozen=True)
class CostEstimate:
    amount: float | None
    currency: str | None
    confidence: str
    matched_model: str | None = None
    effective_date: str | None = None


def provider_from_base_url(base_url: str = BASE_URL) -> str:
    host = (urlparse(base_url).hostname or "").lower()
    if "dashscope.aliyuncs.com" in host:
        return "dashscope"
    if "api.deepseek.com" in host:
        return "deepseek"
    if "openai.com" in host:
        return "openai"
    return host or "unknown"


def _load_entries(path: Path = LLM_PRICE_TABLE_PATH) -> list[PriceEntry]:
    if not path.is_file():
        return []
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError):
        return []
    raw_entries = payload.get("prices") if isinstance(payload, dict) else None
    if not isinstance(raw_entries, list):
        return []
    entries: list[PriceEntry] = []
    for raw in raw_entries:
        if not isinstance(raw, dict):
            continue
        try:
            effective = date.fromisoformat(str(raw["effective_date"]))
            provider = str(raw["provider"]).strip().lower()
            model = str(raw["model"]).strip()
            currency = str(raw["currency"]).strip().upper()
            if not provider or not model or not currency:
                continue
            raw_tariff = raw.get("tariff")
            tariff = _parse_tariff(raw_tariff) if isinstance(raw_tariff, dict) else None
            if tariff is None:
                input_price = float(raw["input_per_million"])
                output_price = float(raw["output_per_million"])
                if (
                    input_price < 0
                    or output_price < 0
                    or not math.isfinite(input_price)
                    or not math.isfinite(output_price)
                ):
                    continue
            else:
                input_price = None
                output_price = None
            entries.append(
                PriceEntry(
                    provider=provider,
                    model=model,
                    currency=currency,
                    effective_date=effective,
                    input_per_million=input_price,
                    output_per_million=output_price,
                    tariff=tariff,
                )
            )
        except (KeyError, TypeError, ValueError):
            continue
    return entries


def _parse_tariff(raw: dict[str, Any]) -> TimeOfDayTariff | None:
    """Parse a time-of-day tariff only when every required rate is usable."""
    try:
        timezone_name = str(raw["timezone"]).strip()
        ZoneInfo(timezone_name)
        values = {
            name: float(raw[name])
            for name in (
                "cache_hit_offpeak_per_million",
                "cache_hit_peak_per_million",
                "input_offpeak_per_million",
                "input_peak_per_million",
                "output_offpeak_per_million",
                "output_peak_per_million",
            )
        }
    except (KeyError, TypeError, ValueError):
        return None
    if not timezone_name or any(value < 0 or not math.isfinite(value) for value in values.values()):
        return None
    return TimeOfDayTariff(timezone=timezone_name, **values)


def _finite_usage(usage: dict[str, int | float], key: str) -> float | None:
    try:
        value = float(usage.get(key, 0))
    except (TypeError, ValueError):
        return None
    if value < 0 or not math.isfinite(value):
        return None
    return value


def _is_beijing_peak(at: datetime, timezone_name: str) -> bool:
    """Classify the supplied tariff's Beijing peak windows [09:00,12:00), [14:00,18:00)."""
    local = at.astimezone(ZoneInfo(timezone_name)).timetz().replace(tzinfo=None)
    return time(9, 0) <= local < time(12, 0) or time(14, 0) <= local < time(18, 0)


def _estimate_tariff_cost(
    entry: PriceEntry,
    usage: dict[str, int | float],
    *,
    at: datetime | None,
) -> CostEstimate:
    tariff = entry.tariff
    if tariff is None:
        return CostEstimate(None, None, "unknown")
    now = at or datetime.now(ZoneInfo(tariff.timezone))
    peak = _is_beijing_peak(now, tariff.timezone)
    cache_hit_price = tariff.cache_hit_peak_per_million if peak else tariff.cache_hit_offpeak_per_million
    input_price = tariff.input_peak_per_million if peak else tariff.input_offpeak_per_million
    output_price = tariff.output_peak_per_million if peak else tariff.output_offpeak_per_million
    input_tokens = _finite_usage(usage, "input")
    output_tokens = _finite_usage(usage, "output")
    cached_input = _finite_usage(usage, "cached_input")
    uncached_input = _finite_usage(usage, "uncached_input")
    if input_tokens is None or output_tokens is None:
        return CostEstimate(None, None, "unknown")
    if cached_input is None:
        cached_input = 0.0
    if uncached_input is None:
        uncached_input = max(0.0, input_tokens - cached_input)
    if cached_input + uncached_input > input_tokens:
        return CostEstimate(None, None, "unknown")
    # Providers occasionally omit a split for a small remainder. Pricing it as
    # an ordinary input token is conservative and explicitly not bill-exact.
    remainder = input_tokens - cached_input - uncached_input
    amount = (
        cached_input * cache_hit_price
        + (uncached_input + remainder) * input_price
        + output_tokens * output_price
    ) / 1_000_000
    return CostEstimate(
        amount=amount,
        currency=entry.currency,
        confidence="estimated",
        matched_model=entry.model,
        effective_date=entry.effective_date.isoformat(),
    )


def estimate_cost(
    usage: dict[str, int | float],
    *,
    provider: str | None = None,
    model: str = LLM_MODEL,
    as_of: date | None = None,
    at: datetime | None = None,
    table_path: Path = LLM_PRICE_TABLE_PATH,
) -> CostEstimate:
    """Estimate cost; never claim exact billing when using an average price."""
    entries = _load_entries(table_path)
    if not entries:
        return CostEstimate(None, None, "unknown")
    provider_name = (provider or provider_from_base_url()).strip().lower()
    current = as_of or date.today()
    eligible = [entry for entry in entries if entry.effective_date <= current]
    if not eligible:
        return CostEstimate(None, None, "unknown")

    exact = [
        entry
        for entry in eligible
        if entry.provider == provider_name and entry.model == model
    ]
    if exact:
        selected = max(exact, key=lambda entry: entry.effective_date)
        if selected.tariff is not None:
            return _estimate_tariff_cost(selected, usage, at=at)
        confidence = "exact"
        candidates = [selected]
    else:
        provider_entries = [
            entry for entry in eligible
            if entry.provider == provider_name and entry.tariff is None
        ]
        candidates = provider_entries or eligible
        # A historical price is not today's fallback price. Keep only each
        # listed model's latest price effective on the requested date before
        # calculating a deliberately coarse provider average.
        latest_by_model: dict[tuple[str, str, str], PriceEntry] = {}
        for entry in candidates:
            key = (entry.provider, entry.model, entry.currency)
            previous = latest_by_model.get(key)
            if previous is None or entry.effective_date > previous.effective_date:
                latest_by_model[key] = entry
        candidates = list(latest_by_model.values())
        currencies = {entry.currency for entry in candidates}
        if len(currencies) != 1:
            # Averaging USD and CNY (or any other currencies) is meaningless.
            return CostEstimate(None, None, "unknown")
        confidence = "estimated"

    if not candidates:
        return CostEstimate(None, None, "unknown")
    currency = candidates[0].currency
    if any(entry.input_per_million is None or entry.output_per_million is None for entry in candidates):
        return CostEstimate(None, None, "unknown")
    input_price = fmean(entry.input_per_million for entry in candidates if entry.input_per_million is not None)
    output_price = fmean(entry.output_per_million for entry in candidates if entry.output_per_million is not None)
    input_tokens = _finite_usage(usage, "input")
    output_tokens = _finite_usage(usage, "output")
    if input_tokens is None or output_tokens is None:
        return CostEstimate(None, None, "unknown")
    amount = (input_tokens * input_price + output_tokens * output_price) / 1_000_000
    return CostEstimate(
        amount=amount,
        currency=currency,
        confidence=confidence,
        matched_model=candidates[0].model if confidence == "exact" else None,
        effective_date=max(entry.effective_date for entry in candidates).isoformat(),
    )


def price_table_status(path: Path = LLM_PRICE_TABLE_PATH) -> dict[str, Any]:
    entries = _load_entries(path)
    return {
        "entries": len(entries),
        "configured": bool(entries),
    }
