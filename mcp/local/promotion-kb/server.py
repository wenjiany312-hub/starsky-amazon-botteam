#!/usr/bin/env python3
# -*- coding: utf-8 -*-
r"""产品推广知识库 · 本地 MCP 服务（stdio，纯标准库，无第三方依赖）

姊妹版：amazon-ad-knowledge-learner/scripts/kb_mcp_server.py（04 广告库）。
本版适配 promotion-kb 的三点差异：
  1. 卡片 ID 前缀是 PK-（引用姊妹库广告执行细节时仍是 KC-）；
  2. 场景维度是 scene（新品首推/断货重推/老品反超/排名恢复…），不是 04 库的 ad_type；
  3. 场景手册在 scenarios/SC-*.md（04 库在 playbooks/PB-*.md）。

工具一览：
  kb_status              库状态、大纲完成度、开放缺口数、产品线清单
  kb_query               按标签 + 文本检索知识卡
  kb_get_card            读一张卡全文（frontmatter + 正文）
  kb_scenario_rules      按 产品类型 × 生命周期 × 推广场景 取规则/阈值/诊断（带回退链）
  kb_diagnose            按症状找诊断卡与相关阈值
  kb_playbook            读场景手册（SC-*）
  kb_products            读 kb_manifest 里登记的产品线（ASIN/阶段/目录）
  kb_record_observation  回写实测观察到 gaps.yaml（学习闭环）
  kb_list_gaps           列开放问题

启动：python kb_mcp_server.py --kb "<bundled-promotion-kb>"
      （或环境变量 PROMO_KB_ROOT）
协议：MCP JSON-RPC 2.0 over stdio（一行一条消息）。日志只写 stderr。
"""
from __future__ import annotations

import argparse
import datetime as _dt
import json
import os
import re
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import kb as KB  # noqa: E402
import kb_follow  # noqa: E402

SERVER_NAME = "promotion-knowledge-base"
SERVER_VERSION = "1.0.0"
PROTOCOL_VERSIONS = ("2025-06-18", "2025-03-26", "2024-11-05")
CID_RE = r"(?:PK|KC)-\d{4,}"  # 本库 PK-xxxx；兼容引用姊妹库的 KC-xxxx


def log(msg: str) -> None:
    sys.stderr.write(f"[{SERVER_NAME}] {msg}\n")
    sys.stderr.flush()


