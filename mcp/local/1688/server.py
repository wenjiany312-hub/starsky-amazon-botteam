#!/usr/bin/env python3
"""Local 1688 offer MCP (optional add-on).

Opens its own Chromium, keeps login in ./pw_user_data, writes HTML/JSON to disk.
Works with DPH (DeepSeek Harness) and Codex. Callers pass offer_id digits only;
do not open a 1688 URL in the chat browser.
"""

from __future__ import annotations

import json
import os
import re
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any
from urllib.parse import urlparse
from urllib.request import Request, urlopen

UA = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36"
DETAIL_SRC_RE = re.compile(
    r"""(?:src|data-src|data-lazyload)\s*=\s*\\?["']([^"'\\]+)""",
    re.IGNORECASE,
)
DETAIL_BARE_RE = re.compile(
    r"https?://[^\"'\\\s>]+\.(?:jpg|jpeg|png|webp)",
    re.IGNORECASE,
)

from mcp.server.fastmcp import FastMCP

ROOT = Path(__file__).resolve().parent
USER_DATA = ROOT / "pw_user_data"
DEFAULT_OUT = ROOT / "dumps"
# Optional anchor for relative save_dir (set in the MCP env config if desired).
WORKSPACE_ROOT = os.environ.get("SALON_WORKSPACE_ROOT", "").strip()


def _resolve_save_dir(save_dir: str, default: Path) -> tuple[Path, str]:
    """Resolve save_dir; warn when a relative path lands inside the MCP dir.

    The MCP process cwd is its own install dir, not the chat workspace. A
    relative save_dir therefore writes media where the user will never look.
    """
    if not save_dir:
        return default, ""
    p = Path(save_dir).expanduser()
    warning = ""
    if not p.is_absolute():
        base = Path(WORKSPACE_ROOT) if WORKSPACE_ROOT else Path.cwd()
        p = (base / p).resolve()
        if not WORKSPACE_ROOT:
            try:
                inside_mcp = p == ROOT or ROOT in p.parents
            except Exception:
                inside_mcp = False
            if inside_mcp:
                warning = (
                    "save_dir was RELATIVE and resolved inside the MCP install dir, "
                    "not your workspace. Files are at: " + str(p) + " . "
                    "Pass an ABSOLUTE workspace path next time, e.g. "
                    "D:/.../product_runs/{run}/产品资料, or move these files there."
                )
    return p, warning

mcp = FastMCP("1688_scraper")

_playwright = None
_context = None
_browser_executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="1688-playwright")


def _ok(payload: dict[str, Any]) -> str:
    return json.dumps(payload, ensure_ascii=False)


def _compact_capture(path, callback):
    """Keep source HTML/JSON in the step receipt, media at its original location."""
    scripts = ROOT.parents[1] / 'scripts'
    helper = scripts / 'r01_native.py'
    if not helper.is_file():
        return NotImplemented
    previous = list(sys.path)
    try:
        sys.path.insert(0, str(scripts))
        from r01_native import find_case, execute_native
        root = find_case(path)
        if root is None:
            return NotImplemented
        result = execute_native(root, lambda view: callback(view, root), step=5)
        return result if isinstance(result, str) else _ok(result)
    finally:
        sys.path[:] = previous


def _normalize_offer(offer_id: str = "", url: str = "") -> tuple[str, str] | tuple[None, str]:
    raw = (offer_id or "").strip() or (url or "").strip()
    if not raw:
        return None, "missing_offer"
    digits = re.search(r"(\d{8,})", raw)
    if not digits:
        return None, "invalid_offer"
    oid = digits.group(1)
    return oid, f"https://detail.1688.com/offer/{oid}.html"


_context_closed = {"v": False}


def _shutdown_browser() -> None:
    """Drop the current Chromium so the next call relaunches a fresh one."""
    global _playwright, _context
    if _context is not None:
        try:
            _context.close()
        except Exception:
            pass
    _context = None
    if _playwright is not None:
        try:
            _playwright.stop()
        except Exception:
            pass
    _playwright = None
    _context_closed["v"] = False


