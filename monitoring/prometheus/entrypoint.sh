#!/bin/sh
# Prometheus 启动前把 METRICS_TOKEN 写入文件，供 scrape 用 Authorization: Bearer 认证。
#
# 缺 token 时直接退出：以前会写空文件并带空 Bearer 头去抓取，后端一律 401，
# 结果是 Prometheus 活着但每条时间序列都是空的，告警规则永远不触发——
# 这种“看起来在监控”的状态比起不来更危险。
set -e

if [ -z "$METRICS_TOKEN" ]; then
    echo "prometheus 启动被拒绝：METRICS_TOKEN 为空，无法认证抓取 /metrics" >&2
    echo "请在 .env 中设置 METRICS_TOKEN（与后端同值）后重试。" >&2
    exit 1
fi

# 写到 /tmp 而不是 /run：镜像以 nobody(65534) 运行，对 /run 没有写权限，
# `mkdir -p /run/prometheus` 会 Permission denied，配合 set -e 让容器起不来。
# 也不要写进 /prometheus——那是 tsdb 数据卷，凭据不该跟着时序数据一起持久化。
# 路径需与 prometheus.yml 的 credentials_file 保持一致。
TOKEN_FILE=/tmp/metrics_token
printf '%s' "$METRICS_TOKEN" > "$TOKEN_FILE"
chmod 600 "$TOKEN_FILE"

exec /bin/prometheus "$@"
