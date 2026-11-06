# -*- coding: utf-8 -*-
"""领星 OpenAPI 轻量客户端（店铺巡检数据后端）

用法:
  python lingxing_client.py --smoke          # 冒烟测试: 取 token + 拉店铺列表
  python lingxing_client.py --get <路径> [--params '{"k":"v"}']

依赖: requests, pycryptodome, pyyaml（1688-Scraper-MCP 的 .venv 已齐备）
签名算法已对照官方 Python SDK（apidoc.lingxing.com openapi-python3-sdk-20230419）逐行核对（2026-07-06）：
  排序拼接（空串跳过/null 参与）-> MD5 大写 -> AES/ECB/PKCS5(密钥=appId 原串) -> Base64。
凭据安全：本文件不打印 AppSecret / token 全文。
"""
import argparse
import base64
import hashlib
import json
import os
import sys
import time
from pathlib import Path

import requests
import yaml
from Crypto.Cipher import AES

SKILL_DIR = Path(__file__).resolve().parent.parent
CONFIG = SKILL_DIR / "lingxing_config.yaml"

# 领星是国内服务且有 IP 白名单——必须直连，忽略系统代理环境变量，
# 保证出口 IP 恒等于开放后台白名单里登记的真实出口 IP
SESSION = requests.Session()
SESSION.trust_env = False

# 常用接口路径与 HTTP 方法（✅ = 2026-07-06 用真实凭据实测 code=0 通过，必填参数见行尾注释）
ENDPOINTS = {
    "seller_lists": ("GET", "/erp/sc/data/seller/lists"),               # ✅ 店铺列表(冒烟)，无必填业务参数
    "fba_stock": ("POST", "/erp/sc/routing/fba/fbaStock/fbaList"),      # ✅ FBA 库存(断货红线核心：含可售天数/库龄/健康状态/补货建议)
    "asin_daily_sales": ("POST", "/erp/sc/data/sales_report/asinDailyLists"),  # ✅ ASIN 日销量；必填 sid + event_date("Y-m-d")，缺 event_date 报"时间不能为空"
    "listing": ("POST", "/erp/sc/data/mws/listing"),                   # ✅ Listing；必填 sid，配 offset/length 分页；含排名/评论数/星级/7-30天销量
    "orders": ("POST", "/erp/sc/data/mws/orders"),                     # ✅ 订单；必填 sid + start_date + end_date，配 offset/length；缺日期报笼统 500
    # 广告族（2026-07-06 实测 code=0；参数同 orders：sid + start_date + end_date + offset/length）
    "ads_campaigns": ("POST", "/erp/sc/data/ads/campaigns"),           # ✅ 广告活动：花费/ACOS/CTR/CPC/CVR/订单/销售额/serving_status(含 ACCOUNT_OUT_OF_BUDGET 红线)
    "ads_ad_groups": ("POST", "/erp/sc/data/ads/adGroups"),            # ✅ 广告组
    "ads_keywords": ("POST", "/erp/sc/data/ads/keywords"),             # ✅ 关键词
    "ads_targets": ("POST", "/erp/sc/data/ads/targets"),               # ✅ 投放对象(自动广告)
    "ads_query_words": ("POST", "/erp/sc/data/ads/queryWords"),        # ✅ 用户搜索词(否词/拓词分析)
    # "/erp/sc/data/ads/products" 路径存在但报 102 参数不合法，用时到 apidoc 核对必填参数
}


def load_config():
    if not CONFIG.exists():
        sys.exit("未找到 lingxing_config.yaml —— 请按 references/领星接入清单 完成接入后再试")
    cfg = yaml.safe_load(CONFIG.read_text(encoding="utf-8"))["lingxing"]
    if not cfg.get("app_id") or not cfg.get("app_secret"):
        sys.exit("lingxing_config.yaml 中 app_id/app_secret 为空")
    return cfg


def load_stores():
    """stores 在 yaml 顶层，与 lingxing 平级——巡检/报告脚本用它拿 sid→店铺映射"""
    if not CONFIG.exists():
        return []
    return yaml.safe_load(CONFIG.read_text(encoding="utf-8")).get("stores") or []


# 站点当地时区：用于「今天推昨天」——取该站点已收盘的最近完整自然日。
# 北京 07:05 ≈ 英国站刚过零点（夏令时）；北京 15:05 ≈ 美西刚过零点（夏令时）。
MARKETPLACE_TZ = {
    "UK": "Europe/London",
    "GB": "Europe/London",
    "US": "America/Los_Angeles",
}
MARKETPLACE_LABEL = {"UK": "英国站", "US": "美国站"}