def _clear_profile_locks() -> None:
    """Remove stale Chromium singleton locks left by a crashed/killed browser."""
    for name in ("lockfile", "SingletonLock", "SingletonCookie", "SingletonSocket"):
        try:
            p = USER_DATA / name
            if p.exists() or p.is_symlink():
                p.unlink()
        except Exception:
            pass


def _ensure_browser():
    global _playwright, _context
    from playwright.sync_api import sync_playwright

    if _context is not None:
        alive = False
        try:
            _ = _context.pages
            alive = not _context_closed["v"]
        except Exception:
            alive = False
        if alive:
            return _context
        _shutdown_browser()

    USER_DATA.mkdir(parents=True, exist_ok=True)
    _clear_profile_locks()
    _playwright = sync_playwright().start()
    _context = _playwright.chromium.launch_persistent_context(
        user_data_dir=str(USER_DATA),
        headless=False,
        viewport={"width": 1440, "height": 960},
        locale="zh-CN",
        args=[
            "--disable-blink-features=AutomationControlled",
            "--disable-infobars",
        ],
    )
    _context_closed["v"] = False
    # User closing the window must not poison later calls: mark and relaunch next time.
    try:
        _context.on("close", lambda *_: _context_closed.__setitem__("v", True))
    except Exception:
        pass
    return _context


def _get_page():
    """Return a live page; if the user closed Chromium, relaunch it once."""
    last_exc: Exception | None = None
    for _ in range(2):
        context = _ensure_browser()
        try:
            page = None
            for p in context.pages:
                try:
                    if not p.is_closed():
                        page = p
                        break
                except Exception:
                    continue
            if page is None:
                page = context.new_page()
            return page
        except Exception as exc:
            last_exc = exc
            _shutdown_browser()
    raise RuntimeError(f"browser_unavailable: {last_exc}")


def _goto(page, url: str, timeout: int = 45000):
    """goto with one automatic relaunch if the browser died mid-session."""
    try:
        page.goto(url, wait_until="domcontentloaded", timeout=timeout)
        return page
    except Exception:
        _shutdown_browser()
        page = _get_page()
        page.goto(url, wait_until="domcontentloaded", timeout=timeout)
        return page


def _page_status(page) -> str:
    href = (page.url or "").lower()
    title = ""
    try:
        title = page.title() or ""
    except Exception:
        pass
    html_head = ""
    try:
        html_head = page.content()[:4000]
    except Exception:
        pass
    blob = f"{href}\n{title}\n{html_head}"
    if "_____tmd_____/punish" in blob or "punish" in href:
        return "punish"
    if "login.1688.com" in href or "login.taobao.com" in href or "passport.alibaba.com" in href:
        return "login"
    if any(k in blob for k in ("滑块", "安全验证", "请完成验证", "验证码拦截")):
        return "verification"
    if "detail.1688.com/offer" in href:
        return "offer"
    return "other"


def _extract_context(page) -> Any:
    return page.evaluate(
        """() => {
            try {
                if (window.context && window.context.result) {
                    return window.context.result.data || window.context.result;
                }
            } catch (e) {}
            return null;
        }"""
    )


def _pick(data: Any, path: list[str], default=None):
    cur = data
    for key in path:
        if not isinstance(cur, dict) or key not in cur:
            return default
        cur = cur[key]
    return cur


