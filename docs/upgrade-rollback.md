# 升级与回滚手册

> 文档版本：`0.1`
> 适用应用版本：`0.1.0`
> 最近核对：`2026-08-21`

当前系统是单实例、有状态部署，不支持无停机滚动升级。升级窗口必须允许停止写入和短时不可用。

## 发布输入

发布负责人必须准备：

- 目标 Git 提交和应用版本；
- `CHANGELOG.md` 中的升级影响；
- 当前运行提交或可重建的上一镜像；
- 最近一次已验证备份；
- 数据、配置和依赖兼容性结论；
- 发布和回滚操作人；
- 验收结果记录位置。

Compose 文件当前使用 `latest` 标签，不能单靠该标签回滚。发布前必须给当前和目标镜像增加不可变标签。

## 发布前记录与备份

```bash
git rev-parse HEAD
docker compose images
docker image inspect enterprise-rag-backend:latest --format '{{.Id}}'
docker image inspect enterprise-rag-nginx:latest --format '{{.Id}}'

export RELEASE_ID="0.1.0-$(git rev-parse --short HEAD)"
docker tag enterprise-rag-backend:latest "enterprise-rag-backend:pre-$RELEASE_ID"
docker tag enterprise-rag-nginx:latest "enterprise-rag-nginx:pre-$RELEASE_ID"
```

停止 backend 后创建离线备份：

```bash
docker compose stop backend
docker compose run --rm --no-deps backend \
  python scripts/backup.py create --confirm-stopped
docker compose start backend
```

记录新备份目录，并执行 `backup.py verify`。若备份或校验失败，取消发布。

## 升级前验证

在目标提交执行：

```bash
docker compose config --quiet
docker compose --profile monitoring config --quiet
docker compose build
npx --yes pyright@1.1.311
uv run pytest tests -q
```

前端验证：

```bash
cd frontend
npm ci
npm run lint
npm run test
npm run build
cd ..
```

如果目标主机不承担源码测试，必须引用同一提交的 CI 证据。不能引用其他提交的通过结果。

## 升级

```bash
docker compose stop backend nginx
docker compose up -d --no-deps backend
docker compose ps
```

backend healthy 后再启动 Nginx：

```bash
docker compose up -d --no-deps nginx
docker compose --profile monitoring up -d
```

执行 [部署后验收](deployment-runbook.md#8-发布后验收)。

## 回滚触发条件

出现任一情况立即回滚，不在生产现场继续试错：

- backend 无法在发布窗口内变为 healthy；
- 生产安全门控失败且不是已知配置遗漏；
- 登录、上传、检索、来源或文档管理主流程失败；
- manifest 不一致、Chroma 无法读取或数据数量异常；
- 监控和通知链路失效；
- 新版本产生持续错误或不可接受延迟；
- 文档/API/配置事实与实际行为冲突，影响安全或数据正确性。

## 代码和镜像回滚

停止入口和后端：

```bash
docker compose stop nginx backend
```

将 `enterprise-rag-backend:latest` 和 `enterprise-rag-nginx:latest` 重新标记为发布前保存的镜像：

```bash
docker tag "enterprise-rag-backend:pre-$RELEASE_ID" enterprise-rag-backend:latest
docker tag "enterprise-rag-nginx:pre-$RELEASE_ID" enterprise-rag-nginx:latest
docker compose up -d backend nginx
```

若 Compose 或配置文件也变化，先恢复上一提交中的 `Dockerfile`、`docker-compose.yml`、`nginx.conf` 和兼容 `.env`，再启动。不得使用 `git reset --hard` 处理带有未保存工作的生产目录；发布应从受控工作树或制品执行。

## 数据回滚

只有升级确实修改或损坏运行数据时才恢复数据。普通代码回滚不应顺带覆盖生产数据。

```bash
docker compose stop backend
docker compose run --rm --no-deps backend \
  python scripts/backup.py restore <backup-path> \
  --confirm-stopped --confirm-replace
docker compose start backend
```

恢复命令会先校验备份，并把当前数据目录保留为 `data.pre-restore-<timestamp>`。恢复后必须检查 readiness、文档数量、一次已知检索和登录状态。

## 回滚完成条件

- 上一版本主页、登录和主业务流程恢复；
- `/api/ready` 为 200；
- 数据数量和抽样检索符合回滚前记录；
- 监控和通知恢复；
- 事故记录包含时间线、影响、触发条件、使用的镜像和备份、验证结果；
- 在确认根因前暂停再次发布。