def normalize_marketplace(value):
    raw = str(value or "").strip().upper()
    if raw in ("UK", "GB", "UNITED KINGDOM", "英国", "英国站"):
        return "UK"
    if raw in ("US", "USA", "UNITED STATES", "美国", "美国站"):
        return "US"
    return raw


def marketplace_label(mp):
    return MARKETPLACE_LABEL.get(normalize_marketplace(mp), mp or "")


def marketplace_tz_name(mp):
    return MARKETPLACE_TZ.get(normalize_marketplace(mp), "Asia/Shanghai")


def closed_pulse_day(marketplace, days_back=1):
    """站点当地已收盘的最近完整自然日（默认昨天）。Windows 缺 tzdata 时回退北京日历。"""
    mp = normalize_marketplace(marketplace)
    from datetime import datetime, timedelta as _td
    try:
        from zoneinfo import ZoneInfo
        today_local = datetime.now(ZoneInfo(marketplace_tz_name(mp))).date()
        return today_local - _td(days=days_back)
    except Exception:
        from datetime import date as _date
        return _date.today() - _td(days=days_back)


def patrol_stores(marketplace=None):
    stores = [s for s in load_stores() if s.get("patrol")]
    if not marketplace:
        return stores
    mp = normalize_marketplace(marketplace)
    matched = [s for s in stores if normalize_marketplace(s.get("marketplace")) == mp]
    return matched


def store_nicknames(marketplace):
    return [s.get("nickname") or str(s.get("sid")) for s in patrol_stores(marketplace)]


ERP_EXTRACT_REL = Path("S5_运营板块") / "lingxing-erp-connector" / "erp_extract"


def _find_product_root(start: Path):
    """从 start 往上找含 S5_运营板块/ 的产品根（Codex 打开的产品文件夹）。"""
    for p in [start, *start.parents]:
        if (p / "S5_运营板块").is_dir():
            return p
    return None


def report_root():
    """报告落盘根目录（按 skill_folder_map：S5_运营板块/lingxing-erp-connector/erp_extract/），四级优先：
    1. lingxing_config.yaml 顶层 report_root（显式指定，建议写产品根下的 erp_extract 绝对路径）
    2. 环境变量 STARSKY_PRODUCT_ROOT → {产品根}/S5_运营板块/lingxing-erp-connector/erp_extract
    3. 当前工作目录（或其上级）就是产品根 → 同上
    4. 兜底 ~/starsky_runs/店铺巡检/（不再写死 D 盘；命中兜底会提示去配 report_root）"""
    if CONFIG.exists():
        custom = (yaml.safe_load(CONFIG.read_text(encoding="utf-8")) or {}).get("report_root")
        if custom:
            return Path(str(custom)).expanduser()
    ops_root = os.environ.get("STARSKY_OPS_ROOT", "").strip()
    if ops_root:
        return Path(ops_root).expanduser() / ERP_EXTRACT_REL
    env_root = os.environ.get("STARSKY_PRODUCT_ROOT", "").strip()
    if env_root:
        return Path(env_root).expanduser() / ERP_EXTRACT_REL
    found = _find_product_root(Path.cwd())
    if found:
        return found / ERP_EXTRACT_REL
    fallback = Path.home() / "starsky_runs" / "店铺巡检"
    print(f"[提示] 未配置 report_root，报告落到兜底目录 {fallback}；"
          f"建议在 lingxing_config.yaml 写 report_root 指向产品根下的 {ERP_EXTRACT_REL}")
    return fallback


def get_token(cfg):
    url = f"{cfg['base_url']}/api/auth-server/oauth/access-token"
    r = SESSION.post(url, params={"appId": cfg["app_id"], "appSecret": cfg["app_secret"]}, timeout=30)
    if r.status_code == 403:
        sys.exit("403 IP 不在白名单（领星返回: %s）——去领星开放后台把本机直连出口 IP 加入白名单。"
                 "查真实出口 IP 用国内服务: curl.exe --noproxy \"*\" http://ip.3322.net"
                 "（勿用 ip.sb，本机代理会导致查到国外出口）" % r.text.strip()[:120])
    r.raise_for_status()
    data = r.json()
    if str(data.get("code")) not in ("200", "0"):
        sys.exit(f"取 token 失败: code={data.get('code')} msg={data.get('msg')}")
    tok = data["data"]["access_token"]
    print(f"[OK] access_token 获取成功（{tok[:6]}...，有效期 {data['data'].get('expires_in')}s）")
    return tok