def _summarize(offer_id: str, data: Any) -> dict[str, Any]:
    if not isinstance(data, dict):
        return {"offer_id": offer_id, "has_context": False}
    gallery = _pick(data, ["gallery", "fields"], {}) or {}
    price = _pick(data, ["mainPrice", "fields", "finalPriceModel", "tradeWithoutPromotion"], {}) or {}
    title_fields = _pick(data, ["productTitle", "fields"], {}) or {}
    shop = _pick(title_fields, ["shopInfo"], {}) or {}
    temp = _pick(data, ["Root", "fields", "dataJson", "tempModel"], {}) or {}
    sku_model = _pick(data, ["Root", "fields", "dataJson", "skuModel"], {}) or {}
    sku_map = sku_model.get("skuInfoMap") if isinstance(sku_model, dict) else {}
    imgs = gallery.get("offerImgList") or gallery.get("mainImage") or []
    return {
        "offer_id": offer_id,
        "has_context": True,
        "title": gallery.get("subject") or title_fields.get("subject") or "",
        "price_display": price.get("offerPriceDisplay"),
        "min_price": price.get("offerMinPrice"),
        "max_price": price.get("offerMaxPrice"),
        "begin_amount": price.get("offerBeginAmount"),
        "company": temp.get("companyName") or shop.get("companyName"),
        "seller": temp.get("sellerLoginId"),
        "sale_num": title_fields.get("saleNum") or temp.get("saledCount"),
        "sku_count": len(sku_map) if isinstance(sku_map, dict) else 0,
        "image_count": len(imgs) if isinstance(imgs, list) else 0,
        "location": _pick(data, ["shippingServices", "fields", "location"]),
        "detail_url": _pick(data, ["description", "fields", "detailUrl"]),
    }


def _abs_url(url: str) -> str:
    url = (url or "").strip()
    if url.startswith("//"):
        return "https:" + url
    return url


def _http_get(url: str, timeout: int = 30) -> bytes:
    req = Request(
        url,
        headers={"User-Agent": UA, "Referer": "https://detail.1688.com/"},
    )
    with urlopen(req, timeout=timeout) as resp:
        return resp.read()


def _extract_detail_images(html: str) -> list[str]:
    urls: list[str] = []
    seen: set[str] = set()
    raw_hits = DETAIL_SRC_RE.findall(html or "") or DETAIL_BARE_RE.findall(html or "")
    for raw in raw_hits:
        url = _abs_url(raw.replace("\\/", "/"))
        if not url.startswith("http"):
            continue
        low = url.lower()
        if not any(low.endswith(ext) for ext in (".jpg", ".jpeg", ".png", ".webp")):
            continue
        if any(skip in low for skip in ("tbvideo", "/tps/", "spaceball")):
            continue
        if url in seen:
            continue
        seen.add(url)
        urls.append(url)
    return urls


