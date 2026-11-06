# -*- coding: utf-8 -*-
"""统一 finding 输出协议(40 号文档 P1 落地,协议字段对齐《数据驱动增长》一文)

每条异常 = 一个可执行对象:
  {id, date, domain, store, object, fact, status, reason_chain, impact, impact_amount,
   currency, action, priority, confidence, confidence_basis, rule, time_sensitive}

- 规则阈值:同目录上级 rules.yaml(load_rules 深合并默认值,改 yaml 不改代码)
- 落盘:报告根\\findings\\{日期}.json,逐日一文件(为 Phase2 持续性判定与 Phase3 留痕闭环打底)
- 幂等:save_findings 按 domain 替换写入,同一生产者重跑不产生重复
"""
import hashlib
import json
from datetime import date
from pathlib import Path

_HERE = Path(__file__).resolve().parent
RULES_FILE = _HERE.parent / "rules.yaml"

PRIORITY_ORDER = {"P1": 0, "P2": 1, "P3": 2}

DEFAULT_RULES = {
    "ads": {"acos_red": 0.35, "acos_yellow": 0.25, "min_spend_for_red": 5.0,
            "burn_min_spend_7d": 5.0, "bad_status_min_spend_7d": 1.0,
            "bad_status_keywords": ["OUT_OF_BUDGET", "SUSPENDED", "INCOMPLETE", "MISSING_"]},
    "sales": {"drop_pct_warn": 0.40, "min_amount_base": 50},
    "family": {"price_change_pct": 0.10, "star_drop": 0.1, "review_jump": 3},
    "inventory": {"red_per_sku": True, "aggregate_others": True},
    "priority": {"p1_min_impact": 20, "p2_min_impact": 5},
}


def load_rules():
    merged = json.loads(json.dumps(DEFAULT_RULES))
    try:
        import yaml
        user = yaml.safe_load(RULES_FILE.read_text(encoding="utf-8")) or {}
        for k, v in user.items():
            if isinstance(v, dict) and isinstance(merged.get(k), dict):
                merged[k].update(v)
            else:
                merged[k] = v
    except FileNotFoundError:
        pass
    return merged


def compute_priority(impact_amount, confidence, time_sensitive, rules):
    pr = rules["priority"]
    amt = impact_amount or 0
    if time_sensitive and confidence != "低":
        return "P1"
    if amt >= pr["p1_min_impact"] and confidence == "高":
        return "P1"
    if amt >= pr["p2_min_impact"]:
        return "P2"
    if confidence == "低":
        return "P3"
    return "P3" if amt < pr["p2_min_impact"] else "P2"


def make_finding(domain, store, object_, fact, status, reason_chain, impact,
                 impact_amount, action, confidence, confidence_basis, rule,
                 currency="", time_sensitive=False, rules=None,
                 marketplace="", native_id="", store_key=""):
    """status: normal/watch/warning/critical;confidence: 高/中/低。
    ID 必须含店铺 / 站点 / 原生 ID，避免同名活动串店。"""
    rules = rules or load_rules()
    store_key = store_key or store
    identity = "|".join([
        str(store_key or ""),
        str(marketplace or ""),
        str(native_id or object_ or ""),
        str(rule or ""),
    ])
    fid = "%s:%s:%s" % (domain, rule, hashlib.md5(identity.encode("utf-8")).hexdigest()[:10])
    return {
        "id": fid, "date": date.today().isoformat(),
        "domain": domain, "store": store, "store_key": store_key,
        "marketplace": marketplace or "",
        "native_id": native_id or None,
        "object": object_,
        "fact": fact, "status": status,
        "reason_chain": reason_chain or [],
        "impact": impact, "impact_amount": round(impact_amount, 2) if impact_amount else None,
        "currency": currency,
        "action": action,
        "priority": compute_priority(impact_amount, confidence, time_sensitive, rules),
        "confidence": confidence, "confidence_basis": confidence_basis,
        "rule": rule, "time_sensitive": bool(time_sensitive),
    }


def findings_path(report_root, day=None):
    d = (day or date.today()).isoformat()
    p = Path(report_root) / "findings"
    p.mkdir(parents=True, exist_ok=True)
    return p / f"{d}.json"


def save_findings(report_root, domains, new_findings, day=None, replace_stores=None):
    """按 domain 替换写入(幂等):同域旧条目清掉再并入;全量按 P1>P2>P3、影响金额降序排。
    分站点定时任务传入 replace_stores，只替换这些店铺的同域条目，避免英国早卡被美国下午卡覆盖。"""
    f = findings_path(report_root, day)
    old = []
    if f.exists():
        try:
            old = json.loads(f.read_text(encoding="utf-8")).get("findings", [])
        except Exception:
            old = []
    domain_set = set(domains)
    if replace_stores:
        store_set = set(replace_stores)
        keep = [x for x in old if not (x.get("domain") in domain_set and x.get("store") in store_set)]
    else:
        keep = [x for x in old if x.get("domain") not in domain_set]
    merged = {x["id"]: x for x in keep}
    for x in new_findings:
        merged[x["id"]] = x
    ordered = sorted(merged.values(),
                     key=lambda x: (PRIORITY_ORDER.get(x.get("priority"), 3),
                                    -(x.get("impact_amount") or 0)))
    f.write_text(json.dumps(
        {"date": (day or date.today()).isoformat(),
         "counts": {p: sum(1 for x in ordered if x.get("priority") == p)
                    for p in ("P1", "P2", "P3")},
         "findings": ordered},
        ensure_ascii=False, indent=1), encoding="utf-8")
    return f


def load_findings(report_root, day=None):
    f = findings_path(report_root, day)
    if not f.exists():
        return None
    try:
        return json.loads(f.read_text(encoding="utf-8"))
    except Exception:
        return None