# ------------------------------------------------------------------ 数据访问
class Store:
    def __init__(self, kb: Path):
        self.kb = kb
        self.P = KB.require_kb(kb)
        self._cards_mtime = None
        self._cards: list[dict] = []

    def validate_for_follow(self) -> None:
        """在切换前触发延迟解析；坏卡不能被 cards() 静默过滤后当成成功。"""
        errors = [c['_error'] for c in KB.load_cards(self.P) if c.get('_error')]
        if errors:
            raise ValueError('目标知识卡解析失败：' + str(errors[0]))
        self.status()

    def cards(self) -> list[dict]:
        mt = max([p.stat().st_mtime for p in self.P["cards"].glob("*.md")] or [0])
        if mt != self._cards_mtime:
            self._cards = [c for c in KB.load_cards(self.P) if not c.get("_error")]
            self._cards_mtime = mt
        return self._cards

    def card_summary(self, c: dict) -> dict:
        return {"id": c.get("id"), "title": c.get("title"), "knowledge_type": c.get("knowledge_type"), "authority": c.get("authority"),
                "confidence": c.get("confidence"), "status": c.get("status"), "statement": c.get("_statement"), "parameters": c.get("parameters") or {},
                "tags": c.get("tags") or {}, "sources": [{"source_id": s.get("source_id"), "locator": s.get("locator"), "date": s.get("date")} for s in (c.get("sources") or []) if isinstance(s, dict)],
                "related": c.get("related") or [], "conflicts_with": c.get("conflicts_with") or [], "promotion_hooks": c.get("promotion_hooks") or [], "file": f"cards/{c['_file']}"}

    def active(self) -> list[dict]:
        return [c for c in self.cards() if c.get("status") not in ("superseded", "retracted")]

    def _pack_file(self) -> Path | None:
        for name in ("knowledge_pack_for_promotion.yaml", "knowledge_pack_for_ads.yaml"):
            p = self.P["root"] / name
            if p.exists():
                return p
        return None

    # ---- tools ----
    def status(self) -> dict:
        man = KB.load_yaml(self.P["manifest"])
        rows = KB.topic_coverage(self.P, self.active())
        need = sum(r["need"] for r in rows)
        have = sum(min(r["have"], r["need"]) for r in rows)
        gaps = KB.load_yaml(self.P["gaps"]) if self.P["gaps"].exists() else {"gaps": []}
        pack_at = None
        pk = self._pack_file()
        if pk:
            try:
                pack_at = KB.load_yaml(pk).get("generated_at")
            except Exception:  # noqa: BLE001
                pack_at = None
        return {"kb": str(self.kb), "name": man.get("name"), "updated": man.get("updated"), "stats": man.get("stats"),
                "products": man.get("products") or [], "curriculum_completion": f"{have}/{need}",
                "open_gaps": sum(1 for g in gaps.get("gaps", []) if isinstance(g, dict) and g.get("status") == "open"),
                "top_gaps": [f"{r['topic']}(缺 {r['gap']})" for r in sorted([r for r in rows if r["gap"]], key=lambda r: (r["priority"], -r["gap"]))[:5]],
                "pack_generated_at": pack_at, "authority_tiers": "A 官方 / B 课程原文 / C 经验 / D 推论 / V 厂商宣称",
                "usage_note": "阈值均为候选口径；用户利润账与合规边界优先；产物请写 evidence_refs: [PK-xxxx]；广告执行细节引用姊妹库 04-广告知识库 KC-xxxx"}

    def query(self, tags: dict | None, text: str | None, ktype: str | None, min_conf: float, limit: int) -> list[dict]:
        want = {k: set(v if isinstance(v, list) else [v]) for k, v in (tags or {}).items() if v}
        terms = [t for t in re.split(r"[\s,，;；/]+", (text or "").lower()) if t]
        scored = []
        for c in self.active():
            if ktype and c.get("knowledge_type") != ktype:
                continue
            try:
                conf = float(c.get("confidence") or 0)
            except (TypeError, ValueError):
                conf = 0
            if conf < min_conf:
                continue
            ctags = c.get("tags") or {}
            hits = miss = 0
            for dim, vals in want.items():
                have = set(ctags.get(dim) or [])
                if have & vals:
                    hits += 1
                elif "通用" in have:
                    hits += 0.5
                else:
                    miss += 1
            hay = " ".join([str(c.get("title") or ""), c.get("_statement") or "", c.get("_body") or ""]).lower()
            th = sum(1 for t in terms if t in hay)
            if terms and th == 0 and not want:
                continue
            if want and hits == 0 and not terms:
                continue
            scored.append((hits * 2 + th * 1.5 + conf - miss, c))
        scored.sort(key=lambda x: -x[0])
        return [dict(self.card_summary(c), score=round(s, 2)) for s, c in scored[:limit]]

    def get_card(self, cid: str) -> dict | None:
        cid = str(cid or "").strip().upper()
        if not re.fullmatch(CID_RE, cid):
            cid = f"PK-{re.sub(r'[^0-9]', '', cid).zfill(4)}" if re.search(r"\d", cid) else cid
        for c in self.cards():
            if str(c.get("id") or "").upper() == cid:
                fm = {k: v for k, v in c.items() if not k.startswith("_")}
                return {"frontmatter": fm, "body": c.get("_body"), "statement": c.get("_statement"), "file": f"cards/{c['_file']}"}
        return None

    @staticmethod
    def _match(c, key: str, val: str) -> bool:
        have = (c.get("tags") or {}).get(key) or []
        return val in have or "通用" in have

    def scenario_rules(self, pt: str, st: str, sc: str, include_handbook: bool) -> dict:
        cards = self.active()
        def pick(pred):
            return [c for c in cards if pred(c)]
        chain = [
            ("exact", pick(lambda c: self._match(c, "product_type", pt) and self._match(c, "lifecycle_stage", st) and self._match(c, "scene", sc))),
        ]
        if len(chain[0][1]) < 5:
            chain.append(("scene→通用", pick(lambda c: self._match(c, "product_type", pt) and self._match(c, "lifecycle_stage", st) and "通用" in ((c.get("tags") or {}).get("scene") or []))))
            chain.append(("lifecycle→通用", pick(lambda c: self._match(c, "product_type", pt) and "通用" in ((c.get("tags") or {}).get("lifecycle_stage") or []) and self._match(c, "scene", sc))))
            chain.append(("stage+scene→通用", pick(lambda c: self._match(c, "product_type", pt) and "通用" in ((c.get("tags") or {}).get("lifecycle_stage") or []) and "通用" in ((c.get("tags") or {}).get("scene") or []))))
        seen, rules = set(), []
        for level, group in chain:
            for c in sorted(group, key=lambda x: -float(x.get("confidence") or 0)):
                if c.get("id") in seen:
                    continue
                seen.add(c.get("id"))
                rules.append(dict(self.card_summary(c), match_level=level))
        by_type: dict[str, list] = {}
        for r in rules:
            by_type.setdefault(r["knowledge_type"], []).append(r["id"])
        out = {"scenario": {"product_type": pt, "lifecycle_stage": st, "scene": sc}, "count": len(rules), "by_type": by_type, "rules": rules,
               "conflicts": sorted({(r["id"], x) for r in rules for x in r["conflicts_with"]}),
               "how_to_cite": "在推广方案/复盘里写 evidence_refs: [PK-...] 与 authority；D 级只进 strategy_review 讨论项；广告执行细节引用 04 库 KC-xxxx"}
        if include_handbook:
            pb = self.playbook(scene=sc, lifecycle=st, pb_id=None)
            if pb:
                out["handbook"] = pb
        return out

    def scenario_books(self) -> list[dict]:
        out = []
        for fp in sorted(self.P["root"].glob("scenarios/SC-*.md")) + sorted(self.P["playbooks"].glob("PB-*.md")):
            try:
                fm, _body = KB.parse_frontmatter(KB.read_text_any(fp))
                fm["_file"] = str(fp.relative_to(self.P["root"]))
                out.append(fm)
            except Exception:  # noqa: BLE001
                continue
        return out

    def playbook(self, scene: str | None, lifecycle: str | None, pb_id: str | None) -> dict | None:
        for p in self.scenario_books():
            sc = p.get("scenario") or {}
            hit = (pb_id and str(p.get("id") or "").upper() == str(pb_id).upper()) \
                or (scene and (sc.get("scene") == scene or scene in str(p.get("title") or "") or scene in str(p.get("id") or ""))) \
                or (lifecycle and lifecycle in (sc.get("lifecycle_stage") or []))
            if hit:
                body = KB.read_text_any(self.P["root"] / p["_file"])
                return {"id": p.get("id"), "title": p.get("title"), "scenario": sc, "status": p.get("status"),
                        "cards_cited": p.get("cards_cited") or [], "file": p["_file"], "markdown": body}
        return None

    def products(self) -> list[dict]:
        man = KB.load_yaml(self.P["manifest"])
        out = []
        for pr in man.get("products") or []:
            d_rel = pr.get("dir", "")
            d = self.P["root"] / d_rel if d_rel else None
            files = sorted(x.name for x in d.iterdir()) if d and d.exists() else []
            out.append(dict(pr, dir_files=files))
        return out

    def diagnose(self, symptom: str, limit: int) -> dict:
        diags = self.query(None, symptom, "diagnostic", 0.0, limit)
        thresholds = self.query(None, symptom, "threshold", 0.0, limit)
        case_studies = self.query(None, symptom, "case_study", 0.0, limit)
        related = self.query(None, symptom, None, 0.0, limit) if not diags else []
        return {"symptom": symptom, "diagnostics": diags, "thresholds": thresholds, "case_studies": case_studies, "related_if_no_diagnostic": related,
                "note": "诊断卡给原因树与候选动作；账户真实 clicks/orders/CVR 是事实主线；stale 卡需核对当前后台"}

    def record_observation(self, topic: str, observation: str, run_id: str | None, card_id: str | None, product_id: str | None, metrics: dict | None) -> dict:
        gaps = KB.load_yaml(self.P["gaps"]) if self.P["gaps"].exists() else {"gaps": []}
        entry = {"kind": "observation", "topic": topic, "observation": observation, "run_id": run_id, "card_id": card_id, "product_id": product_id,
                 "metrics": metrics or {}, "added": _dt.date.today().isoformat(), "status": "open", "source": "mcp:kb_record_observation"}
        gaps.setdefault("gaps", []).append(entry)
        KB.dump_yaml(gaps, self.P["gaps"])
        with self.P["log"].open("a", encoding="utf-8") as f:
            f.write(f"- {_dt.date.today().isoformat()} 观察回写（{run_id or '-'} / {card_id or '-'}）：{observation[:120]}\n")
        return {"status": "recorded", "entry": entry, "next": "下一次 promotion-kb 学习会话会消化 open observations：新建 PK 实测卡 / 调 confidence / 加反方"}

    def list_gaps(self, status: str | None) -> list[dict]:
        gaps = KB.load_yaml(self.P["gaps"]) if self.P["gaps"].exists() else {"gaps": []}
        return [g for g in gaps.get("gaps", []) if isinstance(g, dict) and (not status or g.get("status") == status)]