def _resolve_detail_html(data: Any, save_dir: Path | None = None) -> tuple[str, list[str], str]:
    """Fetch description.fields.detailUrl and return (html, image_urls, path_or_empty)."""
    detail_url = _pick(data, ["description", "fields", "detailUrl"]) or ""
    if not isinstance(detail_url, str) or not detail_url.startswith("http"):
        return "", [], ""
    dest = (save_dir / "1688_detail.html") if save_dir else None
    html = ""
    if dest and dest.exists() and dest.stat().st_size > 20:
        html = dest.read_text(encoding="utf-8", errors="replace")
    else:
        try:
            html = _http_get(detail_url).decode("utf-8", errors="replace")
        except Exception:
            return "", [], ""
        if dest:
            dest.write_text(html, encoding="utf-8")
    imgs = _extract_detail_images(html)
    if dest:
        (save_dir / "1688_detail_images.json").write_text(
            json.dumps({"detail_url": detail_url, "count": len(imgs), "urls": imgs}, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
    return html, imgs, str(dest) if dest else ""


def _collect_media_urls(data: Any, detail_urls: list[str] | None = None) -> list[dict[str, str]]:
    urls: list[dict[str, str]] = []
    seen: set[str] = set()

    def add(url: str, role: str) -> None:
        url = _abs_url(url)
        if not url or not isinstance(url, str):
            return
        if not url.startswith("http"):
            return
        if url in seen:
            return
        seen.add(url)
        urls.append({"url": url, "role": role})

    if not isinstance(data, dict):
        return urls
    gallery = _pick(data, ["gallery", "fields"], {}) or {}
    for u in gallery.get("mainImage") or []:
        add(u, "main")
    for u in gallery.get("offerImgList") or []:
        add(u, "gallery")
    video = gallery.get("video") or {}
    if isinstance(video, dict):
        add(video.get("videoUrl") or "", "video")
        add(video.get("coverUrl") or "", "video_cover")
    sku_model = _pick(data, ["Root", "fields", "dataJson", "skuModel"], {}) or {}
    sku_map = sku_model.get("skuInfoMap") if isinstance(sku_model, dict) else {}
    if isinstance(sku_map, dict):
        for sku in sku_map.values():
            if isinstance(sku, dict):
                add(sku.get("specImage") or sku.get("imageUrl") or "", "sku")
    for u in detail_urls or []:
        add(u, "detail")
    return urls


@mcp.tool()
def ping() -> str:
    """Check that the local 1688 MCP process is alive. Does not open any website."""
    return _ok(
        {
            "ok": True,
            "server": "1688_scraper",
            "version": "1.1.0",
            "tools": ["ping", "search_offers", "login_1688", "get_offer", "download_media"],
            "user_data": str(USER_DATA),
        }
    )


@mcp.tool()
def search_offers(keyword: str, max_results: int = 10, save_dir: str = "") -> str:
    """Search 1688 offers by keyword in the MCP's own Chromium; returns REAL offers.

    keyword: 中文或英文商品词，如 "车载杯座扩展器 可调节". 中文命中率更高。
    max_results: 1-30, default 10.
    save_dir: optional ABSOLUTE dir; saves the search page HTML there as evidence.
    Shortlist rows MUST come from this tool / Sorftime / a user-given link.
    Never invent offer ids: a made-up id redirects to a non-offer page and wastes the run.
    """
    return _browser_executor.submit(_search_offers_impl, keyword, max_results, save_dir).result()


def _search_offers_impl(keyword: str, max_results: int = 10, save_dir: str = "") -> str:
    destination, _warning = _resolve_save_dir(save_dir, DEFAULT_OUT)
    compact = _compact_capture(destination, lambda view, root: _search_offers_impl(
        keyword, max_results, str(view / destination.resolve().relative_to(root))))
    if compact is not NotImplemented:
        return compact
    from urllib.parse import quote

    kw = (keyword or "").strip()
    if not kw:
        return _ok({"ok": False, "error": "missing_keyword"})
    try:
        max_results = max(1, min(int(max_results or 10), 30))
    except Exception:
        max_results = 10

    page = _get_page()
    page = _goto(page, f"https://s.1688.com/selloffer/offer_search.htm?keywords={quote(kw)}&charset=utf8")
    time.sleep(3)
    status = _page_status(page)
    if status in {"login", "verification"}:
        return _ok(
            {
                "ok": False,
                "error": "auth_required",
                "page_status": status,
                "hint": "在 MCP Chromium 窗口完成登录/滑块后，再次调用 search_offers。",
            }
        )
    if status == "punish":
        return _ok(
            {
                "ok": False,
                "error": "risk_control_punish",
                "hint": "触发风控。不要连续重试；稍等或先人工在该窗口浏览后再试。",
            }
        )

    try:
        for _ in range(3):
            page.evaluate("window.scrollBy(0, document.body.scrollHeight)")
            time.sleep(1.2)
    except Exception:
        pass

    try:
        offers = page.evaluate(
            """() => {
                const out = [];
                const seen = new Set();
                const push = (id, a) => {
                    if (!id || seen.has(id)) return;
                    let card = a;
                    for (let i = 0; i < 7 && card.parentElement; i++) {
                        card = card.parentElement;
                        if ((card.innerText || '').length > 60) break;
                    }
                    const text = (card.innerText || '').replace(/\\s+/g, ' ').slice(0, 260);
                    let title = ((a.getAttribute('title') || a.innerText || '').trim()).replace(/\\s+/g, ' ');
                    if (!title || title.length < 4) title = text.slice(0, 80);
                    const priceMatch = text.match(/[¥￥]\\s*([0-9]+(?:\\.[0-9]+)?)/);
                    seen.add(id);
                    out.push({
                        offer_id: id,
                        url: 'https://detail.1688.com/offer/' + id + '.html',
                        title: title.slice(0, 120),
                        price_hint: priceMatch ? priceMatch[1] : '',
                        card_text: text,
                    });
                };
                // 老版桌面链接
                for (const a of document.querySelectorAll('a[href*="detail.1688.com/offer/"]')) {
                    const m = (a.href || '').match(/offer\\/(\\d{8,})\\.html/);
                    if (m) push(m[1], a);
                }
                // 新版搜索页用移动端详情链接 detail.m.1688.com/page/index.html?offerId=...
                for (const a of document.querySelectorAll('a[href*="detail.m.1688.com"]')) {
                    const m = (a.href || '').match(/[?&]offerId=(\\d{8,})/);
                    if (m) push(m[1], a);
                }
                if (out.length === 0) {
                    for (const a of document.querySelectorAll('a[href*="offerId="]')) {
                        const m = (a.href || '').match(/[?&]offerId=(\\d{8,})/);
                        if (m) push(m[1], a);
                    }
                }
                return out;
            }"""
        ) or []
    except Exception as exc:
        return _ok({"ok": False, "error": f"extract_failed: {exc}", "page_status": status})

    html_path = ""
    if save_dir:
        out_dir, _warn = _resolve_save_dir(save_dir, DEFAULT_OUT)
        out_dir.mkdir(parents=True, exist_ok=True)
        safe_kw = re.sub(r"[^\w\u4e00-\u9fff-]+", "_", kw)[:40]
        dest = out_dir / f"1688_search_{safe_kw}.html"
        try:
            dest.write_text(page.content(), encoding="utf-8")
            html_path = str(dest)
        except Exception:
            pass

    return _ok(
        {
            "ok": True,
            "keyword": kw,
            "page_status": status,
            "total_found_on_page": len(offers),
            "offers": offers[:max_results],
            "search_page_html": html_path,
            "hint": (
                "以上 offer 均来自真实搜索页。短名单只能引用这些 offer_id/URL"
                "（或 Sorftime、用户提供的链接），并注明来源；结果不足时如实上报，禁止补编。"
            ),
        }
    )


@mcp.tool()
def login_1688(wait_seconds: int = 0) -> str:
    """Open the 1688 login window using the MCP's own Chromium (not Codex Browser Use).

    If wait_seconds > 0, poll until login leaves the login page (max 180).
    Prefer wait_seconds=0: user logs in the popped window, then call get_offer.
    """
    return _browser_executor.submit(_login_1688_impl, wait_seconds).result()


def _login_1688_impl(wait_seconds: int = 0) -> str:
    """Run all Sync Playwright work on its dedicated owner thread."""
    wait_seconds = max(0, min(int(wait_seconds or 0), 180))
    page = _get_page()
    page = _goto(page, "https://login.1688.com/")
    status = _page_status(page)
    if wait_seconds:
        deadline = time.time() + wait_seconds
        while time.time() < deadline:
            status = _page_status(page)
            if status not in {"login", "verification"}:
                break
            time.sleep(2)
            try:
                page.reload(wait_until="domcontentloaded", timeout=20000)
            except Exception:
                pass
        status = _page_status(page)
    return _ok(
        {
            "status": status,
            "current_url_host": urlparse(page.url).netloc,
            "hint": "If status is login/verification, finish scan/slider in the Chromium window, then call get_offer.",
        }
    )


@mcp.tool()
def get_offer(offer_id: str, save_dir: str = "", wait_login_seconds: int = 0) -> str:
    """Open a 1688 offer by numeric id, dump window.context JSON + HTML.

    offer_id: digits only, e.g. 828851649557. Do not pass a full URL if you can avoid it.
    save_dir: ABSOLUTE workspace path (e.g. D:/.../product_runs/{run}/产品资料).
        A relative path resolves against the MCP install dir, NOT your workspace.
        Empty = MCP dumps/.
    wait_login_seconds: if the page is a login wall, wait this many seconds (0-180).
    """
    return _browser_executor.submit(
        _get_offer_impl, offer_id, save_dir, wait_login_seconds
    ).result()


def _get_offer_impl(offer_id: str, save_dir: str = "", wait_login_seconds: int = 0) -> str:
    """Run all Sync Playwright work on its dedicated owner thread."""
    oid, target = _normalize_offer(offer_id=offer_id)
    if oid is None:
        return _ok({"ok": False, "error": target})

    out_dir, dir_warning = _resolve_save_dir(save_dir, DEFAULT_OUT / oid)
    compact = _compact_capture(out_dir, lambda view, root: _get_offer_impl(
        offer_id, str(view / out_dir.resolve().relative_to(root)), wait_login_seconds))
    if compact is not NotImplemented:
        return compact
    out_dir.mkdir(parents=True, exist_ok=True)

    page = _get_page()
    page = _goto(page, target)
    time.sleep(3)
    status = _page_status(page)

    wait_login_seconds = max(0, min(int(wait_login_seconds or 0), 180))
    if status in {"login", "verification"} and wait_login_seconds:
        deadline = time.time() + wait_login_seconds
        while time.time() < deadline:
            status = _page_status(page)
            if status not in {"login", "verification"}:
                break
            time.sleep(2)
        if "detail.1688.com/offer" not in (page.url or ""):
            page = _goto(page, target)
            time.sleep(3)
        status = _page_status(page)

    if status == "punish":
        return _ok(
            {
                "ok": False,
                "error": "risk_control_punish",
                "offer_id": oid,
                "hint": "Stop. Do not retry this offer in the same browser. Login later in a fresh window.",
            }
        )
    if status in {"login", "verification"}:
        return _ok(
            {
                "ok": False,
                "error": "auth_required",
                "offer_id": oid,
                "page_status": status,
                "hint": "Complete login/slider in the MCP Chromium window, then call get_offer again.",
            }
        )
    if status == "other":
        # 1688 redirects dead/invalid offer ids to workbench/home pages instead of a detail page.
        final_url = page.url or ""
        debug_path = out_dir / f"1688_debug_{oid}.html"
        try:
            debug_path.write_text(page.content(), encoding="utf-8")
        except Exception:
            pass
        return _ok(
            {
                "ok": False,
                "error": "not_offer_page",
                "offer_id": oid,
                "final_url": final_url,
                "debug_html_path": str(debug_path),
                "hint": (
                    "目标商品页没有打开：1688 把该链接重定向到了非商品页，"
                    "这个 offer_id 很可能不存在、已下架，或是编造的。"
                    "不要重试本 ID；用 search_offers 拿真实 offer 列表，"
                    "或让用户给一条能打开的 detail.1688.com/offer/ 链接。"
                ),
            }
        )
    try:
        page_head = page.content()[:8000]
    except Exception:
        page_head = ""
    if any(marker in page_head for marker in ("商品找不到了", "该商品已下架", "宝贝已下架", "商品已失效")):
        return _ok(
            {
                "ok": False,
                "error": "offer_missing",
                "offer_id": oid,
                "final_url": page.url or "",
                "hint": "商品页提示已下架/找不到。换 search_offers 重新选品，不要重试本 ID。",
            }
        )

    try:
        page.evaluate("window.scrollTo(0, document.body.scrollHeight / 2)")
        time.sleep(1.2)
        page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
        time.sleep(1.5)
    except Exception:
        pass

    data = _extract_context(page)
    html = page.content()
    html_path = out_dir / "1688_source.html"
    json_path = out_dir / "1688_source.json"
    html_path.write_text(html, encoding="utf-8")
    json_path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")

    if not data:
        return _ok(
            {
                "ok": False,
                "error": "empty_context",
                "offer_id": oid,
                "page_status": status,
                "html_path": str(html_path),
                "html_bytes": html_path.stat().st_size,
                "hint": (
                    "已到达商品页但没拿到数据（可能是风控壳页或加载超时）。"
                    "只重试一次；再失败就换 search_offers 里的其他 offer，不要死磕。"
                ),
            }
        )

    _, detail_imgs, detail_html_path = _resolve_detail_html(data, out_dir)
    summary = _summarize(oid, data)
    summary.update(
        {
            "ok": True,
            "page_status": status,
            "save_dir_resolved": str(out_dir),
            "html_path": str(html_path),
            "json_path": str(json_path),
            "detail_html_path": detail_html_path,
            "html_bytes": html_path.stat().st_size,
            "json_bytes": json_path.stat().st_size,
            "detail_image_count": len(detail_imgs),
            "media_candidates": len(_collect_media_urls(data, detail_imgs)),
        }
    )
    if dir_warning:
        summary["warning"] = dir_warning
    return _ok(summary)


def _dest_for(out: Path, role: str, counters: dict[str, int], url: str) -> Path:
    ext = ".mp4" if "mp4" in url.lower() or role == "video" else ".jpg"
    if role in {"main", "gallery"}:
        counters["main"] += 1
        (out / "主图").mkdir(parents=True, exist_ok=True)
        return out / "主图" / f"main_{counters['main']:02d}{ext}"
    if role == "sku":
        counters["sku"] += 1
        (out / "SKU图").mkdir(parents=True, exist_ok=True)
        return out / "SKU图" / f"sku_{counters['sku']:02d}{ext}"
    if role == "video":
        (out / "视频").mkdir(parents=True, exist_ok=True)
        return out / "视频" / f"product_video{ext}"
    if role == "video_cover":
        (out / "视频").mkdir(parents=True, exist_ok=True)
        return out / "视频" / f"cover{ext}"
    counters["detail"] += 1
    (out / "详情图").mkdir(parents=True, exist_ok=True)
    return out / "详情图" / f"detail_{counters['detail']:02d}{ext}"


@mcp.tool()
def download_media(json_path: str, save_dir: str, max_files: int = 80) -> str:
    """Download offer media. Also fetches description.fields.detailUrl for real 详情图.

    save_dir: ABSOLUTE workspace path; relative paths resolve against the MCP
    install dir, NOT your workspace.
    Roles: main/gallery → 主图/; sku → SKU图/; detail → 详情图/;
    video → 视频/product_video.mp4; video_cover → 视频/cover.jpg（亚马逊传主图视频时要配的缩略图，供应商海报帧可直接用）.
    """
    src = Path(json_path).expanduser()
    destination, _warning = _resolve_save_dir(save_dir, src.parent)
    compact = _compact_capture(destination, lambda view, root: download_media(
        str(view / src.resolve().relative_to(root)),
        str(view / destination.resolve().relative_to(root)), max_files))
    if compact is not NotImplemented:
        return compact
    if not src.exists():
        return _ok({"ok": False, "error": "json_not_found", "json_path": str(src)})
    try:
        data = json.loads(src.read_text(encoding="utf-8"))
    except Exception as exc:
        return _ok({"ok": False, "error": f"json_parse: {exc}"})

    out, dir_warning = _resolve_save_dir(save_dir, src.parent)
    out.mkdir(parents=True, exist_ok=True)
    _, detail_imgs, detail_html_path = _resolve_detail_html(data, src.parent)
    items = _collect_media_urls(data, detail_imgs)
    cap = max(1, min(int(max_files or 80), 120))
    if len(items) > cap:
        preferred = [i for i in items if i["role"] in {"main", "detail", "video", "video_cover"}]
        rest = [i for i in items if i["role"] not in {"main", "detail", "video", "video_cover"}]
        items = (preferred + rest)[:cap]
    saved = []
    failed = []
    counters = {"main": 0, "sku": 0, "detail": 0}
    for item in items:
        url = item["url"]
        role = item["role"]
        dest = _dest_for(out, role, counters, url)
        try:
            dest.write_bytes(_http_get(url))
            saved.append({"role": role, "path": str(dest), "bytes": dest.stat().st_size})
        except Exception as exc:
            failed.append({"role": role, "url": url, "error": str(exc)})
    result = {
        "ok": True,
        "saved": len(saved),
        "failed": len(failed),
        "save_dir_resolved": str(out),
        "detail_html_path": detail_html_path,
        "detail_image_count": len(detail_imgs),
        "items": saved,
        "errors": failed[:8],
    }
    if dir_warning:
        result["warning"] = dir_warning
    return _ok(result)


if __name__ == "__main__":
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    mcp.run()
