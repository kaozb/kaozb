#!/usr/bin/env bash
# 把组织下的全部 fork 同步到各自上游（方案 B：使用 GitHub 官方「Sync fork」接口，禁止强推）
# 走 POST /repos/{owner}/{repo}/merge-upstream，服务端只在可 fast-forward 时生效；
# 若 fork 有本地提交导致分叉，接口返回 409，绝不强推、绝不丢历史。
# 需要：ORG、TOKEN 环境变量；token 需对目标仓库有 contents:write（classic token 的 repo 权限即可）
# 可选：DRY_RUN=1 只打印计划不执行
set -uo pipefail

ORG="${ORG:-kaozbf}"
: "${TOKEN:?需要设置 TOKEN 环境变量}"
DRY_RUN="${DRY_RUN:-0}"

API="https://api.github.com"

api() { curl -sS -H "Authorization: token ${TOKEN}" -H "Accept: application/vnd.github+json" "$@"; }

echo "== 获取 ${ORG} 下全部仓库（含 fork 标记）=="
repos_json="$(api "${API}/orgs/${ORG}/repos?per_page=100&type=all")"

mapfile -t FORKS < <(printf '%s' "$repos_json" | python3 -c '
import sys, json
d = json.load(sys.stdin)
for r in sorted(d, key=lambda x: x["name"].lower()):
    if r.get("fork"):
        print(r["full_name"])
')

if [ "${#FORKS[@]}" -eq 0 ]; then
  echo "没有找到 fork 仓库"
  exit 0
fi
echo "共 ${#FORKS[@]} 个 fork"

ok=0; fail=0; skip=0; up_to_date=0
for full in "${FORKS[@]}"; do
  name="${full##*/}"
  echo "----------------------------------------"
  echo ">> ${full}"

  detail="$(api "${API}/repos/${full}")"
  parent="$(printf '%s' "$detail" | python3 -c 'import sys,json;d=json.load(sys.stdin);p=d.get("parent") or {};print(p.get("full_name",""))')"
  pdef="$(printf '%s' "$detail" | python3 -c 'import sys,json;d=json.load(sys.stdin);p=d.get("parent") or {};print(p.get("default_branch",""))')"
  fdef="$(printf '%s' "$detail" | python3 -c 'import sys,json;print(json.load(sys.stdin).get("default_branch",""))')"

  if [ -z "$parent" ] || [ -z "$pdef" ]; then
    echo "  跳过：拿不到上游信息"
    skip=$((skip+1)); continue
  fi
  echo "  上游：${parent}  默认分支：${fdef} -> ${pdef}"

  if [ "$DRY_RUN" = "1" ]; then
    echo "  [dry-run] 将调用 POST ${API}/repos/${full}/merge-upstream  branch=${fdef}（不强推）"
    skip=$((skip+1)); continue
  fi

  # 官方「Sync fork」接口：只在可 fast-forward 时生效，不会强推、不会丢历史
  resp="$(api -w '\n%{http_code}' -X POST \
    -d "$(python3 -c 'import json,sys;print(json.dumps({"branch": sys.argv[1]}))' "$fdef")" \
    "${API}/repos/${full}/merge-upstream")"
  code="$(printf '%s' "$resp" | tail -n1)"
  body="$(printf '%s' "$resp" | sed '$d')"
  msg="$(printf '%s' "$body" | python3 -c 'import sys,json;
try:
    d=json.load(sys.stdin); print(d.get("message") or d.get("merge_type") or "")
except Exception:
    print("")')"

  case "$code" in
    200)
      echo "  已同步（fast-forward）：${msg}"
      ok=$((ok+1)) ;;
    409)
      # 有本地提交导致分叉，服务端拒绝快进；不强推，跳过即可
      echo "  跳过：存在本地提交无法快进（${msg:-conflict}），未做任何改动"
      up_to_date=$((up_to_date+1)) ;;
    422)
      echo "  跳过：该分支无法直接同步（${msg:-unprocessable}）"
      skip=$((skip+1)) ;;
    *)
      echo "  失败：HTTP ${code} ${msg}"
      fail=$((fail+1)) ;;
  esac
done

echo "========================================"
echo "完成：成功 ${ok}  分叉跳过 ${up_to_date}  其他跳过 ${skip}  失败 ${fail}"
[ "$fail" -eq 0 ]