# ------------------------------------------------------------------ 工具声明
def tool_defs() -> list[dict]:
    tags_schema = {"type": "object", "description": "标签过滤（维度见 taxonomy.yaml），如 {\"category_family\":[\"宠物\"],\"scene\":[\"断货重推\"],\"keyword_tier\":[\"核心大词\"]}",
                   "additionalProperties": {"type": "array", "items": {"type": "string"}}}
    return [
        {"name": "kb_status", "description": "产品推广知识库状态：卡数/来源数/产品线清单/大纲完成度/开放缺口/知识包时间。任何推广决策前先调一次确认库可用。",
         "inputSchema": {"type": "object", "properties": {}}},
        {"name": "kb_query", "description": "按标签 + 文本检索知识卡（返回结论、参数、证据层级、置信度、出处、推广接口）。用于给某个具体推广决定找依据。",
         "inputSchema": {"type": "object", "properties": {"tags": tags_schema, "text": {"type": "string", "description": "关键词，空格分隔"},
                                                          "knowledge_type": {"type": "string", "enum": ["policy", "mechanism", "inference", "playbook", "threshold", "diagnostic", "anti_pattern", "glossary", "formula", "case_study"]},
                                                          "min_confidence": {"type": "number", "default": 0.0}, "limit": {"type": "integer", "default": 10}}}},
        {"name": "kb_get_card", "description": "读一张知识卡全文（frontmatter + 六段正文）。", "inputSchema": {"type": "object", "properties": {"id": {"type": "string", "description": "如 PK-0001（引用姊妹库广告卡时也可传 KC-xxxx）"}}, "required": ["id"]}},
        {"name": "kb_scenario_rules", "description": "按 产品类型 × 生命周期 × 推广场景 取该场景的全部规则/阈值/诊断/机制/案例卡（不足 5 张时自动回退到通用，可附场景手册）。拟推广方案时首选。",
         "inputSchema": {"type": "object", "properties": {"product_type": {"type": "string", "enum": ["标品", "非标品", "半标品", "通用"]},
                                                          "lifecycle_stage": {"type": "string", "enum": ["立项期", "新品期D0-30", "成长期D30-90", "成熟期D90+", "衰退清仓", "断货重启", "通用"]},
                                                          "scene": {"type": "string", "enum": ["新品首推", "断货重推", "老品反超", "排名恢复", "换链接重上", "旺季大促", "淡季维持", "通用"]},
                                                          "include_handbook": {"type": "boolean", "default": True}},
                         "required": ["product_type", "lifecycle_stage", "scene"]}},
        {"name": "kb_diagnose", "description": "按症状（如「老品流量下滑」「高点击低转化」「断货后掉排名」）找诊断卡、阈值卡与同类 case_study。推广报表诊断时用。",
         "inputSchema": {"type": "object", "properties": {"symptom": {"type": "string"}, "limit": {"type": "integer", "default": 8}}, "required": ["symptom"]}},
        {"name": "kb_playbook", "description": "读场景手册全文。现有：SC-断货重推 / SC-老品反超 / SC-排名恢复（可传 scene 中文名、手册 id，或用 lifecycle_stage 模糊匹配）。",
         "inputSchema": {"type": "object", "properties": {"scene": {"type": "string", "enum": ["新品首推", "断货重推", "老品反超", "排名恢复", "换链接重上", "旺季大促", "淡季维持"]},
                                                          "lifecycle_stage": {"type": "string"}, "pb_id": {"type": "string", "description": "如 SC-断货重推"}}}},
        {"name": "kb_products", "description": "列出 kb_manifest 登记的产品线（ASIN/阶段/目录/目录下文件），用于定位某产品的词包/快照/复盘日志。",
         "inputSchema": {"type": "object", "properties": {}}},
        {"name": "kb_record_observation", "description": "学习闭环回写：把账户实测与知识卡结论的一致/相悖写进 gaps.yaml（不直接改卡）。推广跑数复盘后调用。",
         "inputSchema": {"type": "object", "properties": {"topic": {"type": "string", "description": "大纲主题 ID，如 restock_relaunch"}, "observation": {"type": "string"},
                                                          "run_id": {"type": "string"}, "card_id": {"type": "string"}, "product_id": {"type": "string"},
                                                          "metrics": {"type": "object", "description": "可选：clicks/orders/cvr/acos 等实测数"}},
                         "required": ["topic", "observation"]}},
        {"name": "kb_list_gaps", "description": "列出知识库开放问题/待消化观察。", "inputSchema": {"type": "object", "properties": {"status": {"type": "string", "enum": ["open", "closed"]}}}},
    ]


