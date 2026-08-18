"""Configurable, explicitly estimated LLM token pricing."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
import json
import math
from pathlib import Path
from statistics import fmean
from typing import Any
from urllib.parse import urlparse

from ..config import BASE_URL, LLM_MODEL, LLM_PRICE_TABLE_PATH


@dataclass(frozen=True)
class PriceEntry:
    provider: str
    model: str
    currency: str
    effective_date: date
    input_per_million: float
    output_per_million: float


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
            input_price = float(raw["input_per_million"])
            output_price = float(raw["output_per_million"])
            provider = str(raw["provider"]).strip().lower()
            model = str(raw["model"]).strip()
            currency = str(raw["currency"]).strip().upper()
            if (
                input_price < 0
                or output_price < 0
                or not math.isfinite(input_price)
                or not math.isfinite(output_price)
                or not provider
                or not model
                or not currency
            ):
                continue
            entries.append(
                PriceEntry(
                    provider=provider,
                    model=model,
                    currency=currency,
                    effective_date=effective,
                    input_per_million=input_price,
                    output_per_million=output_price,
                )
            )
        except (KeyError, TypeError, ValueError):
            continue
    return entries


def estimate_cost(
    usage: dict[str, int | float],
    *,
    provider: str | None = None,
    model: str = LLM_MODEL,
    as_of: date | None = None,
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
        confidence = "exact"
        candidates = [selected]
    else:
        provider_entries = [entry for entry in eligible if entry.provider == provider_name]
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
    input_price = fmean(entry.input_per_million for entry in candidates)
    output_price = fmean(entry.output_per_million for entry in candidates)
    try:
        input_tokens = max(0.0, float(usage.get("input", 0)))
        output_tokens = max(0.0, float(usage.get("output", 0)))
    except (TypeError, ValueError):
        return CostEstimate(None, None, "unknown")
    if not math.isfinite(input_tokens) or not math.isfinite(output_tokens):
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
