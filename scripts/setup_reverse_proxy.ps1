param(
    [string]$OutputDir = ".\reverse-proxy",
    [ValidateSet("caddy", "nginx", "both")]
    [string]$Mode = "both",
    [string]$HostName = "example.internal",
    [string]$FrontendDist = ".\frontend\dist"
)

$ErrorActionPreference = "Stop"
$output = [IO.Path]::GetFullPath($OutputDir)
$dist = [IO.Path]::GetFullPath($FrontendDist)
New-Item -ItemType Directory -Force -Path $output | Out-Null
$distUnix = $dist.Replace('\','/')

$caddy = @"
$HostName {
    root * $distUnix
    @probe path /api/live /api/ready /metrics
    respond @probe 404
    @api path /api/*
    reverse_proxy @api 127.0.0.1:8000 { flush_interval -1 }
    try_files {path} /index.html
    file_server
}
"@
$nginx = @"
server {
    listen 443 ssl;
    server_name $HostName;
    root $distUnix;
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
"@
if ($Mode -in @('caddy', 'both')) { Set-Content -LiteralPath (Join-Path $output 'Caddyfile') -Value $caddy -Encoding UTF8 }
if ($Mode -in @('nginx', 'both')) { Set-Content -LiteralPath (Join-Path $output 'nginx.conf') -Value $nginx -Encoding UTF8 }
Set-Content -LiteralPath (Join-Path $output 'CHECKLIST.txt') -Value @(
    'Generated templates only. No service, certificate, firewall, DNS, or task changes were made.',
    'Set strong APP_PASSWORD and separate METRICS_TOKEN before exposure.',
    'Keep FastAPI on 127.0.0.1:8000 and one worker.',
    'Configure certificates and HTTPS before opening 443.',
    "Frontend dist: $dist"
) -Encoding UTF8
Write-Output "Generated reverse-proxy templates in $output"
