#!/usr/bin/env python3
"""自动生成 GitHub 主页 README（kaozb/kaozb）。

数据来源：GitHub REST API。输出 README.md，含：
  - 动态徽章（shields.io，浏览器端实时取数）
  - 活跃状态卡片（streak-stats）
  - 组织 kaozbf 的仓库卡片墙（每个仓库一张卡片：仓库链接 + 仓库自己的描述 + 语言）

在 GitHub Actions 中由仓库自带的 GITHUB_TOKEN 调用；本地运行需设置 GITHUB_TOKEN。
"""
import html
import json
import os
import urllib.request

USER = "kaozb"
ORG = "kaozbf"
TOKEN = os.environ.get("GITHUB_TOKEN", "")

# GitHub 语言官方配色（用不到的会回落到灰色）
LANG_COLORS = {
    "Python": "3572A5", "JavaScript": "f1e05a", "TypeScript": "3178c6",
    "Go": "00ADD8", "Rust": "dea584", "Java": "b07219", "C": "555555",
    "C++": "f34b7d", "C#": "178600", "Shell": "89e051", "HTML": "e34c26",
    "CSS": "563d7c", "Vue": "41b883", "PHP": "4F5D95", "Ruby": "701516",
    "Kotlin": "A97BFF", "Swift": "F05138", "Dart": "00B4AB", "Dockerfile": "384d54",
    "Lua": "000080", "Jupyter Notebook": "DA5B0B", "MDX": "fcb32c",
}


def api(url):
    req = urllib.request.Request(url, headers={
        "Accept": "application/vnd.github+json",
        "User-Agent": "kaozb-profile-bot",
        **({"Authorization": f"Bearer {TOKEN}"} if TOKEN else {}),
    })
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.load(r)


def fetch_org_repos():
    """拉取组织仓库，并逐仓库补全语言/续页信息。"""
    repos = api(f"https://api.github.com/orgs/{ORG}/repos?per_page=100&sort=updated")
    out = []
    for r in repos:
        name = r["name"]
        # 列表接口不返回 language，且部分字段可能被裁剪，逐仓库取详情
        try:
            d = api(f"https://api.github.com/repos/{ORG}/{name}")
        except Exception:
            d = r
        out.append({
            "name": name,
            "desc": (d.get("description") or r.get("description") or "").strip(),
            "lang": d.get("language") or "",
            "url": d.get("html_url") or f"https://github.com/{ORG}/{name}",
            "stars": d.get("stargazers_count", 0),
            "forks": d.get("forks_count", 0),
        })
    out.sort(key=lambda x: x["name"].lower())
    return out


def card(repo):
    """生成单个仓库卡片（HTML，用于 README 卡片墙）。"""
    name = html.escape(repo["name"])
    url = repo["url"]
    desc = html.escape(repo["desc"]) or "<i>（暂无描述）</i>"
    lang = repo["lang"]
    lang_badge = ""
    if lang:
        color = LANG_COLORS.get(lang, "6e7681")
        lang_badge = (
            f'<img src="https://img.shields.io/badge/{html.escape(lang).replace(" ", "%20")}'
            f'-{color}?style=flat-square&logoColor=white" alt="{html.escape(lang)}" height="16">'
        )
    return (
        "<td width=\"50%\" valign=\"top\">\n\n"
        f"**[{name}]({url})**<br>\n"
        f"<sub>{desc}</sub><br>\n"
        f"{lang_badge}\n\n"
        "</td>"
    )


def render(repos):
    lines = []
    lines.append(f"# {USER}")
    lines.append("")
    lines.append("<p>")
    lines.append(f'  <a href="https://github.com/{USER}?tab=followers"><img src="https://img.shields.io/github/followers/{USER}?label=Followers&style=flat-square&color=1f6feb" alt="followers"></a>')
    lines.append(f'  <a href="https://github.com/{USER}?tab=repositories"><img src="https://img.shields.io/badge/dynamic/json?url=https%3A%2F%2Fapi.github.com%2Fusers%2F{USER}&query=%24.public_repos&label=Repos&style=flat-square&color=2ea043" alt="repos"></a>')
    lines.append(f'  <a href="https://github.com/{ORG}"><img src="https://img.shields.io/badge/dynamic/json?url=https%3A%2F%2Fapi.github.com%2Forgs%2F{ORG}&query=%24.public_repos&label={ORG}%20repos&style=flat-square&color=8957e5" alt="org repos"></a>')
    lines.append(f'  <img src="https://img.shields.io/badge/dynamic/json?url=https%3A%2F%2Fapi.github.com%2Fusers%2F{USER}&query=%24.created_at&label=Joined&style=flat-square&color=6e7681" alt="joined">')
    lines.append("</p>")
    lines.append("")
    lines.append("## 📈 活跃状态")
    lines.append("")
    lines.append("<p>")
    lines.append(f'  <img src="https://github-readme-streak-stats.herokuapp.com/?user={USER}&hide_border=true" alt="streak stats">')
    lines.append("</p>")
    lines.append("")
    lines.append(f"## 🏢 组织 {ORG}")
    lines.append("")
    lines.append("<p>")
    lines.append(f'  <a href="https://github.com/{ORG}"><img src="https://img.shields.io/badge/dynamic/json?url=https%3A%2F%2Fapi.github.com%2Forgs%2F{ORG}&query=%24.public_repos&label=Public%20Repos&style=for-the-badge&color=1f6feb" alt="public repos"></a>')
    lines.append(f'  <img src="https://img.shields.io/badge/dynamic/json?url=https%3A%2F%2Fapi.github.com%2Forgs%2F{ORG}&query=%24.created_at&label=Created&style=for-the-badge&color=2ea043" alt="created">')
    lines.append(f'  <img src="https://img.shields.io/badge/dynamic/json?url=https%3A%2F%2Fapi.github.com%2Forgs%2F{ORG}&query=%24.followers&label=Followers&style=for-the-badge&color=8957e5" alt="followers">')
    lines.append("</p>")
    lines.append("")
    lines.append(f"共收录 **{len(repos)}** 个仓库（由 GitHub Actions 定时自动更新）：")
    lines.append("")
    # 卡片墙：每行两张卡片
    for i in range(0, len(repos), 2):
        pair = repos[i:i + 2]
        cells = [card(r) for r in pair]
        if len(cells) == 1:
            cells.append("<td width=\"50%\" valign=\"top\"></td>")
        lines.append("<table>")
        lines.append("<tr>")
        lines.append(" ".join(cells))
        lines.append("</tr>")
        lines.append("</table>")
        lines.append("")
    lines.append("<!-- AUTO-GENERATED by update-readme.yml，请勿手动编辑本文件 -->")
    lines.append("")
    return "\n".join(lines)


def main():
    repos = fetch_org_repos()
    content = render(repos)
    with open("README.md", "w", encoding="utf-8") as f:
        f.write(content)
    print(f"README.md 已生成，共 {len(repos)} 个仓库")


if __name__ == "__main__":
    main()
