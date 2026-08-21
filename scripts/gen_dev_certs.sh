#!/bin/sh
# 生成本地验收用的自签 TLS 证书到 ./certs/。
#
# 仅用于本地 docker compose 集成验收：浏览器和 curl 都会因为证书不受信任而报错，
# curl 需加 -k。生产环境请改用 Let's Encrypt 或企业 CA 签发的真实证书，
# 覆盖同名的 certs/fullchain.pem 与 certs/privkey.pem。
#
# 用法：
#   bash scripts/gen_dev_certs.sh              # CN=localhost
#   bash scripts/gen_dev_certs.sh kb.example.com
set -e

DOMAIN="${1:-localhost}"
CERT_DIR="$(cd "$(dirname "$0")/.." && pwd)/certs"

mkdir -p "$CERT_DIR"

if [ -f "$CERT_DIR/fullchain.pem" ] && [ -f "$CERT_DIR/privkey.pem" ]; then
    echo "已存在 $CERT_DIR/fullchain.pem，跳过生成。删除后重跑可覆盖。"
    exit 0
fi

# openssl 失败时不要留下半套证书：只有私钥、没有证书链的目录会让 nginx 报一个
# 和真实原因无关的错。用 trap 清理，成功路径上再解除。
cleanup_partial() {
    rm -f "$CERT_DIR/privkey.pem" "$CERT_DIR/fullchain.pem"
}
trap cleanup_partial EXIT

# Git Bash / MSYS2 会把以 / 开头的参数当成路径改写，使 -subj "/CN=host" 变成
# "D:/git/.../CN=host"，openssl 直接报错。这里只豁免 /CN= 开头的参数——
# 不能用 MSYS_NO_PATHCONV=1 全局关闭，那会连 -keyout/-out 的 /d/... 路径
# 一起停止转换，Windows 版 openssl 反而写不出文件。
# 该变量在 Linux 上是无用变量，不影响生产环境执行。
MSYS2_ARG_CONV_EXCL='/CN=' openssl req -x509 -nodes -newkey rsa:2048 -days 365 \
    -keyout "$CERT_DIR/privkey.pem" \
    -out "$CERT_DIR/fullchain.pem" \
    -subj "/CN=$DOMAIN" \
    -addext "subjectAltName=DNS:$DOMAIN,DNS:localhost,IP:127.0.0.1"

chmod 600 "$CERT_DIR/privkey.pem"

trap - EXIT

echo "已生成自签证书（CN=$DOMAIN，有效期 365 天）："
echo "  $CERT_DIR/fullchain.pem"
echo "  $CERT_DIR/privkey.pem"
echo "certs/ 已在 .gitignore 中，不会被提交。"
