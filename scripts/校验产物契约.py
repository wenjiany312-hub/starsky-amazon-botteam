#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
产物契约校验器（星空 V2.1）—— 对照 templates/skill_folder_map.yaml 与 templates/artifact_contract.md
检查一个产品根目录。

契约是权威的，实跑必须服从。这个脚本把「感觉不统一」变成一张能勾掉的清单。

用法:
    python 校验产物契约.py <产品根>
    python 校验产物契约.py <产品根> --path supply-first   # 手动指定 S1 路径，不靠自动判定
    python 校验产物契约.py <产品根> --json                # 机器可读，给闸门用
    python 校验产物契约.py <产品根> --contract <artifact_contract.md> --folder-map <skill_folder_map.yaml>

退出码（阶段闸门按这个判）:
    0 = 合规，放行
    1 = 存在分叉 / 版本并存 / 顶层或板块内目录违规 / 词包命名违规 —— ⛔ 拦截，必须修
    2 = 仅有缺失 —— ⚠️ 报给主讲，人工判是不是这条路径本来就不跑
    3 = 参数或环境错误
"""

import argparse
import json
import re
import sys
from pathlib import Path

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

try:
    import yaml  # type: ignore
except ImportError:  # pragma: no cover
    yaml = None

PLUGIN_ROOT = Path(__file__).resolve().parent.parent

# ---------------------------------------------------------------- 布局常量（skill_folder_map.yaml 缺失时的兜底）

STAGE_DIRS = ["S1_选品板块", "S2_文案板块", "S3_视觉板块", "S4_广告板块", "S5_运营板块"]
CONTROL_DIR = "00_总控"
FEED_DIR = "00_产品资料"
OVERVIEW_DIR = "_阶段总览"
HTML_SKILL = "github-tabler-html-report"
KEYWORD_SKILLS = ["amazon-sif-keyword-research", "amazon-sellersprite-keyword-research",
                  "amazon-xiyou-keyword-research"]

# 2.4.0 改名前的旧夹名：旧产品夹里只读兼容，校验器视为合法子夹
LEGACY_SKILL_NAMES = {
    "amazon-salon-workflow": "starsky-fullchain-workflow",
    "codex-amazon-salon-quickstart": "starsky-singlepoint-intake",
}

FALLBACK_LAYOUT = {
    CONTROL_DIR: ["starsky-amazon-agent", "starsky-fullchain-workflow", "starsky-singlepoint-intake",
                  "category-adapter"] + list(LEGACY_SKILL_NAMES.keys()),
    "S1_选品板块": ["jingpu-product-selection", "amazon-trend-signal-scanner", "amazon-market-research",
                "amazon-competition-difficulty-assessor", "amazon-gray-tactic-detector",
                "amazon-seasonal-selection", "alibaba-supplier-evaluation",
                "1688-product-detail-scraper", "amazon-ip-risk-assessment",
                "amazon-variant-packaging-planner", "amazon-product-development-prd",
                HTML_SKILL],
    "S2_文案板块": KEYWORD_SKILLS + ["amazon-listing-copywriter", HTML_SKILL, "_投喂_关键词导出"],
    "S3_视觉板块": ["amazon-visual-benchmark-learner", "amazon-listing-image-designer",
                "amazon-premium-aplus-designer", "amazon-standard-aplus-designer",
                "amazon-product-video-director", HTML_SKILL,
                "出图_主副图", "出图_高级A+", "出图_普通A+", "视频_即梦"],
    "S4_广告板块": ["amazon-ad-strategy-architect", "amazon-ad-campaign-builder", "amazon-bulk-upload",
                "amazon-bulk-upload-troubleshooter", "amazon-ad-report-analyzer", "amazon-launch-plan",
                HTML_SKILL],
    "S5_运营板块": ["amazon-ops-workflow", "amazon-store-patrol-orchestrator", "lingxing-erp-connector",
                HTML_SKILL],
}

# 旧布局的顶层目录 → 现在该去哪（出现即硬错误）
LEGACY_TOP_DIRS = {
    "生成报告": "各板块 Skill 夹（见 artifact_contract.md）",
    "产品资料": "00_产品资料/（投喂原件）或 S1_选品板块/1688-product-detail-scraper/媒体/（1688 抓取）",
    "产品垫图": "S3_视觉板块/amazon-listing-image-designer/产品垫图/",
    "关键词导出": "S2_文案板块/_投喂_关键词导出/",
    "visuals": "S3_视觉板块/amazon-listing-image-designer/visuals/",
    "aplus_visuals": "S3_视觉板块/amazon-premium-aplus-designer/aplus_visuals/",
    "video_exports": "S3_视觉板块/amazon-product-video-director/video_exports/",
    "视频垫图": "S3_视觉板块/amazon-product-video-director/视频垫图/",
    "bulk": "S4_广告板块/amazon-bulk-upload/bulk/",
    "运行数据": "{板块夹}/{skill}/运行数据/",
    "运行输入": "00_产品资料/",
    "HTML": "{板块夹}/github-tabler-html-report/HTML/",
    "生成图片": "S3_视觉板块/amazon-listing-image-designer/visuals/",
    "产品视频": "S3_视觉板块/amazon-product-video-director/video_exports/",
}

# 产品根上允许的文件
ROOT_FILE_WHITELIST = [
    r"^workflow_state\.yaml$",
    r"^_NEXT_.+\.md$",
    r"^_全链闭环自检\.md$",
    r"^_PLAN_.+\.md$",
    r"^README_项目结构\.md$",
    r"^fx_rate_snapshot\.yaml$",
    r"^\.gitignore$",
]

# 不进契约但允许存在的过程文件 / 目录（相对产品根）
COMMON_WHITELIST = [
    r"(^|/)README\.md$",
    r"(^|/)README_投喂说明\.md$",
    r"(^|/)_投喂状态\.yaml$",
    r".*\.bak(-[\w-]+)?$",
    r".*\.log$",
    r"^00_产品资料/",
    r"^S2_文案板块/_投喂_关键词导出/",
    r"^S5_运营板块/_投喂_巡检报表/",
    r"/运行数据/",
    r"/媒体/",
    r"/产品垫图/",
    r"/视频垫图/",
    r"/visuals/",
    r"/aplus_visuals/",
    r"/video_exports/",
    r"/video_production_v2/",
    r"/bulk/_archive/",
    r"/erp_extract/",
    r"_data_source_log.*\.yaml$",
    r"\.validation\.json$",
    r"^S4_广告板块/amazon-bulk-upload(-troubleshooter)?/bulk/.*\.(xlsx|md|yaml)$",
]

# step 分档：required / path:xxx / stage:S? / optional
STEP_TIERS = {
    # 总控
    "intake_interview": "path:condition-first",
    "single_point_intake": "required_if_singlepoint",
    "data_intake": "path:supply-first",
    "enterprise_data_ingest": "path:supply-first",
    "condition_select": "path:condition-first",
    # S1（板块动过才要求；刚建好的空产品夹不报缺失）
    "jingpu_selection": "optional",
    # 2.5.0 阈值法方法层：首版全部 optional，跑过真实 run 后再把 difficulty_assessment 转 stage:S1
    "signal_scan": "optional",
    "seasonal_selection": "optional",
    "difficulty_assessment": "optional",
    "competitor_signal_check": "optional",
    "variant_plan": "optional",
    "market_research": "stage:S1_选品板块",
    "competitor_asin_research": "stage:S2_文案板块",   # 竞品池是 S2 词表导出的前置，S2 动了才要求
    "supplier": "path:supply-first",
    "product_scrape": "path:supply-first",
    "supplier_data_request": "path:supply-first",
    "supplier_data_merge": "path:supply-first",
    "ip_compliance": "stage:S1_选品板块",
    "product_prd": "optional",
    "phase_s1": "stage:S1_选品板块",
    # S2
    "keyword_export_prepare": "stage:S2_文案板块",
    "keyword_research": "stage:S2_文案板块",
    "keyword_html_report": "stage:S2_文案板块",
    "keyword_multi_source": "optional",
    "listing_copy": "stage:S2_文案板块",
    "phase_s2": "stage:S2_文案板块",
    # S3
    "visual_learn": "stage:S3_视觉板块",
    "visual_asset_prepare": "stage:S3_视觉板块",
    "image_design": "stage:S3_视觉板块",
    "visual_generation_ready": "stage:S3_视觉板块",
    "visual_generation": "optional",
    "premium_aplus": "stage:S3_视觉板块",
    "standard_aplus": "optional",
    "product_video": "optional",
    "video_asset_prepare": "optional",
    "video_generation_ready": "optional",
    "video_production": "optional",
    "phase_s3": "stage:S3_视觉板块",
    # S4
    "ad_intake": "stage:S4_广告板块",
    "ad_strategy": "stage:S4_广告板块",
    "ad_architecture": "stage:S4_广告板块",
    "bulk": "optional",
    "ad_upload_support": "optional",
    "ad_diagnosis": "optional",
    "launch_plan": "optional",
    "phase_s4": "stage:S4_广告板块",
    # S5
    "ops_state": "optional",
    "store_patrol": "stage:S5_运营板块",
    "erp_extract": "optional",
    "phase_s5": "stage:S5_运营板块",
}

PLACEHOLDERS = {
    "{商品ID}": r"[^/\\]+",
    "{date}": r"\d{6,8}",
    "{n}": r"\d+",
    "{report_slug}": r"[^/\\]+",
    "{产品简称}": r"[^/\\]+",
    "{日期}": r"\d{6,8}",
}

# 契约承认的合法后缀：可选日期 + demo 标记
LEGIT_SUFFIX_RE = r"(_\d{6,8})?(_DEMO_ONLY_NOT_UPLOADABLE)?"

# 词包禁止带来源后缀（V2.1 靠 Skill 夹区分来源）
BAD_SOURCE_SUFFIX_RE = re.compile(
    r"(^|/)(keyword_package|06_关键词库报告|06_keyword_data_source_log)[._-](sif|sellersprite|xiyou|SIF|卖家精灵|西柚)\.(yaml|md|json|html)$"
)


# ---------------------------------------------------------------- 布局加载


def load_layout(folder_map: Path | None):
    """返回 {顶层目录: [允许的子夹名]}；读不到 yaml 就用兜底常量。"""
    layout = {k: list(v) for k, v in FALLBACK_LAYOUT.items()}
    if not folder_map or not folder_map.is_file() or yaml is None:
        return layout
    try:
        data = yaml.safe_load(folder_map.read_text(encoding="utf-8")) or {}
    except Exception:
        return layout
    control = ((data.get("shared") or {}).get(CONTROL_DIR) or {}).get("skills")
    if isinstance(control, dict):
        layout[CONTROL_DIR] = list(control.keys())
    elif isinstance(control, list):
        layout[CONTROL_DIR] = [str(x) for x in control]
    # 旧名子夹只读兼容（yaml 里的 legacy_skill_names 优先，没有就用兜底常量）
    legacy = data.get("legacy_skill_names") or LEGACY_SKILL_NAMES
    for old in legacy:
        if old not in layout[CONTROL_DIR]:
            layout[CONTROL_DIR].append(str(old))
    for _, st in (data.get("stages") or {}).items():
        d = st.get("dir")
        if not d:
            continue
        subs = list((st.get("skills") or {}).keys())
        for feed in [*(st.get("feed_dirs") or {}), *(st.get("generated_dirs") or {})]:
            top = str(feed).split("/", 1)[0]
            if top not in subs:
                subs.append(top)
        layout[d] = subs
    return layout


# ---------------------------------------------------------------- 契约解析


def _backticked(s: str):
    return [x.strip() for x in re.findall(r"`([^`]+)`", s) if x.strip()]


def parse_contract(contract_path: Path):
    """从 V2.1 artifact_contract.md 解析 step_id → [产品根相对 canonical 路径]。

    表格三列：step_id | 归属夹 | 文件。板块由所在小节标题 `### S1 · …（`S1_选品板块/`）` 给出；
    00 小节的归属夹已含 00_总控/ 前缀。文件列里 `HTML → x` 段是板块相对路径。
    """
    text = contract_path.read_text(encoding="utf-8")
    steps: dict[str, list[str]] = {}
    stage = ""
    for raw in text.splitlines():
        line = raw.strip()
        m = re.match(r"^###\s+.*?（`([^`]+)/`）", line)
        if m:
            stage = m.group(1)
            continue
        if line.startswith("### 00"):
            stage = ""
            continue
        if not line.startswith("|") or line.startswith("| step_id") or set(line) <= set("|- "):
            continue
        cols = [c.strip() for c in line.strip("|").split("|")]
        if len(cols) < 3:
            continue
        step_id = re.sub(r"（.*?）", "", cols[0]).strip()
        owners = _backticked(cols[1])
        owner = owners[0] if owners else ""
        if owner == "{关键词 Skill}/":
            owner = "{关键词Skill}/"
        paths: list[str] = []
        for seg in cols[2].split("；"):
            seg = seg.strip()
            if not seg:
                continue
            if "→" in seg:
                head, tail = seg.split("→", 1)
                items = _backticked(tail)
                if head.strip().startswith("HTML"):
                    paths += [f"{stage}/{it}" if stage else it for it in items]
                else:
                    paths += [_join(stage, owner, it) for it in items]
            else:
                paths += [_join(stage, owner, it) for it in _backticked(seg)]
        if paths:
            steps[step_id] = paths
    return steps


def _join(stage: str, owner: str, item: str) -> str:
    if owner.startswith("00_总控/") or not stage:
        return f"{owner}{item}"
    return f"{stage}/{owner}{item}"


def path_to_regex(canonical: str) -> re.Pattern:
    pat = canonical
    pat = pat.replace("{关键词Skill}", "\x00(?:" + "|".join(map(re.escape, KEYWORD_SKILLS)) + ")\x00")
    pat = re.sub(r"\{([^{}|]+\|[^{}]+)\}",
                 lambda m: "\x00(?:" + "|".join(map(re.escape, m.group(1).split("|"))) + ")\x00", pat)
    for ph, rx in PLACEHOLDERS.items():
        pat = pat.replace(ph, "\x00" + rx + "\x00")
    parts = pat.split("\x00")
    escaped = "".join(p if i % 2 else re.escape(p) for i, p in enumerate(parts))
    if canonical.endswith("/"):
        return re.compile("^" + escaped)
    stem, dot, ext = escaped.rpartition(re.escape("."))
    if dot:
        escaped = stem + LEGIT_SUFFIX_RE + dot + ext
    return re.compile("^" + escaped + "$")


def fork_pattern(canonical: str) -> re.Pattern:
    """同物异名 / 分叉探测：同目录、同主干、带额外尾巴的文件。"""
    p = canonical.rstrip("/")
    p = p.replace("{关键词Skill}", "\x00")
    parent = str(Path(p).parent).replace("\\", "/")
    stem, ext = Path(p).stem, Path(p).suffix
    if "{" in stem:  # 带占位符的主干不做分叉探测（占位符本身就吃掉了尾巴）
        return re.compile(r"(?!x)x")
    prefix = "" if parent in (".", "") else re.escape(parent).replace("\x00", "[^/]+") + "/"
    ext_esc = re.escape(ext) if ext else r"\.[^.]+"
    return re.compile("^" + prefix + re.escape(stem) + r"[^/]*" + ext_esc + "$")


# ---------------------------------------------------------------- 扫描与判定


def detect_path(files, run_dir: Path) -> str:
    ws = run_dir / "workflow_state.yaml"
    if ws.is_file():
        try:
            text = ws.read_text(encoding="utf-8", errors="ignore")
            if re.search(r"supply[_-]?first", text, re.I):
                return "supply-first"
            if re.search(r"condition[_-]?first", text, re.I):
                return "condition-first"
        except OSError:
            pass
    fs = set(files)
    # 新旧总控夹名都认（旧产品夹只读兼容）
    for ctl in ("starsky-fullchain-workflow", "amazon-salon-workflow"):
        if {f"00_总控/{ctl}/enterprise_data_manifest.yaml", f"00_总控/{ctl}/00C_资料接入与优化建议报告.md"} & fs:
            return "supply-first"
        if f"00_总控/{ctl}/00A_选品需求确认单.md" in fs:
            return "condition-first"
    return "unknown"


def stage_started(stage_dir: str, files) -> bool:
    """板块夹里除 README 之外有任何文件，就算这一板块动过。"""
    return any(f.startswith(stage_dir + "/") and not re.search(r"(^|/)README(_.*)?\.md$", f) for f in files)


def tier_active(step_id: str, run_path: str, files) -> bool:
    tier = STEP_TIERS.get(step_id, "required")
    if tier == "required":
        return True
    if tier == "optional":
        return False
    if tier == "required_if_singlepoint":
        return any(f"00_总控/{c}/00B_单点任务接入单.md" in files
                   for c in ("starsky-singlepoint-intake", "codex-amazon-salon-quickstart"))
    if tier.startswith("path:"):
        # S1 没动过就不要求两条路径的任何产物
        return stage_started("S1_选品板块", files) and run_path in (tier.split(":", 1)[1], "unknown")
    if tier.startswith("stage:"):
        return stage_started(tier.split(":", 1)[1], files)
    return True


def whitelisted(rel: str) -> bool:
    return any(re.search(rx, rel) for rx in COMMON_WHITELIST)


def check_layout(run_dir: Path, layout: dict):
    """顶层目录 + 板块内 Skill 夹名 唯一真相校验。"""
    violations = []
    allowed_top = set(layout.keys()) | {FEED_DIR}
    for p in sorted(run_dir.iterdir()):
        if p.is_dir():
            if p.name in allowed_top or p.name.startswith("."):
                continue
            hint = LEGACY_TOP_DIRS.get(p.name, "不在顶层目录唯一真相表内（00_产品资料 / 00_总控 / S1–S5 板块夹）")
            violations.append((p.name + "/", hint))
        else:
            if not any(re.search(rx, p.name) for rx in ROOT_FILE_WHITELIST):
                violations.append((p.name, "产品根只放 workflow_state.yaml / _NEXT_*.md / README_项目结构.md；报告进 Skill 夹"))
    for top, subs in layout.items():
        d = run_dir / top
        if not d.is_dir():
            continue
        allowed = set(subs) | {OVERVIEW_DIR}
        for p in sorted(d.iterdir()):
            if p.is_dir():
                if p.name not in allowed:
                    violations.append((f"{top}/{p.name}/", f"子夹名必须是插件 skills/ 目录名或 {OVERVIEW_DIR}；本板块允许：{', '.join(sorted(allowed))}"))
            elif not re.search(r"^README(_.*)?\.md$", p.name):
                violations.append((f"{top}/{p.name}", "板块夹根上不放文件，进对应 Skill 夹或 _阶段总览/"))
    return violations


def check_keyword_naming(files):
    problems = []
    for f in files:
        if BAD_SOURCE_SUFFIX_RE.search(f):
            problems.append((f, "V2.1 每源一夹，文件名不带来源后缀：写成 S2_文案板块/<关键词Skill>/keyword_package.yaml"))
        if re.search(r"^S2_文案板块/(?!_投喂_关键词导出/)[^/]+/分源明细/", f):
            problems.append((f, "分源明细/ 已废：每个来源写自己的 Skill 夹"))
    return problems


def check(run_dir: Path, contract_path: Path, folder_map: Path | None, forced_path=None):
    layout = load_layout(folder_map)
    steps = parse_contract(contract_path)
    if yaml and (run_dir/'workflow_state.yaml').is_file():
        state=yaml.safe_load((run_dir/'workflow_state.yaml').read_bytes()) or {}
        if (state.get('s3_execution') or {}).get('roadmap_version')=='r03.s3':
            native=yaml.safe_load((PLUGIN_ROOT/'templates/artifact_contract_s3_r03.yaml').read_bytes())
            steps={key:paths for key,paths in steps.items() if not any(p.startswith('S3_视觉板块/') for p in paths)}
            steps.update(native['required'])
            plan_path=run_dir/'S3_视觉板块/amazon-listing-image-designer/visual_suite.yaml'
            plan=yaml.safe_load(plan_path.read_bytes()) if plan_path.is_file() else {}
            steps['aplus_production']=[native['aplus'][(plan or {}).get('aplus_path','premium')]]
    files = sorted(
        p.relative_to(run_dir).as_posix() for p in run_dir.rglob("*")
        if p.is_file() and not any(part.startswith(".") for part in p.relative_to(run_dir).parts)
    )
    run_path = forced_path or detect_path(files, run_dir)

    matched, missing, forks, skipped_steps = set(), [], [], []
    for step_id, canonicals in steps.items():
        active = tier_active(step_id, run_path, files)
        for canonical in canonicals:
            rx, frx = path_to_regex(canonical), fork_pattern(canonical)
            hits = [f for f in files if rx.match(f)]
            variants = [f for f in files if frx.match(f) and f not in hits]
            matched.update(hits)
            matched.update(variants)
            if variants:
                forks.append((step_id, canonical, hits, variants))
            elif not hits:
                if active:
                    missing.append((step_id, canonical))
                else:
                    skipped_steps.append((step_id, canonical, STEP_TIERS.get(step_id, "required")))

    extras = [
        f for f in files
        if f not in matched and not whitelisted(f)
        and not any(re.search(rx, f) for rx in ROOT_FILE_WHITELIST)
    ]

    version_conflicts = []
    versioned_scope = re.compile(r"^(S[1-5]_[^/]+|00_总控)/(?!出图_|视频_即梦/)[^/]+/(?!运行数据/|媒体/|visuals/|aplus_visuals/|video_exports/|产品垫图/|视频垫图/|bulk/)")
    for f in files:
        if not versioned_scope.match(f):
            continue
        m = re.match(r"^(.*?)(_v\d+[^/.]*)(\.[^./]+)$", f)
        if m and (m.group(1) + m.group(3)) in files:
            version_conflicts.append((m.group(1) + m.group(3), f))

    return {
        "run_dir": str(run_dir),
        "run_path": run_path,
        "file_count": len(files),
        "layout_violations": check_layout(run_dir, layout),
        "keyword_naming_problems": check_keyword_naming(files),
        "forks": forks,
        "version_conflicts": version_conflicts,
        "missing": missing,
        "extras": extras,
        "skipped": skipped_steps,
    }


def is_hard(res: dict) -> bool:
    return bool(res["layout_violations"] or res["keyword_naming_problems"]
                or res["forks"] or res["version_conflicts"])


# ---------------------------------------------------------------- 输出


def report(res: dict) -> int:
    print("=" * 72)
    print(f"契约校验（V2.1 布局）· {res['run_dir']}")
    print(f"S1 路径判定：{res['run_path']}　｜　扫描 {res['file_count']} 个文件")
    print("=" * 72)

    if res["layout_violations"]:
        print(f"\n❌ 目录违规（{len(res['layout_violations'])} 处）—— 硬错误")
        print("-" * 72)
        print("  夹名对不上 skill_folder_map.yaml，下一窗就找不到上游：")
        for name, hint in res["layout_violations"]:
            print(f"    {name}  →  {hint}")

    if res["keyword_naming_problems"]:
        print(f"\n❌ 词包命名违规（{len(res['keyword_naming_problems'])} 处）—— 硬错误")
        print("-" * 72)
        for f, why in res["keyword_naming_problems"]:
            print(f"    {f}\n        {why}")

    if res["forks"]:
        print(f"\n❌ 分叉 / 同物异名（{len(res['forks'])} 处）—— 硬错误")
        print("-" * 72)
        for step_id, canonical, hits, variants in res["forks"]:
            flag = "canonical 缺席，只有变体" if not hits else "canonical 与变体并存"
            print(f"\n  [{step_id}] {flag}")
            print(f"    契约要求: {canonical}")
            for h in hits:
                print(f"      ✅ {h}")
            for v in variants:
                print(f"      ⚠️  {v}")

    if res["version_conflicts"]:
        print(f"\n❌ 版本并存（{len(res['version_conflicts'])} 处）—— 硬错误")
        print("-" * 72)
        for base, v in res["version_conflicts"]:
            print(f"    {base}")
            print(f"    {v}   ← 两份同时在，下一段读哪份看运气")

    if res["missing"]:
        print(f"\n⚠️  缺失（{len(res['missing'])} 处）—— 本路径 / 已开始的板块应该有却没有")
        print("-" * 72)
        for step_id, canonical in res["missing"]:
            print(f"    [{step_id}] {canonical}")

    if res["extras"]:
        print(f"\n➕ 契约外产物（{len(res['extras'])} 个）—— 逐个判：进契约，还是删")
        print("-" * 72)
        for e in res["extras"][:40]:
            print(f"    {e}")
        if len(res["extras"]) > 40:
            print(f"    …… 另有 {len(res['extras']) - 40} 个，用 --json 看全量")

    if res["skipped"]:
        print(f"\nℹ️  本路径 / 未开始板块不要求的 step（{len(res['skipped'])} 处，已静默）")

    print()
    if is_hard(res):
        print("判定：❌ 不合规 —— ⛔ 不许进入下一阶段，先修目录与命名")
        return 1
    if res["missing"]:
        print("判定：⚠️ 有缺失但无分叉 —— 人工判是不是这条路径本来就不跑")
        return 2
    print("✅ 全部合规")
    return 0


def main():
    ap = argparse.ArgumentParser(description="对照 skill_folder_map.yaml + artifact_contract.md 校验产品根目录产物")
    ap.add_argument("run_dir", help="产品根（Codex 打开的产品文件夹）")
    ap.add_argument("--contract", default=None)
    ap.add_argument("--folder-map", default=None)
    ap.add_argument("--path", default=None, choices=["condition-first", "supply-first"],
                    help="手动指定 S1 路径，不靠自动判定")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args()

    run_dir = Path(args.run_dir).resolve()
    if not run_dir.is_dir():
        print(f"产品根不存在: {run_dir}", file=sys.stderr)
        return 3

    contract = Path(args.contract).resolve() if args.contract else PLUGIN_ROOT / "templates" / "artifact_contract.md"
    folder_map = Path(args.folder_map).resolve() if args.folder_map else PLUGIN_ROOT / "templates" / "skill_folder_map.yaml"
    if not contract.is_file():
        print("找不到 artifact_contract.md，请用 --contract 指定", file=sys.stderr)
        return 3

    res = check(run_dir, contract, folder_map if folder_map.is_file() else None, args.path)

    if args.json:
        print(json.dumps(res, ensure_ascii=False, indent=2, default=list))
        return 1 if is_hard(res) else 0

    return report(res)


if __name__ == "__main__":
    sys.exit(main())
