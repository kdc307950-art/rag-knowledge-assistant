# Reverse Proxy Deployment

Supported topology: browser -> HTTPS reverse proxy -> `127.0.0.1:8000`. Serve
`frontend/dist` from the proxy so the browser remains same-origin. Keep
FastAPI on one worker.

## Preflight

- Set a long random `APP_PASSWORD` and a separate `METRICS_TOKEN`.
- Keep `/api/live`, `/api/ready`, and `/metrics` off the public internet.
- Allow only the proxy to reach TCP 8000.
- Public HTTPS requires a real domain, trusted certificate, and ports 80/443.
- Private deployments need a trusted enterprise CA or a client-trusted self-signed certificate.

## Caddy on Windows

```caddyfile
example.internal {
    root * D:/software/PythonProject1/PythonProject/rag/frontend/dist
    @probe path /api/live /api/ready /metrics
    respond @probe 404
    @api path /api/*
    reverse_proxy @api 127.0.0.1:8000 {
        flush_interval -1
    }
    try_files {path} /index.html
    file_server
}
```

`flush_interval -1` preserves SSE delivery. Caddy can manage public
certificates when DNS and ports are correctly configured. The proxy forwards
`X-API-Key`; backend authentication remains authoritative.

## Nginx essentials

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
        proxy_read_timeout 1h;
        proxy_set_header X-API-Key $http_x_api_key;
        proxy_set_header Host $host;
        proxy_set_header X-Forwarded-Proto $scheme;
    }
    location / { try_files $uri $uri/ /index.html; }
}
```

Add a separate port-80 HTTP-to-HTTPS redirect. `scripts/health_check.py` calls
loopback `/api/ready` directly, so proxy failure and application failure remain
distinguishable. Rollback is restoring the previous proxy configuration; do
not delete `data/` or certificates.
