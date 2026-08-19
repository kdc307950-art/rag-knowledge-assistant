#!/bin/sh
# Prometheus 启动前把 METRICS_TOKEN 写入文件，供 scrape 用 Authorization: Bearer 认证
set -e
mkdir -p /run/prometheus
if [ -n "$METRICS_TOKEN" ]; then
    printf '%s' "$METRICS_TOKEN" > /run/prometheus/metrics_token
    chmod 600 /run/prometheus/metrics_token
else
    # 无 token：写空文件，Prometheus 发送空 Bearer 头，后端拒绝（生产预期有 token）
    touch /run/prometheus/metrics_token
fi
exec /bin/prometheus "$@"
