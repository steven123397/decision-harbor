#!/usr/bin/env bash
# 校验 GitHub Issues 的结构一致性：正文 "## Blocked by" 声明与原生 blocked-by 边
# 双向一致，sub-issue 的 Parent 必须真实挂载。/to-tickets、/wayfinder 发布后运行本脚本。
# 用法：scripts/check-tracker.sh [issue 编号...]（缺省检查全部 open issue）
set -uo pipefail

REPO=$(gh repo view --json nameWithOwner --jq .nameWithOwner)

retry() {
  local out
  for _ in 1 2 3 4 5; do
    out=$("$@" 2>/dev/null) && { printf '%s' "$out"; return 0; }
    sleep 2
  done
  return 1
}

issues=("$@")
if [ ${#issues[@]} -eq 0 ]; then
  mapfile -t issues < <(retry gh issue list --repo "$REPO" --state open --limit 200 --json number --jq '.[].number' || true)
fi
[ ${#issues[@]} -eq 0 ] && { echo "no issues to check"; exit 0; }

# 正文 "Blocked by" 段声明的编号 vs 原生 blocked_by 边
declare -A DECL NATIVE
fail=0
for n in "${issues[@]}"; do
  body=$(retry gh issue view "$n" --repo "$REPO" --json body --jq .body) || { echo "ERR: 无法读取 #$n"; fail=1; continue; }
  # 提取 "## Blocked by" 段内的 #编号
  mapfile -t decl < <(awk '/^## Blocked by/{f=1;next} /^## /{f=0} f' <<<"$body" | grep -oE '#[0-9]+' | tr -d '#' | sort -u)
  DECL[$n]=$(IFS=,; echo "${decl[*]:-}")
  # 原生边（响应条目是完整 issue 对象，编号字段是 .number）
  edges=$(retry gh api "repos/$REPO/issues/$n/dependencies/blocked_by" --jq '[.[].number] | sort | join(",")')
  NATIVE[$n]=${edges:-}
done

echo "issue | 正文声明 | 原生边 | 判定"
for n in "${issues[@]}"; do
  d=${DECL[$n]:-}; v=${NATIVE[$n]:-}
  # 归一比较：声明的每条边都应出现在原生边里
  ok=yes
  [ -z "$d" ] && [ -z "$v" ] && { echo "#$n | - | - | OK"; continue; }
  if [ -n "$d" ]; then
    for x in ${d//,/ }; do
      [ -n "$x" ] || continue
      [[ ",$v," == *",$x,"* ]] || ok="缺边 #$n←#$x"
    done
  fi
  # 反向逐项比对：每条原生边都应在正文声明，否则正文已过期（信息漂移）
  if [ -n "$v" ]; then
    for y in ${v//,/ }; do
      [ -n "$y" ] || continue
      [[ ",$d," == *",$y,"* ]] || ok="$ok; 正文缺 #$y"
    done
  fi
  echo "#$n | ${d:--} | ${v:--} | $ok"
  [ "$ok" != yes ] && fail=1
done

# Parent 声明 vs sub-issue 挂载
echo
echo "sub-issue 挂载核对："
for n in "${issues[@]}"; do
  body=$(retry gh issue view "$n" --repo "$REPO" --json body --jq .body) || continue
  parent=$(awk '/^## Parent/{f=1;next} /^## /{f=0} f' <<<"$body" | grep -oE '#[0-9]+' | head -1 | tr -d '#')
  [ -z "$parent" ] && continue
  subs=$(retry gh api "repos/$REPO/issues/$parent/sub_issues" --jq '[.[].number] | index('"$n"') != null')
  [ "$subs" = true ] && echo "#$n → parent #$parent : OK" || { echo "#$n → parent #$parent : 未挂载"; fail=1; }
done

exit $fail
