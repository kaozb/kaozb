#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
清理当前 GitHub 用户全部仓库中「昨天及之前」的 Actions workflow run 与 deployment 记录。

保留策略（默认）：
  RETENTION_DAYS=0  => 只保留「今天（UTC）」的记录，删除「昨天及之前」的全部记录。

用法：
  python3 cleanup.py [--days N] [--dry-run] [--repos a/b,c/d]

环境变量：
  GITHUB_TOKEN / TOKEN   必需的令牌（需要 repo + workflow 权限）
  RETENTION_DAYS         保留天数，默认 0
  CONCURRENCY            并发删除数，默认 8
  DRY_RUN                设为 1 等价于 --dry-run
  INCLUDE_FORKS          设为 1 时同时处理 fork 仓库，默认跳过
  INCLUDE_ARCHIVED       设为 1 时同时处理已归档仓库，默认跳过
"""

import argparse
import concurrent.futures
import datetime as dt
import json
import os
import sys
import threading
import time
import urllib.error
import urllib.parse
import urllib.request

API = "https://api.github.com"
UA = "kaozb-cleanup-bot/1.0"

_lock = threading.Lock()
_stats = {"runs": 0, "deployments": 0, "failed": 0}


def log(msg):
    with _lock:
        print(f"[{dt.datetime.utcnow().strftime('%H:%M:%S')}] {msg}", flush=True)


class Github:
    def __init__(self, token):
        self.token = token

    def request(self, method, path, params=None, retries=4, body=None):
        url = API + path
        if params:
            url += "?" + urllib.parse.urlencode(params)
        data = json.dumps(body).encode() if body is not None else None
        req = urllib.request.Request(url, method=method, data=data)
        req.add_header("Authorization", f"token {self.token}")
        req.add_header("Accept", "application/vnd.github+json")
        req.add_header("X-GitHub-Api-Version", "2022-11-28")
        req.add_header("User-Agent", UA)
        if data is not None:
            req.add_header("Content-Type", "application/json")

        last_err = None
        for attempt in range(retries):
            try:
                with urllib.request.urlopen(req, timeout=60) as resp:
                    body = resp.read()
                    return resp.status, (json.loads(body) if body else None), resp.headers
            except urllib.error.HTTPError as e:
                code = e.code
                raw = e.read()
                # 404/410 视为正常（资源不存在/已删除）
                if code in (404, 410):
                    return code, None, e.headers
                # 403 可能是限流，读取 retry-after
                if code == 403 and attempt < retries - 1:
                    ra = e.headers.get("Retry-After")
                    wait = int(ra) if ra and ra.isdigit() else 5 * (attempt + 1)
                    log(f"HTTP 403 限流，{wait}s 后重试 {method} {path}")
                    time.sleep(wait)
                    last_err = e
                    continue
                if code >= 500 and attempt < retries - 1:
                    time.sleep(2 * (attempt + 1))
                    last_err = e
                    continue
                try:
                    msg = json.loads(raw).get("message", "")
                except Exception:
                    msg = raw[:200].decode("utf-8", "replace")
                return code, {"message": msg}, e.headers
            except Exception as e:  # 网络类错误
                last_err = e
                if attempt < retries - 1:
                    time.sleep(2 * (attempt + 1))
                    continue
                raise
        raise last_err

    def paginate(self, path, params=None, limit=100, max_pages=1000):
        """逐页返回列表结果；命中 limit（默认 100）时继续翻页。"""
        page = 1
        while page <= max_pages:
            p = dict(params or {})
            p["per_page"] = limit
            p["page"] = page
            code, data, _ = self.request("GET", path, p)
            if code != 200 or not isinstance(data, list):
                if code not in (200, 404):
                    log(f"列取失败 {path} page={page} HTTP {code} {data}")
                return
            yield from data
            if len(data) < limit:
                return
            page += 1


def list_repos(gh, include_forks, include_archived):
    """当前用户（owner）名下的全部仓库。"""
    out = []
    for r in gh.paginate("/user/repos", {"affiliation": "owner", "sort": "full_name"}):
        if r.get("fork") and not include_forks:
            continue
        if r.get("archived") and not include_archived:
            continue
        if r.get("disabled"):
            continue
        out.append(r["full_name"])
    return out


def delete_runs(gh, repo, cutoff_iso, dry_run, executor):
    """删除 repo 中 created < cutoff 的全部 workflow run。"""
    deleted = 0
    while True:
        # created=<date 由服务端过滤，只会返回「昨天及之前」的记录
        code, data, _ = gh.request(
            "GET", f"/repos/{repo}/actions/runs",
            {"per_page": 100, "page": 1, "created": f"<{cutoff_iso}"},
        )
        if code != 200:
            if code not in (404, 410):
                log(f"[{repo}] runs 列取失败 HTTP {code} {data}")
            return deleted
        runs = (data or {}).get("workflow_runs", [])
        if not runs:
            return deleted
        # 双重保险：本地再按 created_at 过滤一次
        ids = [r["id"] for r in runs if r.get("created_at", "") < cutoff_iso]
        if not ids:
            return deleted

        if dry_run:
            deleted += len(ids)
            log(f"[{repo}] [dry-run] 将删除 {len(ids)} 条 run")
            return deleted

        def do_rm(run_id):
            c, _, _ = gh.request("DELETE", f"/repos/{repo}/actions/runs/{run_id}")
            if c not in (204, 404):
                with _lock:
                    _stats["failed"] += 1
                log(f"[{repo}] run {run_id} 删除失败 HTTP {c}")
                return 0
            with _lock:
                _stats["runs"] += 1
            return 1

        got = sum(executor.map(do_rm, ids))
        deleted += got
        log(f"[{repo}] 本轮删除 {got}/{len(ids)} 条 run，累计 {deleted}")
        if got == 0:
            # 全都删不掉，避免死循环
            return deleted
    return deleted


def delete_deployments(gh, repo, cutoff_iso, dry_run, executor):
    """删除 repo 中 created_at < cutoff 的全部 deployment 记录。"""
    deleted = 0
    while True:
        code, data, _ = gh.request(
            "GET", f"/repos/{repo}/deployments",
            {"per_page": 100, "page": 1, "sort": "created", "direction": "asc"},
        )
        if code != 200:
            if code not in (404, 410):
                log(f"[{repo}] deployments 列取失败 HTTP {code} {data}")
            return deleted
        deps = (data or [])
        if not deps:
            return deleted
        old = [d["id"] for d in deps if d.get("created_at", "") < cutoff_iso]
        if not old:
            # 最早的记录都在保留期内，无需继续翻页
            return deleted

        if dry_run:
            deleted += len(old)
            log(f"[{repo}] [dry-run] 将删除 {len(old)} 条 deployment")
            return deleted

        def do_rm(dep_id):
            # GitHub 不允许删除「处于 active」的 deployment（除非它是该 environment 的唯一一条），
            # 因此先追加一条 state=inactive 的状态，使其不再 active，再执行删除。
            c, body, _ = gh.request("DELETE", f"/repos/{repo}/deployments/{dep_id}")
            if c in (204, 404):
                with _lock:
                    _stats["deployments"] += 1
                return 1
            # 先置为 inactive 再删
            cs, _, _ = gh.request(
                "POST", f"/repos/{repo}/deployments/{dep_id}/statuses",
                None, body={"state": "inactive"},
            )
            if cs in (200, 201):
                c2, body2, _ = gh.request("DELETE", f"/repos/{repo}/deployments/{dep_id}")
                if c2 in (204, 404):
                    with _lock:
                        _stats["deployments"] += 1
                    return 1
                body = body2
            with _lock:
                _stats["failed"] += 1
            msg = (body or {}).get("message", "")
            errs = (body or {}).get("errors")
            log(f"[{repo}] deployment {dep_id} 删除失败 HTTP {c} {msg} {errs or ''}".rstrip())
            return 0

        got = sum(executor.map(do_rm, old))
        deleted += got
        log(f"[{repo}] 本轮删除 {got}/{len(old)} 条 deployment，累计 {deleted}")
        if got == 0:
            return deleted
    return deleted


def main():
    ap = argparse.ArgumentParser(description="清理昨天及之前的 Actions run 与 deployment")
    ap.add_argument("--days", type=int, default=None,
                    help="保留天数，默认取环境变量 RETENTION_DAYS 或 0")
    ap.add_argument("--dry-run", action="store_true", help="只统计不删除")
    ap.add_argument("--repos", default=None, help="只处理指定仓库，逗号分隔，如 a/b,c/d")
    args = ap.parse_args()

    token = os.environ.get("GITHUB_TOKEN") or os.environ.get("TOKEN")
    if not token:
        log("缺少令牌：请设置 GITHUB_TOKEN 或 TOKEN")
        return 2

    days = args.days if args.days is not None else int(os.environ.get("RETENTION_DAYS", "0"))
    concurrency = int(os.environ.get("CONCURRENCY", "8"))
    dry_run = args.dry_run or os.environ.get("DRY_RUN") == "1"
    include_forks = os.environ.get("INCLUDE_FORKS") == "1"
    include_archived = os.environ.get("INCLUDE_ARCHIVED") == "1"

    # cutoff = 今天(UTC) 00:00 减去 days 天；删除 created < cutoff 的记录
    today = dt.datetime.now(dt.timezone.utc).replace(
        hour=0, minute=0, second=0, microsecond=0)
    cutoff = today - dt.timedelta(days=days)
    cutoff_iso = cutoff.strftime("%Y-%m-%dT%H:%M:%SZ")

    gh = Github(token)
    code, me, _ = gh.request("GET", "/user")
    if code != 200:
        log(f"令牌校验失败 HTTP {code} {me}")
        return 2
    login = me["login"]

    if args.repos:
        repos = [r.strip() for r in args.repos.split(",") if r.strip()]
    else:
        repos = list_repos(gh, include_forks, include_archived)

    log(f"用户 {login}｜保留最近 {days} 天（删除 created < {cutoff_iso}）"
        f"｜仓库 {len(repos)} 个｜并发 {concurrency}"
        + ("｜dry-run" if dry_run else ""))

    with concurrent.futures.ThreadPoolExecutor(max_workers=concurrency) as ex:
        for repo in repos:
            try:
                r = delete_runs(gh, repo, cutoff_iso, dry_run, ex)
                d = delete_deployments(gh, repo, cutoff_iso, dry_run, ex)
                if r or d:
                    log(f"[{repo}] 完成：run {r} 条，deployment {d} 条")
            except Exception as e:
                log(f"[{repo}] 异常：{e}")
                with _lock:
                    _stats["failed"] += 1

    log(f"总计：run {_stats['runs']} 条，deployment {_stats['deployments']} 条，失败 {_stats['failed']}")
    return 1 if _stats["failed"] else 0


if __name__ == "__main__":
    sys.exit(main())