def call_tool(store: Store, name: str, args: dict) -> dict:
    if name == "kb_status":
        return store.status()
    if name == "kb_query":
        return {"count": None, "cards": store.query(args.get("tags"), args.get("text"), args.get("knowledge_type"), float(args.get("min_confidence") or 0), int(args.get("limit") or 10))}
    if name == "kb_get_card":
        c = store.get_card(str(args.get("id") or ""))
        return c or {"error": f"卡 {args.get('id')} 不存在"}
    if name == "kb_scenario_rules":
        return store.scenario_rules(args["product_type"], args["lifecycle_stage"], args["scene"], bool(args.get("include_handbook", True)))
    if name == "kb_diagnose":
        return store.diagnose(str(args.get("symptom") or ""), int(args.get("limit") or 8))
    if name == "kb_playbook":
        pb = store.playbook(args.get("scene"), args.get("lifecycle_stage"), args.get("pb_id"))
        return pb or {"error": "该场景尚无手册（现有：SC-断货重推 / SC-老品反超 / SC-排名恢复）"}
    if name == "kb_products":
        return {"products": store.products()}
    if name == "kb_record_observation":
        return store.record_observation(args["topic"], args["observation"], args.get("run_id"), args.get("card_id"), args.get("product_id"), args.get("metrics"))
    if name == "kb_list_gaps":
        return {"gaps": store.list_gaps(args.get("status"))}
    raise KeyError(name)


