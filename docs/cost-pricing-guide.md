# LLM Price Table Guide

`config/llm_prices.json` remains empty until a provider price is verified. An
invented price is worse than an unknown estimate because it pollutes diagnostics
and operational decisions.

## Required fields

```json
{
  "schema_version": 1,
  "prices": [{
    "provider": "dashscope",
    "model": "qwen3.7-flash",
    "currency": "CNY",
    "effective_date": "2026-08-18",
    "input_per_million": 0,
    "output_per_million": 0
  }]
}
```

Replace zero placeholders only after checking the provider's official pricing
page or console. Record the official URL, account region, retrieval date, and
discount policy in the change note. The estimator covers ordinary input/output
usage only; cache and batch prices are separate concerns. Mixed currencies
return `unknown` instead of being averaged.

## DeepSeek-V4 screenshot review

The supplied pricing screenshots show the following CNY prices per million
tokens:

| model | cache hit off-peak / peak | cache miss off-peak / peak | output off-peak / peak |
| --- | ---: | ---: | ---: |
| DeepSeek-V4-Flash-0731 | 0.05 / 0.10 | 1.50 / 3.00 | 4.50 / 9.00 |
| DeepSeek-V4-Pro-0813 | 0.15 / 0.30 | 4.50 / 9.00 | 13.50 / 27.00 |

These values are recorded from a user-provided image, not independently
verified against a provider source. They are therefore **not** added to the
active price table. The current `.env` model is `qwen3.7-flash`, while the
repository fallback configuration is `deepseek-v4-flash`; applying these
numbers without confirming the active model would be a model-mapping error.

The current estimator also cannot infer cache-hit tokens or Beijing peak
windows from the existing usage payload. Before enabling a DeepSeek profile,
extend the schema to record `cached_input_tokens`, request timezone, and the
peak/off-peak tariff, then verify the provider's exact model IDs and effective
date. Do not collapse the screenshot's peak price into a single average price.
