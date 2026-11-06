#!/usr/bin/env python3
"""按 skill_folder_map.yaml 在产品根生成板块夹 + 与插件同名的 Skill 子夹。"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

# 与 templates/skill_folder_map.yaml 保持一致。夹名 = 插件 skills/ 目录名。
STAGES: list[dict] = [
    {
        "official": "S1: 选品板块",
        "dir": "S1_选品板块",
        "skills": [
            "jingpu-product-selection",
            "amazon-trend-signal-scanner",
            "amazon-market-research",
            "amazon-competition-difficulty-assessor",
            "amazon-gray-tactic-detector",
            "amazon-seasonal-selection",
            "alibaba-supplier-evaluation",
            "1688-product-detail-scraper",
            "amazon-ip-risk-assessment",
            "amazon-variant-packaging-planner",
            "amazon-product-development-prd",
            "github-tabler-html-report",
        ],
        "extra": [],
    },
    {
        "official": "S2: 文案板块",
        "dir": "S2_文案板块",
        "skills": [
            "amazon-sif-keyword-research",
            "amazon-sellersprite-keyword-research",
            "amazon-xiyou-keyword-research",
            "amazon-listing-copywriter",
            "github-tabler-html-report",
        ],
        "extra": [
            "_投喂_关键词导出/SIF",
            "_投喂_关键词导出/卖家精灵",
            "_投喂_关键词导出/西柚找词",
        ],
    },
    {
        "official": "S3: 视觉板块",
        "dir": "S3_视觉板块",
        "skills": [
            "amazon-visual-benchmark-learner",
            "amazon-listing-image-designer",
            "amazon-premium-aplus-designer",
            "amazon-standard-aplus-designer",
            "amazon-product-video-director",
            "github-tabler-html-report",
        ],
        "extra": [],
    },
    {
        "official": "S4: 广告板块",
        "dir": "S4_广告板块",
        "skills": [
            "amazon-ad-strategy-architect",
            "amazon-ad-campaign-builder",
            "amazon-bulk-upload",
            "amazon-bulk-upload-troubleshooter",
            "amazon-ad-report-analyzer",
            "amazon-launch-plan",
            "github-tabler-html-report",
        ],
        "extra": ["amazon-bulk-upload/bulk"],
    },
    {
        "official": "S5: 运营板块",
        "dir": "S5_运营板块",
        "skills": [
            "amazon-ops-workflow",
            "amazon-store-patrol-orchestrator",
            "lingxing-erp-connector",
            "github-tabler-html-report",
        ],
        "extra": [
            "_投喂_巡检报表/领星导出",
            "_投喂_巡检报表/后台报表",
        ],
    },
]

CONTROL = {
    "dir": "00_总控",
    "skills": [
        "starsky-amazon-agent",
        "starsky-fullchain-workflow",
        "starsky-singlepoint-intake",
        "category-adapter",
    ],
}


def write_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text.replace("\r\n", "\n").replace("\r", "\n"), encoding="utf-8")


def skill_readme(stage_official: str, skill: str) -> str:
    extra = ""
    if skill == "1688-product-detail-scraper":
        extra = (
            "- 抓页主路径是必装本地 MCP `1688_scraper`（插件 `mcp/1688/`）。\n"
            "- 本夹只收规范报告；不要把 MCP 源码或登录态拷进来。\n"
        )
    return (
        f"# {skill}\n\n"
        f"- 对应插件 Skill：`skills/{skill}/SKILL.md`\n"
        f"- 所属板块：{stage_official}\n"
        f"- 本 Skill 的报告、yaml、HTML 只写在这个夹里。\n"
        f"- 禁止写到产品根的 `生成报告/`，禁止写进其他 Skill 夹。\n"
        f"{extra}"
    )


def init_layout(root: Path) -> dict:
    created: list[dict] = []

    feed = root / "00_产品资料"
    feed.mkdir(parents=True, exist_ok=True)
    write_text(
        feed / "README.md",
        "# 00_产品资料\n\n企业投喂原件。不是 Skill 夹。拷完回对话：已完成\n"
        "不知道放哪就先建 `_未分类`（要建夹口令会生成子夹）。\n",
    )

    nav = root / "00_导航"
    nav.mkdir(parents=True, exist_ok=True)
    write_text(
        nav / "README.md",
        "# 00_导航\n\n`导航.html` 由星空导航自动生成（路线图 + 当前进度），离线可打开。\n不要手改本页；以聊天里的步骤卡与阶段报告为准。\n",
    )

    control = root / CONTROL["dir"]
    control.mkdir(parents=True, exist_ok=True)
    for skill in CONTROL["skills"]:
        d = control / skill
        d.mkdir(parents=True, exist_ok=True)
        write_text(d / "README.md", skill_readme("00_总控", skill))
        created.append({"stage": "00_总控", "skill": skill, "path": str(d)})
    write_text(
        control / "README.md",
        "# 00_总控\n\n00A / 00C / Overlay / 选品推荐写在对应 Skill 子夹。\n"
        "`workflow_state.yaml` 和 `_NEXT_*.md` 仍放在产品根，方便换窗接力。\n",
    )

    for stage in STAGES:
        stage_dir = root / stage["dir"]
        stage_dir.mkdir(parents=True, exist_ok=True)
        overview = stage_dir / "_阶段总览"
        overview.mkdir(parents=True, exist_ok=True)
        write_text(
            overview / "README.md",
            f"# {stage['official']} · 阶段总览\n\n"
            f"本板块综合报告写这里。各 Skill 的分报告写在同级的 Skill 夹。\n",
        )
        for skill in stage["skills"]:
            d = stage_dir / skill
            d.mkdir(parents=True, exist_ok=True)
            write_text(d / "README.md", skill_readme(stage["official"], skill))
            created.append({"stage": stage["dir"], "skill": skill, "path": str(d)})
        for extra in stage["extra"]:
            (stage_dir / extra).mkdir(parents=True, exist_ok=True)
        write_text(
            stage_dir / "README.md",
            (
                f"# {stage['official']}\n\n"
                f"子夹名 = 插件 Skill 目录名，必须对得上。\n\n"
                + "\n".join(f"- `{s}/` ← `skills/{s}/SKILL.md`" for s in stage["skills"])
                + "\n\n开跑：新开对话 → `启动星空 "
                + stage["official"]
                + "`\n"
            ),
        )

    write_text(
        root / "README_项目结构.md",
        "\n".join(
            [
                "# 本产品项目结构",
                "",
                "只装一个星空插件。夹名必须能对上 Skill。",
                "",
                "| 路径 | 对应 |",
                "|---|---|",
                "| `00_产品资料/` | 投喂原件，不是 Skill |",
                "| `00_总控/<skill>/` | 入口 / 总控 / 00C |",
                "| `S1_选品板块/<skill>/` | 该 Skill 的全部产物 |",
                "| `S2_文案板块/<skill>/` | 同上 |",
                "| `S3_视觉板块/<skill>/` | 同上 |",
                "| `S4_广告板块/<skill>/` | 同上 |",
                "| `S5_运营板块/<skill>/` | 同上 |",
                "| `_阶段总览/` | 该板块综合报告 |",
                "| `workflow_state.yaml` | 总进度，在产品根 |",
                "| `_NEXT_S*到S*.md` | 换窗接力，在产品根 |",
                "",
                "权威对照：插件 `templates/skill_folder_map.yaml`",
                "",
            ]
        )
        + "\n",
    )
    return {"product_root": str(root), "skill_folders": created}


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", default=".", help="product project root")
    args = parser.parse_args()
    root = Path(args.root).expanduser().resolve()
    if not root.is_dir():
        raise SystemExit(f"product root not found: {root}")
    print(json.dumps(init_layout(root), ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