def _aes_ecb_encrypt(text: str, key: str) -> str:
    # 官方 SDK：appId 原串直接作密钥（不截断不补位），长度须为 16/24/32 字节
    k = key.encode("utf-8")
    if len(k) not in (16, 24, 32):
        sys.exit(f"appId 长度 {len(k)} 字节不是合法 AES 密钥长度(16/24/32)——请核对 app_id 是否复制完整")
    pad = 16 - len(text.encode()) % 16
    padded = text.encode() + bytes([pad]) * pad       # PKCS5/7
    return base64.b64encode(AES.new(k, AES.MODE_ECB).encrypt(padded)).decode()


def _sign(params: dict, app_id: str) -> str:
    items = []
    for k in sorted(params):
        v = params[k]
        if v == "":                                   # 官方规则：空串不参与签名（null 参与）
            continue
        if isinstance(v, bool):                       # 请求体里是 JSON 的 true / false，签名也要同一写法
            v = "true" if v else "false"
        elif isinstance(v, (dict, list)):
            # 对齐官方 SDK orjson.OPT_SORT_KEYS：键排序 + 紧凑 + 不转义非 ASCII
            v = json.dumps(v, separators=(",", ":"), ensure_ascii=False, sort_keys=True)
        items.append(f"{k}={v}")
    md5_up = hashlib.md5("&".join(items).encode()).hexdigest().upper()
    return _aes_ecb_encrypt(md5_up, app_id)


def _normalize_path(path: str) -> str:
    """对 Git Bash(MSYS) 路径转换免疫：/erp/... 会被转成 C:/Users/.../erp/... 或
    /C:/Program Files/Git/erp/...，这里把真实接口路径抠回来（接口路径恒以 /erp/ 或 /api/ 开头）"""
    import re
    m = re.search(r"/(erp|api)/.*$", path.replace("\\", "/"))
    if m:
        return m.group(0)
    return path if path.startswith("/") else "/" + path


def call(cfg, token, path, biz_params=None, method="POST"):
    """公共参数走 query，业务参数 GET 走 query / POST 走 json body；签名覆盖两者（官方 SDK 行为）"""
    path = _normalize_path(path)
    common = {
        "app_key": cfg["app_id"],
        "access_token": token,
        "timestamp": str(int(time.time())),
    }
    sign_src = dict(biz_params or {})
    sign_src.update(common)
    common["sign"] = _sign(sign_src, cfg["app_id"])   # requests 会自动对 sign 做 URL 编码
    url = f"{cfg['base_url']}{path}"
    if method.upper() == "GET":
        q = dict(biz_params or {}); q.update(common)
        r = SESSION.get(url, params=q, timeout=60)
    else:
        # 请求体必须和签名用的是同一串 JSON（键排序、紧凑、不转义中文）；数组 / 对象参数（写接口）不一致会报 api sign not correct
        body = json.dumps(biz_params or {}, separators=(",", ":"), ensure_ascii=False, sort_keys=True).encode("utf-8")
        r = SESSION.post(url, params=common, data=body, headers={"Content-Type": "application/json"}, timeout=60)
    r.raise_for_status()
    return r.json()


def smoke():
    cfg = load_config()
    token = get_token(cfg)
    method, path = ENDPOINTS["seller_lists"]
    data = call(cfg, token, path, method=method)
    if str(data.get("code")) not in ("200", "0"):
        sys.exit(f"店铺列表失败: code={data.get('code')} msg={data.get('msg')}（若为签名错误，按 apidoc 修正 _sign）")
    sellers = data.get("data") or []
    print(f"[OK] 店铺列表: {len(sellers)} 个店铺")
    for s in sellers[:10]:
        print(f"  sid={s.get('sid')} | {s.get('name') or s.get('seller_name')} | {s.get('marketplace') or s.get('country')}")
    print("冒烟测试通过 —— 可以把 sid 填进 lingxing_config.yaml 的 stores 映射了")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--smoke", action="store_true")
    ap.add_argument("--get", help="接口路径，如 /erp/sc/data/seller/lists")
    ap.add_argument("--params", help='业务参数 JSON，如 {"sid":1}')
    ap.add_argument("--method", default="GET", help="--get 时的 HTTP 方法，默认 GET；报文档要求 POST 的接口传 POST")
    a = ap.parse_args()
    if a.smoke:
        smoke()
    elif a.get:
        cfg = load_config()
        tok = get_token(cfg)
        out = call(cfg, tok, a.get, json.loads(a.params) if a.params else None, method=a.method)
        print(json.dumps(out, ensure_ascii=False, indent=2)[:3000])
    else:
        ap.print_help()
