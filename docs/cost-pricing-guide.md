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
