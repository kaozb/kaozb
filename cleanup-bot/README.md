# cleanup-bot

自动清理当前 GitHub 用户（kaozb）名下**全部仓库**中「昨天及之前」的
GitHub Actions workflow run 与 deployment 记录。

## 保留策略

- 默认 `RETENTION_DAYS=0`：只保留**今天（UTC）**产生的记录，
  删除「昨天及之前」的全部 run 与 deployment。
- 可通过 workflow_dispatch 输入的 `days` 调整保留天数。

## 运行方式

- 定时：每天 `18:00 UTC`（北京时间次日 02:00）自动执行。
- 手动：Actions → Cleanup old runs & deployments → Run workflow，
  可填 `days`（保留天数）与 `dry_run`（true = 只统计不删除）。

## 认证

workflow 使用 `environment: action`，从中读取名为 `TOKEN` 的 secret。
该 secret 必须是具备 `repo` + `workflow` 权限的 PAT（classic），
否则无法跨仓库删除其它仓库的 run 记录（自动的 `GITHUB_TOKEN` 只对当前仓库有效）。

在仓库 Settings → Environments → `action` → Environment secrets
添加 secret `TOKEN` 即可。

## 实现要点

- 仓库清单来自 `GET /user/repos?affiliation=owner`，与本地是否存在副本无关；
  默认跳过 fork 与 archived 仓库（可用 `INCLUDE_FORKS=1` / `INCLUDE_ARCHIVED=1` 打开）。
- run 用服务端过滤 `?created=<cutoff` 只取旧记录，本地再按 `created_at` 兜底过滤。
- deployment 删除时 GitHub 不允许删除仍处于 active 的 deployment，
  脚本先追加一条 `state=inactive` 的状态再删除。
- 并发删除，默认 `CONCURRENCY=8`；404/410 视为已删除，403 限流自动退避重试。

## 本地调试

```bash
export TOKEN=<你的 PAT>
python3 cleanup.py --dry-run            # 全量仓库，只统计
python3 cleanup.py --repos kaozb/zero    # 只处理指定仓库
python3 cleanup.py --days 3              # 保留最近 3 天
```

## 环境变量

| 变量 | 默认 | 说明 |
| --- | --- | --- |
| `GITHUB_TOKEN` / `TOKEN` | 无 | 必需的令牌 |
| `RETENTION_DAYS` | `0` | 保留天数，0 = 只保留今天 |
| `CONCURRENCY` | `8` | 并发删除数 |
| `DRY_RUN` | 空 | 设为 `1` 等价于 `--dry-run` |
| `INCLUDE_FORKS` | 空 | 设为 `1` 时包含 fork 仓库 |
| `INCLUDE_ARCHIVED` | 空 | 设为 `1` 时包含归档仓库 |
