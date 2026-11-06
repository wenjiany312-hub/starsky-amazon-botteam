#!/usr/bin/env python3
"""S1 阶段验收自检：只读，不修改任何文件，不达标返回非零码。

覆盖 report_depth_and_demo_policy.md 里那些"写进文档也防不住"的项：

  C1  深度下限（§3.1 / §3.0 演示档）
  C2  五维加权分能否用所列权重与单项分复现（防"勾了但没算"）
  C3  九项 MCP 取数下限（尤其第 9 项最晚下单日）
  C4  结构化必出判据（最强反方 / Pass-Hold-Stop / 允许到岸成本 / 覆盖率降级）
  C5  报告与 state 里的相对路径能否从 run 根解析（防媒体目录落错层）
  C6  汇率是否绑定快照、有无硬编码
  C7  验收勾选是否附了算式/数值/路径（防"勾了但没算"）
  C13 报告质量：整段重复写入阻断；篇幅、表格过长只提示（R06.1）

用法：
    python scripts/verify_s1_acceptance.py salon_runs/2026-08-02_condition_first_intake
    python scripts/verify_s1_acceptance.py <run_dir> --mode salon_live   # 覆盖模式判定

FAIL 阻断验收，WARN 只提示。任一 FAIL 即退出码 1。
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
from contextlib import contextmanager
from pathlib import Path

try:
    import yaml
except ImportError:  # pragma: no cover
    print("FAIL: 需要 PyYAML。运行  pip install pyyaml")
    raise SystemExit(1)

# Shared normalization is shipped with both host editions.
_SHARED_SCRIPTS = Path(__file__).resolve().parents[3] / "scripts"


@contextmanager
def _shared_imports():
    """Keep installed helper imports available only while their work executes."""
    previous = sys.path[:]
    try:
        sys.path.insert(0, str(_SHARED_SCRIPTS))
        yield
    finally:
        sys.path[:] = previous


with _shared_imports():
    from s1_market_facts import validate_criteria
    from s1_report_v2 import required_field_issues,quality_issues
    from s1_acceptance_contract import floors as coverage_floors, unverifiable_error, FX_CONTEXT_RE

# ---------------------------------------------------------------- 下限表

# (最少行数, 最少表格数据行, 最少二级以上标题)
FLOORS_FULL = {
    "stage": (300, 120, 25),
    "market": (250, 100, 20),
    "keyword": (200, 0, 15),
    "listing": (200, 50, 20),
    "ad": (250, 90, 20),
}
# salon_live：行数与表格行放宽，标题数不变（policy §3.0「少一个章节判不合格，行数少不算」）
FLOORS_LIVE = {
    "stage": (90, 40, 25),
    "market": (80, 33, 20),
    "keyword": (70, 0, 15),
    "listing": (70, 17, 20),
    "ad": (80, 30, 20),
}

REPORT_PATTERNS = [
    ("stage", re.compile(r"^阶段_S1.*\.md$")),
    ("market", re.compile(r"^01_.*市场(?:穿透|分析).*\.md$")),
    ("keyword", re.compile(r".*关键词库报告.*\.md$")),
    ("listing", re.compile(r".*Listing文案.*\.md$")),
    ("ad", re.compile(r".*广告架构.*\.md$")),
]

# 产出目录锚点：这些前缀的相对路径必须能从产品根解析
# V2.1 布局（板块夹 / 总控 / 投喂）+ 旧布局（只读兼容）
OUTPUT_DIR_PREFIXES = (
    "S1_选品板块/", "S2_文案板块/", "S3_视觉板块/", "S4_广告板块/", "S5_运营板块/",
    "00_总控/", "00_产品资料/",
    "产品资料/", "生成报告/", "bulk/", "产品垫图/", "outputs/",
)

# V2.1：S1 的报告分散在这些夹里；旧布局只有 生成报告/ 一个夹
S1_REPORT_DIRS = (
    "S1_选品板块/_阶段总览",
    "S1_选品板块/amazon-market-research",
    "S1_选品板块/alibaba-supplier-evaluation",
    "S1_选品板块/1688-product-detail-scraper",
    "S1_选品板块/amazon-ip-risk-assessment",
    "S1_选品板块/jingpu-product-selection",
    "S1_选品板块/amazon-product-development-prd",
    "00_总控/starsky-fullchain-workflow",
    "00_总控/amazon-salon-workflow",   # 2.4.0 改名前的旧夹，旧产品夹只读兼容
)


class ReportsView:
    """把多个 Skill 夹合成一个「平铺的报告目录」视图，让下面的检查函数不用关心布局。"""

    def __init__(self, dirs: list[Path]) -> None:
        self.dirs = [d for d in dirs if d.is_dir()]

    def is_dir(self) -> bool:
        return bool(self.dirs)

    def glob(self, pattern: str):
        seen: set[str] = set()
        for d in self.dirs:
            for p in sorted(d.glob(pattern)):
                if p.is_file() and p.name not in seen:
                    seen.add(p.name)
                    yield p

    def rglob(self, pattern: str):
        for d in self.dirs:
            yield from d.rglob(pattern)

    def __truediv__(self, name: str) -> Path:
        for d in self.dirs:
            if (d / name).exists():
                return d / name
        return (self.dirs[0] if self.dirs else Path(".")) / name

    def __str__(self) -> str:
        return " + ".join(str(d) for d in self.dirs) or "<无报告目录>"


def locate_reports(run_dir: Path) -> ReportsView:
    v2 = [run_dir / d for d in S1_REPORT_DIRS]
    if any(d.is_dir() for d in v2):
        return ReportsView(v2)
    return ReportsView([run_dir / "生成报告"])

ASIN_RE = re.compile(r"\bB0[A-Z0-9]{8}\b")
# 节点号可能写在 "节点 X"、"Cable Raceways（X）"、"node X" 等位置，窗口放宽到 60 字符双向
NODE_RE = re.compile(
    r"(?:(?:node|节点|类目|category)[^\n]{0,60}?(\d{7,11})|(\d{7,11})[^\n]{0,20}?(?:node|节点))",
    re.IGNORECASE,
)
FX_SUSPECT_RE = re.compile(r"(?<![\d.])([0-9]+\.\d+)(?![\d.%])")


class Findings:
    def __init__(self) -> None:
        self.fails: list[str] = []
        self.warns: list[str] = []
        self.notes: list[str] = []
        self.data_gaps: list[str] = []

    def fail(self, check: str, msg: str) -> None:
        self.fails.append(f"[{check}] {msg}")

    def warn(self, check: str, msg: str) -> None:
        self.warns.append(f"[{check}] {msg}")

    def note(self, msg: str) -> None:
        self.notes.append(msg)


# ---------------------------------------------------------------- 工具

def metrics(path: Path) -> tuple[int, int, int]:
    """返回 (行数, 表格数据行, 二级以上标题数)。"""
    text = path.read_text(encoding="utf-8", errors="replace")
    lines = text.splitlines()
    rows = sum(1 for ln in lines if ln.lstrip().startswith("|"))
    heads = sum(1 for ln in lines if ln.startswith("##"))
    return len(lines), rows, heads


def report_kind(name: str) -> str | None:
    for kind, pattern in REPORT_PATTERNS:
        if pattern.match(name):
            return kind
    return None


def read_state(run_dir: Path) -> dict:
    state_path = run_dir / "workflow_state.yaml"
    if not state_path.exists():
        return {}
    try:
        return yaml.safe_load(state_path.read_text(encoding="utf-8")) or {}
    except yaml.YAMLError:
        return {}


def concat_reports(reports_dir: Path) -> str:
    parts = []
    for path in sorted(reports_dir.rglob("*")):
        if path.suffix.lower() in {".md", ".yaml", ".yml"} and path.is_file():
            parts.append(path.read_text(encoding="utf-8", errors="replace"))
    return "\n".join(parts)


# ---------------------------------------------------------------- C1

def check_depth(reports_dir: Path, mode: str, f: Findings) -> None:
    floors = FLOORS_FULL if mode == "full_delivery" else FLOORS_LIVE
    seen = set()
    for path in sorted(reports_dir.glob("*.md")):
        kind = report_kind(path.name)
        ln, rows, heads = metrics(path)
        if kind is None:
            f.note(f"  · {path.name}: {ln} 行 / {rows} 表行 / {heads} 标题（中间产物，不设下限）")
            continue
        seen.add(kind)
        body=path.read_text(encoding='utf-8-sig')
        bad=required_field_issues(body,kind)
        if bad:
            f.fail("C1", f"{path.name} 必备字段未通过：{'、'.join(bad)}")
        else:
            f.note(f"  ✓ {path.name}: 必备字段齐全（{ln}行，行数不作为通过依据）")

    if "stage" not in seen:
        f.fail("C1", "缺少 阶段_S1_*.md 阶段综合报告")


# ---------------------------------------------------------------- C2

def check_scores(reports_dir: Path, f: Findings) -> None:
    sel = reports_dir / "00_product_selection.yaml"
    if not sel.exists():
        f.fail("C2", "缺少 00_product_selection.yaml，无法复算五维加权分")
        return
    try:
        data = yaml.safe_load(sel.read_text(encoding="utf-8")) or {}
    except yaml.YAMLError as exc:
        f.fail("C2", f"00_product_selection.yaml 解析失败：{exc}")
        return

    weights = data.get("scoring_weights") or {}
    if not weights:
        f.fail("C2", "00_product_selection.yaml 缺 scoring_weights")
        return

    total_w = round(sum(weights.values()), 6)
    if abs(total_w - 1.0) > 1e-6:
        f.fail("C2", f"权重合计 {total_w} ≠ 1.00：{weights}")

    candidates = data.get("candidates") or []
    if not candidates:
        f.fail("C2", "00_product_selection.yaml 无 candidates")
        return

    # A partially evidenced projection is not a ranked recommendation. Known
    # candidates still undergo the full evidence/weighted-total checks below.
    pending_ranking = data.get('scoring_status')=='pending_evidence' and any(
        isinstance(cell,dict) and cell.get('score') is None and str(cell.get('missing_reason') or '').strip()
        for cand in candidates for axis in weights for cell in [(cand.get('scores') or {}).get(axis)])
    if pending_ranking and any(cand.get('rank') is not None for cand in candidates):
        f.fail('C2','候选尚缺评分依据，不得形成整体推荐排名')

    md_path = reports_dir / "00_选品推荐报告.md"
    md_text = md_path.read_text(encoding="utf-8", errors="replace") if md_path.exists() else ""

    prev_score = None
    for cand in candidates:
        rank = cand.get("rank")
        name = cand.get("product_direction", f"rank {rank}")
        scores = cand.get("scores") or {}
        stated = cand.get("weighted_score")

        missing = set(weights) - set(scores)
        if missing:
            f.fail("C2", f"#{rank} {name} 缺单项分：{sorted(missing)}")
            continue

        if any(isinstance(item,dict) for item in scores.values()):
            numeric={};invalid=False;unknown=[]
            for axis in weights:
                entry=scores[axis]
                if not isinstance(entry,dict):f.fail('C2',f'#{rank} {axis} 缺结构化分数依据');invalid=True;continue
                score=entry.get('score');evidence=entry.get('evidence') or {}
                if score is None:
                    if not str(entry.get('missing_reason') or '').strip():f.fail('C2',f'#{rank} {axis} null分数缺原因');invalid=True
                    unknown.append(axis);continue
                if type(score) not in (int,float) or not 1<=score<=5 or not all(evidence.get(k) is not None and evidence.get(k)!='' for k in ('value','source','time_window')):
                    f.fail('C2',f'#{rank} {axis} 分数须1–5且有value/source/time_window依据');invalid=True
                else:numeric[axis]=score
            if invalid:continue
            if unknown:
                if stated is not None:f.fail('C2',f'#{rank} 缺数轴不得计算总分：{unknown}')
                f.data_gaps.append(f'[C2] #{rank} 尚缺评分依据：{unknown}；保留null原因，不编分')
                continue
            scores=numeric

        recomputed = round(sum(scores[k] * weights[k] for k in weights), 4)
        if stated is None:
            f.fail("C2", f"#{rank} {name} 缺 weighted_score（应为 {recomputed}）")
        elif abs(float(stated) - recomputed) > 0.01:
            detail = " + ".join(f"{scores[k]}×{weights[k]}" for k in weights)
            f.fail(
                "C2",
                f"#{rank} {name} 加权分不可复现：写 {stated}，按所列单项分与权重应为 {recomputed}"
                f"（{detail}）。差 {round(float(stated) - recomputed, 4)}——"
                f"要么单项分写错，要么加权算错，必须定位是哪一个",
            )
        else:
            f.note(f"  ✓ #{rank} 加权分 {stated} 可复现")

        # 排名与分数是否同序
        if not pending_ranking and prev_score is not None and stated is not None and float(stated) > prev_score + 1e-9:
            f.fail("C2", f"#{rank} 加权分 {stated} 高于前一名 {prev_score}，排名与分数不同序")
        if stated is not None:
            prev_score = float(stated)

        # MD 报告是否与 YAML 同值。MD 常按 1 位小数显示（69.75 → 69.8），
        # 故按数值容差比对而非字符串匹配，避免 round() 的银行家舍入误判。
        if md_text and stated is not None:
            md_numbers = [float(n) for n in re.findall(r"\d+\.\d+", md_text)]
            if not any(abs(n - float(stated)) <= 0.05 for n in md_numbers):
                f.warn(
                    "C2",
                    f"#{rank} 的加权分 {stated} 在 00_选品推荐报告.md 中找不到相符数值"
                    f"（容差 ±0.05），MD 与 YAML 可能不同步",
                )


# ---------------------------------------------------------------- C3

def effective_market_criteria(reports_dir: Path, profile: dict) -> dict:
    """作者写的判据 + 能由事实快照、成本模型、合规尺子原生算出的判据（作者优先）；派生失败时退回只看作者部分。"""
    author = profile.get("market_criteria") or {}
    facts_path = reports_dir / "01_market_facts.json"
    try:
        facts = json.loads(facts_path.read_text(encoding="utf-8"))
        root = (facts_path.parents[2] if 'S1_选品板块' in facts_path.parts
                else facts_path.parent.parent if facts_path.parent.name == '生成报告' else facts_path.parent)
        with _shared_imports():
            from s1_criteria import derive_criteria, merge_criteria
        return merge_criteria(author, derive_criteria(facts, root, profile))
    except Exception:
        return author


def check_mcp_floors(reports_dir: Path, mode: str, f: Findings) -> None:
    """Inspect structured coverage. Prose cannot prove a tool call or a sample count."""
    path = reports_dir / "01_market_facts.json"
    if not path.exists():
        f.fail("C3", "缺少 01_market_facts.json；先运行 s1_market_facts.py，禁止按正文关键词推定取数完成")
        return
    try:
        facts = json.loads(path.read_text(encoding="utf-8"))
    except (ValueError, OSError) as exc:
        f.fail("C3", f"市场事实文件不可读：{exc}")
        return
    coverage = facts.get("coverage") or {}
    coverage_root=path.parents[2] if 'S1_选品板块' in path.parts else path.parent.parent if path.parent.name=='生成报告' else path.parent
    floors = coverage_floors(mode)
    for key, floor in floors.items():
        item = coverage.get(key)
        if not isinstance(item, dict):
            f.fail("C3", f"{key}：缺结构化覆盖记录")
            continue
        if key=='category_comparison':
            from s1_category_comparison import required, coverage_error
            if required(coverage_root) or item.get('contract')=='r05.adjacent.v1':
                error=coverage_error(coverage_root,item)
                if error:
                    f.fail('C3',f'category_comparison：来源核验失败：{error}')
                    continue
        if item.get('status')=='not_applicable':
            error=unverifiable_error(coverage_root,key,item)
            if error:f.fail('C3',f'{key}：不可核验证据无效：{error}')
            else:f.note(f'  C3 {key}：不可核验：{item.get("reason")}；{item.get("next_action")}（跳过，不计达标）')
            continue
        observed = item.get("observed")
        if not isinstance(observed, (int, float)) or observed < 0:
            f.fail("C3", f"{key}：observed 必须为非负数量")
        elif observed < floor:
            detail = f"{key}：{observed}/{floor}；工具 {item.get('tool') or '未声明'}；{item.get('error') or item.get('reason') or '未记录缺口原因'}"
            # An external failure needs its saved receipt. Ordinary unattempted gaps still block.
            if item.get("tool") and (item.get("error") or item.get("reason")) and (item.get("sources") or item.get("source")) and item.get("next_action"):
                f.data_gaps.append("[C3] " + detail)
            else:
                f.fail("C3", detail)
        else:
            f.note(f"  C3 {key}：{observed}/{floor}")
    profile_path = reports_dir / "01_product_profile.yaml"
    profile = yaml.safe_load(profile_path.read_text(encoding="utf-8")) or {} if profile_path.exists() else {}
    criterion = effective_market_criteria(reports_dir, profile).get("latest_order_date") or {}
    if not criterion or criterion.get("status") not in {"available", "not_calculable", "pending"}:
        f.fail("C3", "latest_order_date：缺结构化最晚下单日判定；提前期缺失时列原因和重算项")


# ---------------------------------------------------------------- C4

def check_criteria(reports_dir: Path, f: Findings) -> None:
    path = reports_dir / "01_product_profile.yaml"
    if not path.exists():
        f.fail("C4", "缺少 01_product_profile.yaml，不能验证结构化必出判据")
        return
    try:
        profile = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    except yaml.YAMLError as exc:
        f.fail("C4", f"01_product_profile.yaml 解析失败：{exc}")
        return
    errors = validate_criteria(effective_market_criteria(reports_dir, profile))
    for error in errors:
        f.fail("C4", error)
    if not errors:
        f.note("  C4 结构化必出判据齐全；数据不足项保留不可计算原因，不以中文措辞判定")


# ---------------------------------------------------------------- C8

# 固定模块契约：templates/s1_report_module_contract.yaml
# 既有 C1 管深度（行数够不够），C8 管形状（板块齐不齐、顺序对不对）。
# 300 行可以是老板一眼能决策的报告，也可以是一堆数据堆着 —— 行数达标不等于能用。

MODULE_CONTRACT_REL = "templates/s1_report_module_contract.yaml"


def _find_module_contract(run_dir: Path) -> Path | None:
    """从 run 目录和脚本位置双向上溯找契约文件。"""
    cands = []
    for base in (run_dir, Path(__file__).resolve().parent):
        p = base
        for _ in range(6):
            cands.append(p / MODULE_CONTRACT_REL)
            p = p.parent
    return next((c for c in cands if c.exists()), None)


def check_modules(run_dir: Path, reports_dir: Path, f: Findings) -> None:
    # ⛔ 只数本检查自己新增的 FAIL。早期版本用 `if not f.fails` 判成功，
    #   结果只要 C1–C4 有任何失败，C8 的通过提示就永远不打印 ——
    #   别的检查一挂就看不出 C8 到底过没过。
    fails_before = len(f.fails)
    contract_path = _find_module_contract(run_dir)
    if contract_path is None:
        f.warn("C8", f"跳过：找不到 {MODULE_CONTRACT_REL}")
        return
    try:
        contract = yaml.safe_load(contract_path.read_text(encoding="utf-8")) or {}
    except yaml.YAMLError as e:
        f.fail("C8", f"模块契约解析失败：{e}")
        return

    stage = sorted(reports_dir.glob("阶段_S1*.md"))
    if not stage:
        f.fail("C8", "缺少 阶段_S1_选品可行性研究报告.md，无法做模块结构检查")
        return
    path = stage[0]
    text = path.read_text(encoding="utf-8", errors="replace")
    for issue in quality_issues(text):f.fail('C8',issue)

    # 顶部判定 → 是否负面
    verdicts = contract.get("verdicts", {})
    hit_verdict, negative = None, None
    canonical=re.search(r'唯一结论[：:]\s*(暂缓（Hold）|有条件做|不做|做)',text)
    for _key, v in verdicts.items():
        if v.get("label") and (v['label']==canonical[1] if canonical else v["label"] in text):
            hit_verdict, negative = v["label"], bool(v.get("negative"))
            break
    if hit_verdict is None:
        f.fail("C8", f"{path.name} 顶部没有明确判定徽章，"
                     f"应为：{'／'.join(v['label'] for v in verdicts.values())}")

    # ⛔ 必须先按 Markdown 标题切段，再只对**标题**做匹配。
    #   早期版本直接拿正则在全文找子串，结果 M08 的「市场容量」命中了
    #   M03 口径注释里的「不是全市场容量」，位置回退 → 误报顺序错乱、
    #   段落被切成两个字 → 必含要素全查不到。教训：模块识别只认标题。
    sections: list[tuple[str, str]] = []          # (标题文本, 段落正文)
    heads = list(re.finditer(r"^(#{1,2})[ \t]+(.+?)[ \t]*$", text, re.MULTILINE))
    if heads:
        preamble = text[:heads[0].start()]
        sections.append((heads[0].group(2), preamble + text[heads[0].start():
                        (heads[1].start() if len(heads) > 1 else len(text))]))
        for i, h in enumerate(heads[1:], start=1):
            end = heads[i + 1].start() if i + 1 < len(heads) else len(text)
            sections.append((h.group(2), text[h.start():end]))

    modules = sorted(contract.get("modules", []), key=lambda m: m.get("order", 0))
    seen: list[tuple[int, str]] = []              # (段落序号, 模块ID)

    for m in modules:
        mid, title = m.get("id", "?"), m.get("title", "")
        if m.get("required", "always") == "if_negative" and negative is not True:
            continue

        idx = next((i for i, (head, _) in enumerate(sections)
                    if re.search(m["match"], head, re.IGNORECASE)), None)
        if idx is None:
            f.fail("C8", f"{path.name} 缺模块 {mid} 「{title}」")
            continue
        seen.append((idx, mid))

        body = sections[idx][1]
        for req in m.get("must_contain", []):
            if not re.search(req["pattern"], body, re.IGNORECASE):
                f.fail("C8", f"{path.name} 模块 {mid}「{title}」缺要素：{req['label']}")

    # 顺序检查：按正文出现次序排出来的模块序列，应与契约 order 一致
    by_text = [mid for _, mid in sorted(seen)]
    by_contract = [mid for _, mid in seen]
    if by_text != by_contract:
        f.fail("C8", f"{path.name} 模块顺序错乱。正文实际次序："
                     f"{' → '.join(by_text)}；契约要求："
                     f"{' → '.join(by_contract)}。老板视图（M01/M02）必须在最前")

    # 双重增长基准闸门
    gm = re.search(r"增长跑赢|双重(增长)?基准|跑赢.{0,6}类目", text, re.IGNORECASE)
    if gm:
        seg = text[gm.start():gm.start() + 3000]
        missing_bench = re.search(r"数据缺失|无法比较|未取得|未确认", seg)
        if missing_bench and hit_verdict == "建议投入":
            f.fail("C8", "双重增长基准存在数据缺失，判定却是「建议投入」；"
                         "缺基准时无法区分「这个细分在涨」与「整个盘子在涨」")

    if len(f.fails) == fails_before:
        f.note(f"  C8 模块结构：{len(seen)} 个模块齐全、顺序正确"
               f"（判定：{hit_verdict}；契约 {contract_path.name}）")


# ---------------------------------------------------------------- C5
NAV_RECEIPT_RE = re.compile(r'/_阶段总览/步骤/\d{2}_回执\.md$')

# 必须以已知产出目录开头、以文件扩展名结尾；不吃空格/引号/标签，避免贪婪吞掉后文
PATH_RE = re.compile(
    r"(?:" + "|".join(re.escape(p) for p in OUTPUT_DIR_PREFIXES) + r")"
    r"[^\s\"'\)\`<>|,;：、]+?"
    r"\.(?:md|ya?ml|xlsx|html?|json|jpe?g|png|webp|mp4|csv)"
)


LATER_STAGE_PREFIXES = ("S2_", "S3_", "S4_", "S5_")


def check_paths(run_dir: Path, f: Findings) -> None:
    """report/HTML 正文里的引用断链算 FAIL；workflow_state 里的产物声明只算 WARN
    （声明的是"该步骤产物应落在哪"，步骤没跑时文件本就不存在）。"""
    cited: dict[str, set[str]] = {}
    declared: dict[str, set[str]] = {}
    for path in sorted(run_dir.rglob("*")):
        if not path.is_file() or path.suffix.lower() not in {".md", ".yaml", ".yml", ".html"}:
            continue
        if any(part in {'_history', '运行数据'} or part.startswith('.') for part in path.relative_to(run_dir).parts):
            continue  # 原始回包、流程对话和历史快照不是本轮正文链接。
        if path.name.endswith(('修订记录.yaml', '纠偏记录.yaml')) or re.fullmatch(r'报告局部优化记录_R\d+\.yaml', path.name):
            record = yaml.safe_load(path.read_text(encoding='utf-8-sig')) or {}
            for entry in record.get('archived', []):
                target = (run_dir / entry.get('to', '')).resolve()
                if not target.is_relative_to(run_dir.resolve()) or not target.is_file():
                    f.fail('C5', '归档去向不可读取：' + str(entry.get('to')))
                elif entry.get('sha256') and hashlib.sha256(target.read_bytes()).hexdigest() != entry['sha256']:
                    f.fail('C5', '归档内容哈希不符：' + str(entry.get('to')))
            continue  # from 是搬迁审计信息；校验真实去向及原始字节。
        text = path.read_text(encoding="utf-8", errors="replace")
        bucket = declared if path.name == "workflow_state.yaml" else cited
        for ref in PATH_RE.findall(text):
            if "{" in ref or "*" in ref:
                continue  # 模板占位
            bucket.setdefault(ref, set()).add(path.name)

    for ref, sources in sorted(cited.items()):
        if not (run_dir / ref).exists() and ref.startswith(LATER_STAGE_PREFIXES):
            # The navigator prints the whole roadmap; later-stage outputs are not due during S1.
            f.warn("C5", f"后续阶段产物尚不存在：{ref}（被 {', '.join(sorted(sources)[:3])} 提到，S1 不检查）")
        elif not (run_dir / ref).exists():
            f.fail(
                "C5",
                f"正文引用断链：{ref}（被 {', '.join(sorted(sources)[:3])} 引用）"
                f"——检查该目录是否落到了 run 目录之外",
            )
    for ref, _ in sorted(declared.items()):
        if NAV_RECEIPT_RE.search(ref):
            # S1-R19: roadmap nav_receipt placeholders (步骤/NN_回执.md). The real page is written as NN_<步骤名>.md
            # or replaced by the step's own report catalogue, and stage_artifacts.audit checks those.
            continue
        if not (run_dir / ref).exists() and ref not in cited:
            f.warn("C5", f"workflow_state 声明的产物尚不存在：{ref}（对应步骤未执行时属正常）")

    total = len(cited)
    if total:
        ok = sum(1 for r in cited if (run_dir / r).exists())
        f.note(f"  ✓ 正文相对路径引用 {ok}/{total} 可从 run 根解析")


# ---------------------------------------------------------------- C6

def check_checkboxes(reports_dir: Path, f: Findings) -> None:
    """C7 空勾检测：打勾的验收项必须带算式/数值/路径，否则是自述不是验证。"""
    # 至少要有一个数字、等号、斜杠路径或百分号才算带了证据
    has_evidence = re.compile(r"\d|=|/|%|\.md|\.yaml")
    empty: list[str] = []
    for path in sorted(reports_dir.glob("*.md")):
        for i, line in enumerate(path.read_text(encoding="utf-8", errors="replace").splitlines(), 1):
            stripped = line.strip()
            if not re.match(r"^[-*]\s*\[[xX]\]", stripped):
                continue
            body = re.sub(r"^[-*]\s*\[[xX]\]\s*", "", stripped)
            if not has_evidence.search(body):
                empty.append(f"{path.name}:{i} {body[:52]}")
    if empty:
        f.warn(
            "C7",
            f"空勾 {len(empty)} 处（打勾但未附算式/数值/路径，属自述非验证）："
            f"{empty[0]}" + (f" 等" if len(empty) > 1 else ""),
        )
        for item in empty[1:6]:
            f.warn("C7", f"  同上：{item}")
    else:
        f.note("  ✓ 验收勾选均带证据")


def unbound_fx_lines(body):
    return [ln.strip()[:90] for ln in body.splitlines()
            if FX_CONTEXT_RE.search(ln) and FX_SUSPECT_RE.search(ln)
            and not re.search(r'快照|snapshot|fx_rate|effective_purchase_rate|缓冲',ln)]


def check_fx(run_dir: Path, reports_dir: Path, f: Findings) -> None:
    body = concat_reports(reports_dir)
    needs_fx = bool(re.search(r"¥|CNY|人民币", body)) and bool(re.search(r"\$|USD", body))
    snapshot = run_dir / "fx_rate_snapshot.yaml"

    if needs_fx and not snapshot.exists():
        f.fail("C6", "报告含 CNY↔USD 换算但缺 fx_rate_snapshot.yaml（policy §3.4 强制）；"
                     "先运行 <python> <插件目录>/scripts/refresh_fx_snapshot.py --run \"<产品根>\" 生成，"
                     "联网取不到时加 --rate <1美元兑人民币的参考汇率>，由用户亲自给出，不得自行编造")
        return
    if not snapshot.exists():
        return

    unbound = unbound_fx_lines(body)
    if unbound:
        f.warn("C6", f"疑似未绑定快照的汇率数字 {len(unbound)} 处，首条：{unbound[0]}")
    else:
        f.note("  ✓ 汇率均绑定快照且带口径标注")

    # 成本模型实际采用的汇率必须与快照一致（常见成因：先 --init 成本输入表、后补快照，表里仍是默认 7.2）。
    def rate_of(value):
        value = value.get("value") if isinstance(value, dict) else value
        try:
            number = float(value)
        except (TypeError, ValueError):
            return None
        return number if number > 0 else None

    try:
        snap = yaml.safe_load(snapshot.read_text(encoding="utf-8-sig")) or {}
        model_path = run_dir / "S1_选品板块" / "amazon-market-research" / "05_cost_model.yaml"
        model = (yaml.safe_load(model_path.read_text(encoding="utf-8-sig")) or {}) if model_path.is_file() else {}
    except (OSError, yaml.YAMLError):
        return
    snap_rate = (rate_of((snap.get("settlement_override") or {}).get("actual_settlement_rate"))
                 or rate_of((snap.get("fx_buffer") or {}).get("effective_purchase_rate"))
                 or rate_of(snap.get("effective_purchase_rate")))
    used_rate = rate_of(model.get("effective_purchase_rate"))
    if snap_rate and used_rate and abs(used_rate - snap_rate) / snap_rate > 0.001:
        f.warn("C6", f"成本模型采用的汇率 {used_rate} 与快照有效汇率 {snap_rate} 不一致；"
                     "请在刷新快照后重新生成成本输入表并复算，不要手改模型数字")


# ---------------------------------------------------------------- main


def check_report_readability(run_dir, f):
    with _shared_imports():
        from s1_readability import readability_issues
        for path in sorted((Path(run_dir)/'S1_选品板块').rglob('*.md')):
            if any(part in {'_history', '历史', '运行数据'} for part in path.parts):
                continue
            errors = readability_issues(path.read_text(encoding='utf-8-sig'))
            if errors:
                f.fail('C12', path.name + '：' + '、'.join(errors))


def check_report_quality(run_dir, f):
    """C13（R06.1 PL-06）：同一章节被整段重复写入直接阻断；篇幅和表格超出参考上限只提示。"""
    with _shared_imports():
        from s1_readability import duplicate_sections, size_warnings
        for path in sorted((Path(run_dir)/'S1_选品板块').rglob('*.md')):
            if any(part in {'_history', '历史', '运行数据'} for part in path.parts):
                continue
            text = path.read_text(encoding='utf-8-sig')
            repeated = duplicate_sections(text)
            if repeated:
                shown = '、'.join(f'「{title.lstrip("# ")}」×{count}' for title, count in repeated[:3])
                more = f' 等 {len(repeated)} 处' if len(repeated) > 3 else ''
                f.fail('C13', f'{path.name}：正文重复写入（{shown}{more}）；用本步写入器重新生成该报告，不要在旧文件上追加')
            for note in size_warnings(path.name, text):
                f.warn('C13', f'{path.name}：{note}')
        # AS-06 / D3: one main report. A second, separately written "final" report is not a conclusion source.
        overview = Path(run_dir)/'S1_选品板块/_阶段总览'
        extra = sorted(p.name for p in overview.glob('*') if p.suffix.lower() in {'.md', '.html'}
                       and not p.name.startswith('阶段_') and re.search(r'综合报告|总报告|最终报告|方案报告', p.stem)) if overview.is_dir() else []
        if extra:
            f.warn('C13', '另编的总报告不作为结论来源：' + '、'.join(extra) + '；条件方案请写进 05_cost_inputs.yaml 的 condition_scenarios，由官方阶段报告呈现')


def financial_display_matches(text, key, expected):
    """Compare a named business field, never an unrelated amount elsewhere."""
    from decimal import Decimal
    text=re.split(r'^##\s+附录\b',text,maxsplit=1,flags=re.M)[0]
    text=re.sub(r'<details\b[^>]*>.*?</details\s*>','',text,flags=re.S|re.I)
    if key=='landed_cost':
        pattern=r'(?:落地成本|落地)\s*[:：|]?\s*(?P<before>-?)\$\s*(?P<value>-?\d[\d,]*(?:\.\d+)?)'
        wanted=Decimal(format(expected,'.2f'))
    else:
        pattern=r'(?<!目标)(?<!后)(?:广告前毛利(?:率)?|毛利(?:率)?)\s*[:：|]?\s*(?P<value>-?\d+(?:\.\d+)?)\s*[%％]'
        wanted=Decimal(format(expected*100,'.1f'))
    # Targets, historical figures and another product cannot satisfy a current
    # field. Keep matching within its line/cell rather than scanning all numbers.
    for line in text.splitlines():
        if re.search(r'目标广告前毛利|历史情景数值|历史情景|历史条件情景',line):continue
        for match in re.finditer(pattern,line):
            value=Decimal(match['value'].replace(',',''))
            if key=='landed_cost' and match['before']:value=-value
            if value==wanted:return True
    return False


def check_report_consistency(run_dir, f):
    with _shared_imports():
        from s1_market_facts import _build_market_facts_native
        from s1_stage_report import load
        root = Path(run_dir)
        model_path = root/'S1_选品板块/_阶段总览/s1_stage_report.json'
        if not model_path.is_file():
            return
        data = load(model_path)
        model = data.get('stage_model') or data
        actual = model.get('facts') or {}
        expected = _build_market_facts_native(root)
        if (actual.get('coverage', {}).get('reviews') or {}).get('observed') != expected['coverage']['reviews']['observed']:
            f.fail('C11', '总报告评论数与实际评论不一致')
        if actual.get('price_bands') != expected.get('price_bands'):
            f.fail('C11', '总报告价格带与实际样本不一致')
        receipts = sorted(root.glob('00_总控/*/运行数据/流程控制/S1/receipts/09.json'))
        if receipts:
            receipt = load(receipts[0])
            final = (receipt.get('research_recommendation') or {}).get('verdict')
            shown = (model.get('report_v2') or {}).get('decision_summary') or {}
            if final and shown.get('label') != ('暂缓（Hold）' if str(final).lower() in ('hold','暂缓（hold）') else final):
                f.fail('C11', '首屏结论与第9步决策不一致')
        from s1_decision_report import load_cost
        from s1_report_v2 import legacy_cost_view
        cost = load_cost(root)
        result = (cost.get('unified_cost') or legacy_cost_view(cost)).get('result') or {}
        md = root/'S1_选品板块/_阶段总览/阶段_S1_选品可行性研究报告.md'
        if md.is_file():
            text = md.read_text(encoding='utf8')
            for key, label in [('landed_cost', '落地成本'),('pre_ad_margin', '广告前毛利')]:
                number = result.get(key)
                if cost.get('_source_current') is not False and isinstance(number,(int,float)) and not financial_display_matches(text,key,number):
                    f.fail('C11', '总报告未呈现当前'+label+'，需核对取数与口径')

def check_compact_sources(run_dir: Path, f: Findings, receipt_path=None) -> None:
    """Compact delivery retains the input/source/output gates in its receipt."""
    run_dir=Path(run_dir).resolve()
    if receipt_path is None:
        state=read_state(run_dir)
        for record in reversed((state.get('s1_execution') or {}).get('records',[])):
            item=(record.get('artifacts') or {}).get('decision_yaml')
            if isinstance(item,dict) and item.get('selector')=='/s1_stage_data':
                receipt_path=item['path']
                break
    if receipt_path is None:
        receipt_path=run_dir/'00_总控/starsky-fullchain-workflow/运行数据/流程控制/S1/receipts/09.json'
    path=(run_dir/receipt_path).resolve()
    if not path.is_relative_to(run_dir):
        f.fail('C10','紧凑阶段回执路径越界')
        return
    if not path.is_file():return
    try:
        document=yaml.safe_load(path.read_text(encoding='utf-8-sig')) or {}
        if 's1_stage_data' not in document:return
        with _shared_imports():
            from s1_stage_report import validate_compact_output_set
            current = validate_compact_output_set(run_dir,path)
        if not current:
            f.fail('C10','紧凑阶段输入、来源清单、报告或历史原文已变化，须重生成并复验')
        else:
            f.note('  C10 紧凑阶段输入、来源清单与双报告一致')
    except (OSError,ValueError,TypeError,yaml.YAMLError) as error:
        f.fail('C10','紧凑阶段回执不可核验：'+str(error))


def check_product_front(run_dir, f):
    assets = Path(__file__).resolve().parents[2] / 'github-tabler-html-report' / 'assets'
    sys.path.insert(0, str(assets))
    from s1_product_front import inspect_front
    filename = '阶段_S1_选品可行性研究报告.html'
    paths = [run_dir/'S1_选品板块/github-tabler-html-report/HTML'/filename,
             run_dir/'S1_选品板块/_阶段总览'/filename, run_dir/'生成报告'/filename]
    report = next((p for p in paths if p.is_file()), None)
    if report is None:
        f.fail('C9', '缺少 S1 综合 HTML，无法核对产品图片与结论首屏')
        return
    errors = inspect_front(report.read_text(encoding='utf-8-sig'))
    for error in errors: f.fail('C9', error)
    if not errors: f.note('  C9 首屏结构通过；图片与选中 SKU 的真实性仍须人工核对')

def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("run_dir", type=Path, help="产品根（Codex 打开的产品文件夹，含 S1_选品板块/）；旧布局 run 目录也可")
    parser.add_argument("--mode", choices=["full_delivery", "salon_live"], help="覆盖模式判定")
    parser.add_argument("--market-only", action="store_true", help="01报告落盘前只运行C2/C3/C4上游门禁")
    parser.add_argument('--receipt',type=Path,help='紧凑阶段回执；模型与来源清单在 s1_stage_data 内')
    args = parser.parse_args()
    return verify(args.run_dir,mode=args.mode,market_only=args.market_only,receipt_path=args.receipt)


def verify(run_dir, *, mode=None, market_only=False, receipt_path=None):
    """Run unchanged native gates against one receipt-backed read-only view."""
    with _shared_imports():
        from r01_case_store import CompactCase
        store=CompactCase(run_dir)
        if receipt_path is not None:receipt_path=store.resolve(receipt_path)
        with store.materialize() as view:
            return _verify_native(view.root,mode=mode,market_only=market_only,
                                  receipt_path=view.map_path(receipt_path) if receipt_path is not None else None)


def _verify_native(run_dir, *, mode=None, market_only=False, receipt_path=None):
    run_dir = Path(run_dir).resolve()
    if not run_dir.is_dir():
        print(f"FAIL: run 目录不存在：{run_dir}")
        return 1
    reports_dir = locate_reports(run_dir)
    if not reports_dir.is_dir():
        print(f"FAIL: 找不到 S1 报告目录（既无 S1_选品板块/ 下的 Skill 夹，也无旧布局 生成报告/）：{run_dir}")
        return 1

    state = read_state(run_dir)
    mode = mode or state.get("run_mode") or "full_delivery"
    if mode not in FLOORS_FULL and mode not in ("full_delivery", "salon_live"):
        mode = "full_delivery"

    f = Findings()
    print(f"S1 验收自检｜run={run_dir.name}｜模式={mode}")
    print("=" * 72)

    if not market_only:
        check_depth(reports_dir, mode, f)
    check_scores(reports_dir, f)
    check_mcp_floors(reports_dir, mode, f)
    check_criteria(reports_dir, f)
    if not market_only:
        check_modules(run_dir, reports_dir, f)
        check_product_front(run_dir, f)
        check_compact_sources(run_dir,f,receipt_path)
        check_report_consistency(run_dir,f)
        check_report_readability(run_dir,f)
        check_report_quality(run_dir,f)
        check_paths(run_dir, f)
        check_checkboxes(reports_dir, f)
        check_fx(run_dir, reports_dir, f)

    if f.notes:
        print("\n明细：")
        for n in f.notes:
            print(n)
    if f.warns:
        print(f"\nWARN（{len(f.warns)}）：")
        for w in f.warns:
            print(f"  ! {w}")
    if f.data_gaps:
        print(f"\n待补证（{len(f.data_gaps)}，业务保持 Hold）：")
        for gap in f.data_gaps:
            print(f"  ? {gap}")
    if f.fails:
        print(f"\nFAIL（{len(f.fails)}）：")
        for x in f.fails:
            print(f"  ✗ {x}")
        print("\n" + "=" * 72)
        print(f"结果：不达标，{len(f.fails)} 项阻断。stage_acceptance.S1 不得写 accepted。")
        return 1

    print("\n" + "=" * 72)
    if f.data_gaps:
        print("结果：报告结构检查通过，证据仍有缺口；可交付待补证研究稿，stage_acceptance.S1 不得写 accepted。")
        return 2
    print("结果：全部通过，S1 可进入验收。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
