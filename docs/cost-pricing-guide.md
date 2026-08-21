# LLM 价格表指南

> 文档版本：`0.2`
> 适用应用版本：`0.1.0`
> 最近核对：`2026-08-21`

价格输入的唯一事实源是 `config/llm_prices.json`。指标中的成本始终是估算值，不是供应商账单或财务对账结果。

## 当前状态

价格表当前使用 `schema_version=2`，包含 `deepseek-v4-flash` 和 `deepseek-v4-pro` 两条 CNY 分时价格。来源是用户提供的 DeepSeek 价格截图，配置中的本地生效日期为 `2026-08-18`；截图本身不包含供应商正式生效日期，因此这些条目不能被描述为已由官方价格公告验证。

如果价格来源、区域、币种、折扣或生效日期无法确认，应删除或停用对应估算，而不是填入猜测价格。

## Schema v2 示例

```json
{
  "schema_version": 2,
  "prices": [
    {
      "provider": "deepseek",
      "model": "deepseek-v4-flash",
      "currency": "CNY",
      "effective_date": "2026-08-18",
      "source": "controlled evidence reference",
      "tariff": {
        "timezone": "Asia/Shanghai",
        "cache_hit_offpeak_per_million": 0.05,
        "cache_hit_peak_per_million": 0.10,
        "input_offpeak_per_million": 1.50,
        "input_peak_per_million": 3.00,
        "output_offpeak_per_million": 4.50,
        "output_peak_per_million": 9.00
      }
    }
  ]
}
```

DeepSeek 当前峰时窗口按代码使用 `Asia/Shanghai` 的 09:00-12:00 和 14:00-18:00。供应商返回缓存命中/未命中 usage 时分别计价；缓存状态未知时按 cache miss 估算。

## 更新流程

1. 从供应商官方页面、控制台或受控合同取得价格。
2. 记录 URL 或证据编号、账户区域、币种、税费/折扣、生效日期和核对日期。
3. 使用与供应商返回完全一致的模型 ID；不要把营销名称映射为猜测 ID。
4. 修改 `config/llm_prices.json` 和本指南的当前状态。
5. 运行 `tests/test_llm_pricing.py` 和 `tests/test_llm_usage.py`。
6. 在 `CHANGELOG.md` 记录成本估算影响。
7. 与供应商账单抽样对比，保留差异；不要把估算标签改成实际账单。

混合币种聚合应返回 unknown，不做无汇率依据的相加。缓存、batch、免费额度和阶梯折扣只有在实现与证据同时支持时才能纳入。