# ------------------------------------------------------------------ JSON-RPC / stdio
def respond(obj: dict) -> None:
    sys.stdout.write(KB.jdumps(obj) + "\n")
    sys.stdout.flush()


def main() -> int:
    ap = argparse.ArgumentParser(description="产品推广知识库 MCP 服务（stdio）")
    ap.add_argument("--kb", default=os.environ.get("PROMO_KB_ROOT"), help="知识库根目录（或环境变量 PROMO_KB_ROOT）")
    a = ap.parse_args()
    if not a.kb:
        log("缺少 --kb 或 PROMO_KB_ROOT")
        return 2
    try:
        store = Store(Path(a.kb).resolve())
        follower = kb_follow.Follower(code_path=__file__, server_version=SERVER_VERSION)
    except SystemExit as e:
        log(str(e))
        return 2
    log(f"ready kb={store.kb}")
    if hasattr(sys.stdin, "reconfigure"):
        sys.stdin.reconfigure(encoding="utf-8")
        sys.stdout.reconfigure(encoding="utf-8")

    for raw in sys.stdin:
        raw = raw.strip()
        if not raw:
            continue
        try:
            msg = json.loads(raw)
        except json.JSONDecodeError:
            respond({"jsonrpc": "2.0", "id": None, "error": {"code": -32700, "message": "Parse error"}})
            continue
        mid = msg.get("id")
        method = msg.get("method")
        params = msg.get("params") or {}
        if method is None:
            continue  # 客户端的响应，不处理
        if mid is None:  # notification
            if method == "notifications/initialized":
                log("client initialized")
            continue
        try:
            if method == "initialize":
                want = str(params.get("protocolVersion") or PROTOCOL_VERSIONS[0])
                ver = want if want in PROTOCOL_VERSIONS else PROTOCOL_VERSIONS[0]
                respond({"jsonrpc": "2.0", "id": mid, "result": {"protocolVersion": ver, "capabilities": {"tools": {"listChanged": False}},
                                                                 "serverInfo": {"name": SERVER_NAME, "version": SERVER_VERSION},
                                                                 "instructions": "产品推广知识库（宠物棉签线/收纳罐线）。先 kb_status；拟方案用 kb_scenario_rules（产品类型×生命周期×场景）；找依据用 kb_query；诊断用 kb_diagnose；手册用 kb_playbook；产品定位用 kb_products；跑数后 kb_record_observation 回写。阈值为候选口径，用户利润账与合规优先。"}})
            elif method == "ping":
                respond({"jsonrpc": "2.0", "id": mid, "result": {}})
            elif method == "tools/list":
                respond({"jsonrpc": "2.0", "id": mid, "result": {"tools": tool_defs()}})
            elif method == "tools/call":
                name = params.get("name")
                args = params.get("arguments") or {}
                try:
                    store = follower.apply(store, Store)
                    result = call_tool(store, name, args)
                    if name == "kb_status" and isinstance(result, dict):
                        result = {**result, "runtime": follower.describe()}
                    respond({"jsonrpc": "2.0", "id": mid, "result": {"content": [{"type": "text", "text": KB.jdumps(result, indent=1)}],
                                                                     "structuredContent": KB._jsonable(result) if isinstance(result, dict) else {"result": KB._jsonable(result)}, "isError": False}})
                except KeyError:
                    respond({"jsonrpc": "2.0", "id": mid, "error": {"code": -32602, "message": f"Unknown tool: {name}"}})
                except Exception as e:  # noqa: BLE001
                    respond({"jsonrpc": "2.0", "id": mid, "result": {"content": [{"type": "text", "text": f"tool error: {e}"}], "isError": True}})
            elif method in ("resources/list", "prompts/list"):
                respond({"jsonrpc": "2.0", "id": mid, "result": {"resources": [], "prompts": []}})
            else:
                respond({"jsonrpc": "2.0", "id": mid, "error": {"code": -32601, "message": f"Method not found: {method}"}})
        except Exception as e:  # noqa: BLE001
            respond({"jsonrpc": "2.0", "id": mid, "error": {"code": -32603, "message": str(e)[:300]}})
    return 0


if __name__ == "__main__":
    sys.exit(main())
