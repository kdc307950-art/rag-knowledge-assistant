# Windows 反向代理参考

> 文档版本：`0.2`
> 适用应用版本：`0.1.0`
> 支持级别：本地或受控内网参考，不是正式生产拓扑
> 最近核对：`2026-08-21`

正式生产部署使用 [Linux Compose + Nginx 手册](deployment-runbook.md)。本文仅说明 Windows 上已有的 Caddy/Nginx 参考拓扑：浏览器 -> HTTPS 反向代理 -> `127.0.0.1:8000`，代理同时服务 `frontend/dist` 以保持同源。

## 前提和边界

- FastAPI 只监听 `127.0.0.1:8000`，保持一个 worker。
- 先执行 `frontend` 的 `npm run build`。
- 局域网暴露至少使用 `single_user`；多人使用必须为 `multi_user/users`。
- `/api/live`、`/api/ready` 和 `/metrics` 不对用户网络公开。
- 公网 HTTPS 需要正式域名、受信任证书和 80/443；当前项目仍只把 Linux Compose 定义为生产支持拓扑。
- multi-user 浏览器使用 HttpOnly Cookie，代理必须保持同源并转发 Cookie、Origin 和常规代理头。

## Caddy 示例

```caddyfile
example.internal {
    root * D:/software/PythonProject1/PythonProject/rag/frontend/dist

    @internal path /api/live /api/ready /metrics
    respond @internal 404

    @api path /api/*
    reverse_proxy @api 127.0.0.1:8000 {
        flush_interval -1
    }

    try_files {path} /index.html
    file_server
}
```

`flush_interval -1` 用于保持 SSE 及时输出。Caddy 在域名、DNS 和端口满足条件时可管理证书。

## Nginx 要点

```nginx
server {
    listen 443 ssl;
    server_name example.internal;
    root D:/software/PythonProject1/PythonProject/rag/frontend/dist;

    location = /api/live { return 404; }
    location = /api/ready { return 404; }
    location = /metrics { return 404; }

    location /api/ {
        proxy_pass http://127.0.0.1:8000;
        proxy_http_version 1.1;
        proxy_buffering off;
        proxy_request_buffering off;
        proxy_read_timeout 300s;
        proxy_send_timeout 300s;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
    }

    location / { try_files $uri $uri/ /index.html; }
}
```

单用户客户端自行发送 `X-API-Key`，代理不应写死或注入秘密。multi-user 浏览器 Cookie 会由标准反向代理头转发，生产写请求还要满足 `PUBLIC_BASE_URL` 的 Origin 校验。

## 验证和回滚

验证 HTTPS、SSE、上传大小、登录 Cookie、公网探针隔离和一次完整问答。回滚只恢复上一份代理配置和前端制品；不要删除 `data/`、证书或用户库。
