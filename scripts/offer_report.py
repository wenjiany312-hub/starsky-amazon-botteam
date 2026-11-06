"""1688 供应商产品报告：把 1688_scraper 抓到的一个 offer 变成可直接看的 HTML（左侧电梯导航）。

输入是 get_offer / download_media 已落盘的 offer 目录（1688_source.json + media/）。
报告写在同一目录；图片压缩后内嵌（单文件可转发），视频体积大，用相对路径引用。不联网。只陈述页面事实，
缺失项明确写"页面未提供"，不补不猜。

用法：
    python offer_report.py --offer-dir <…/1688-product-detail-scraper/offer_930436706135> [--facts <03_1688产品资料_X.md>]
"""
from __future__ import annotations

import argparse
import html
import json
import sys
from pathlib import Path

ASSETS = Path(__file__).resolve().parents[1] / "skills" / "github-tabler-html-report" / "assets"
sys.path.insert(0, str(ASSETS))

GALLERY_CSS = (
    "<style>.og{display:grid;grid-template-columns:repeat(auto-fill,minmax(150px,1fr));gap:10px}"
    ".og figure{margin:0;background:#fff;border:1px solid var(--line);border-radius:10px;overflow:hidden}"
    ".og img{width:100%;aspect-ratio:1/1;object-fit:contain;background:#f6f8f7;display:block;cursor:zoom-in}"
    ".og figcaption{font-size:12px;color:var(--muted);padding:4px 8px}"
    ".od img{width:100%;max-width:790px;display:block;margin:0 auto}"
    ".ov video{width:100%;max-width:720px;border-radius:10px;background:#000}"
    ".tags span{display:inline-block;border:1px solid var(--line);border-radius:999px;padding:2px 10px;margin:3px;font-size:12px}"
    "</style>")


def _fields(data: dict, key: str) -> dict:
    return (data.get(key) or {}).get("fields") or {}


def _media(offer_dir: Path, sub: str) -> list:
    folder = offer_dir / "media" / sub
    if not folder.is_dir():
        folder = offer_dir / sub
    return sorted(p for p in folder.glob("*") if p.suffix.lower() in {".jpg", ".jpeg", ".png", ".webp", ".mp4", ".mov"}) if folder.is_dir() else []


def _rel(offer_dir: Path, p: Path) -> str:
    return html.escape(p.relative_to(offer_dir).as_posix(), quote=True)


def _img_src(p: Path, max_w: int) -> str:
    """图片内嵌进报告（压缩到 max_w 宽的 JPEG），报告单文件可转发、离线可看；无 Pillow 时退回相对路径。"""
    try:
        import base64
        import io
        from PIL import Image
        with Image.open(p) as im:
            im = im.convert("RGB")
            if im.width > max_w:
                im = im.resize((max_w, int(im.height * max_w / im.width)))
            buf = io.BytesIO()
            im.save(buf, "JPEG", quality=82, optimize=True)
        return "data:image/jpeg;base64," + base64.b64encode(buf.getvalue()).decode()
    except Exception:  # noqa: BLE001 - 缺 Pillow 或图片损坏时仍出报告
        return html.escape(p.name, quote=True)


def extract(data: dict) -> dict:
    """页面 JSON → 报告要用的事实；找不到的字段留 None。纯函数，可机检。"""
    title_f = _fields(data, "productTitle")
    shop = title_f.get("shopInfo") or {}
    rate = title_f.get("rateInfo") or {}
    dj = _fields(data, "Root").get("dataJson") or {}
    temp = dj.get("tempModel") or {}
    order = (dj.get("orderParamModel") or {}).get("orderParam") or {}
    sku_model = dj.get("skuModel") or {}
    price_twp = (_fields(data, "mainPrice").get("finalPriceModel") or {}).get("tradeWithoutPromotion") or {}
    gallery = _fields(data, "gallery")
    pack = _fields(data, "productPackInfo")
    skus = []
    for name, info in (sku_model.get("skuInfoMap") or {}).items():
        skus.append({"name": name, "price": info.get("discountPrice") or info.get("price"),
                     "stock": info.get("canBookCount"), "sold": info.get("saleCount")})
    ranges = ((order.get("skuParam") or {}).get("skuRangePrices")) or []
    video = gallery.get("video") or {}
    return {
        "offer_id": temp.get("offerId"), "title": gallery.get("subject") or temp.get("offerTitle"),
        "company": shop.get("companyName") or temp.get("companyName"),
        "factory": shop.get("cardType") == "factory", "repeat_rate": shop.get("byrRepeatRate3m"),
        "service_score": shop.get("sellerSlrServiceScore"), "good_rate": rate.get("goodRates"),
        "reviews": next((t.get("count") for t in rate.get("commonTagNodeList") or [] if t.get("name") == "全部"), None),
        "impressions": [(t.get("name"), t.get("count")) for t in rate.get("impressionTagNodeList") or []],
        "sold": temp.get("saledCount") or order.get("saledCount"), "unit": temp.get("offerUnit") or "个",
        "begin": order.get("beginNum") or price_twp.get("offerBeginAmount"),
        "min_price": price_twp.get("offerMinPrice"), "max_price": price_twp.get("offerMaxPrice"),
        "ranges": [(r.get("beginAmount"), r.get("price")) for r in ranges],
        "sku_props": [(p.get("prop"), [v.get("name") for v in p.get("value") or []]) for p in sku_model.get("skuProps") or []],
        "skus": skus, "weight_kg": pack.get("unitWeight"),
        "has_video": bool(video.get("videoId")), "image_count": len(gallery.get("offerImgList") or []),
        "seven_days_refund": dj.get("sevenDaysRefund"),
        "url": f"https://detail.1688.com/offer/{temp.get('offerId')}.html" if temp.get("offerId") else "",
    }


def product_markdown(raw, candidate, observations, research_notes=''):
    """New Markdown from saved page facts; unverified targets stay unverified.

    Research notes are attributed prior analysis, never a supplier confirmation.
    The caller seals the output and retains its exact source files separately.
    """
    from s1_readability import reader_projection
    facts=extract(raw)
    def cell(value):
        return '—' if value is None else str(value).replace('|','／').replace('\n',' ')
    offer=str(candidate.get('offer_id') or facts.get('offer_id') or '')
    rows=['# 1688 产品资料 · '+cell(candidate.get('company') or facts.get('company'))+' · '+offer,'',
          '结论：'+cell(candidate.get('risk_note') or '页面仅供研究，目标款规格与厂家能力待核。'),
          '待你决定：先索取目标款完整报价和样品，不据现款页价批准采购。',
          '本报告由留存页面与逐图观察重新生成；没有再次抓取或取得厂家确认。','',
          '## Amazon 条件数据卡','','| 项目 | 当前可见事实 |','|---|---|',
          '| 名称 | '+cell(candidate.get('title') or facts.get('title'))+' |',
          '| 净重/毛重 | 页面件重 '+cell(facts.get('weight_kg'))+' kg；未确认净重或含包装毛重，不套给改款 |',
          '| 包装/箱规 | 独立运输包规与箱规未取得 |',
          '| 厂家身份 | '+('页面工厂标签，非审厂证明' if facts.get('factory') else '待核')+'；厂家确认待核 |',
          '| 品牌/性能 | 品牌、图案不可照搬；未实测性能不得用于前台 |','',
          '## 真实 SKU 与页价','','| SKU名称 | SKU编号 | 可确认单价 |','|---|---|---|']
    for sku in candidate.get('skus') or []:
        rows.append('| '+cell(sku.get('name'))+' | '+cell(sku.get('sku_id'))+' | '+cell(sku.get('price'))+' |')
    rows+=['','| 起订数量（件） | 页面单价（人民币） | 适用边界 |','|---:|---:|---|']
    for tier in candidate.get('tiers') or []:
        rows.append('| '+cell(tier.get('min_qty'))+' | ¥'+cell(tier.get('unit_price_cny'))+' | '+cell(tier.get('basis') or '现款页价，非目标改款报价')+' |')
    rows+=['','页面价格区间：¥'+cell(candidate.get('min_price'))+'–'+cell(candidate.get('max_price'))+'。',
           '库存和起订信号不当作单价；未确认 SKU 不推定可成交价格。','',
           '## 逐图事实与边界','','| 图片 | 已留存观察 |','|---|---|']
    for image in observations.get('images') or []:
        rows.append('| '+cell(image.get('source'))+' | '+cell(image.get('text'))+' |')
    rows+=['','## 厂家核实与下游限制','',
           '请厂家逐项确认目标内外尺寸、目标用途适配、材质与结构、SKU、含税报价、MOQ、包装箱规及交期；用样品验证关键结构、尺寸及容量。',
           '页面广告、现款重量和现货阶梯价均不等于目标设计已验证；未确认前不生成可上传 Listing。','',
           '原商品页：https://detail.1688.com/offer/'+offer+'.html','']
    if research_notes:rows+=['## 沿用的原研究判断','',research_notes]
    return reader_projection('\n'.join(rows))


def build(offer_dir: Path, facts_md: Path | None = None) -> Path:
    import salon_report
    salon_report.STAGE_GROUPS.setdefault("OFFER", [
        ("overview", "先看结论"), ("media", "图片与视频"), ("trade", "价格与规格"),
        ("factory", "工厂与评价"), ("facts", "资料卡"),
    ])
    previous_paths = list(sys.path)
    try:
        sys.path.insert(0, str(Path(__file__).resolve().parent))
        from r01_native import execute_native, find_case
        case_root = find_case(offer_dir)
        if case_root is not None:
            offer_relative = Path(offer_dir).resolve().relative_to(case_root)
            facts_relative = Path(facts_md).resolve().relative_to(case_root) if facts_md else None
            compact = execute_native(case_root, lambda view: build(
                view / offer_relative, view / facts_relative if facts_relative else None), step=5)
            if compact is not NotImplemented:
                return compact
    finally:
        sys.path[:] = previous_paths
    offer_dir = offer_dir.resolve()
    data = json.loads((offer_dir / "1688_source.json").read_text(encoding="utf-8"))
    f = extract(data)
    mains, details = _media(offer_dir, "主图"), _media(offer_dir, "详情图")
    skus_img, videos = _media(offer_dir, "SKU图"), [p for p in _media(offer_dir, "视频") if p.suffix.lower() in {".mp4", ".mov"}]
    covers = [p for p in _media(offer_dir, "视频") if p.suffix.lower() in {".jpg", ".jpeg", ".png"}]
    na = "页面未提供"
    price = (f"¥{f['min_price']}" + (f"–{f['max_price']}" if f["max_price"] and f["max_price"] != f["min_price"] else "")) if f["min_price"] else na
    r = salon_report.Report(f"1688 供应商产品报告 · {f['title'] or offer_dir.name}",
                            f"{f['company'] or ''} · offer {f['offer_id'] or ''}",
                            meta=[f"offer {f['offer_id']}", "data_source 1688_scraper(MCP)", f"原页 {f['url']}"], stage="OFFER")

    s = r.section("overview", "一眼看懂这家", group="overview")
    s.kpis([("起批价", price, f"{f['begin'] or '?'} {f['unit']}起批", None),
            ("累计成交", str(f["sold"] or na), f['unit'], None),
            ("3 个月回头率", str(f["repeat_rate"] or na), "买家复购", None),
            ("好评率", f"{f['good_rate']}%" if f["good_rate"] is not None else na, f"{f['reviews'] or 0} 条评价", None)])
    if mains:
        s.raw(GALLERY_CSS + f'<p><img src="{_img_src(mains[0], 640)}" alt="主图" style="max-width:320px;border-radius:10px;border:1px solid var(--line)"></p>')
    s.p(f"工厂：{html.escape(f['company'] or na)}（{'源头工厂' if f['factory'] else '非工厂/贸易'}）· 服务分 {html.escape(str(f['service_score'] or na))} · "
        f"单品重量 {f['weight_kg'] or na} kg · 素材：主图 {len(mains)} 张、详情图 {len(details)} 张、视频 {len(videos)} 个")
    if f["url"]:
        s.raw(f'<p><a href="{html.escape(f["url"], quote=True)}" target="_blank" rel="noopener">打开 1688 原页 →</a></p>')

    s = r.section("gallery", f"主图（{len(mains)}）", group="media")
    s.raw('<div class="og">' + "".join(
        f'<figure><img loading="lazy" src="{_img_src(p, 800)}" alt="{p.stem}" onclick="this.requestFullscreen&&this.requestFullscreen()"><figcaption>{p.name}</figcaption></figure>'
        for p in mains + skus_img) + "</div>" if mains or skus_img else "<p>未下载主图：运行 download_media 后重新生成本报告。</p>")
    s = r.section("video", "产品视频", group="media")
    if videos:
        poster = f' poster="{_rel(offer_dir, covers[0])}"' if covers else ""
        s.raw('<div class="ov">' + "".join(f'<video controls preload="metadata"{poster} src="{_rel(offer_dir, v)}"></video><p>{v.name}</p>' for v in videos) + "</div>")
    else:
        s.p("该商品页面没有主图视频（videoId=0）。" if not f["has_video"] else "页面有视频但尚未下载：重新运行 download_media。")
    s = r.section("detail", f"详情页（{len(details)} 张，按页面顺序）", group="media")
    s.raw('<div class="od">' + "".join(f'<img loading="lazy" src="{_img_src(p, 790)}" alt="{p.stem}">' for p in details) + "</div>"
          if details else "<p>未下载详情图：运行 download_media 后重新生成本报告。</p>")

    s = r.section("price", "价格阶梯与 SKU", group="trade")
    if f["ranges"]:
        s.table(["起订量", "单价"], [[f"≥ {a} {f['unit']}", f"¥{p}"] for a, p in f["ranges"]])
    for prop, values in f["sku_props"]:
        s.p(f"{html.escape(str(prop))}：{html.escape('、'.join(str(v) for v in values))}")
    if f["skus"]:
        s.table(["规格", "单价", "可订库存", "已售"], [[x["name"], f"¥{x['price']}", x["stock"] if x["stock"] is not None else "—",
                                                  x["sold"] if x["sold"] is not None else "—"] for x in f["skus"]], searchable=len(f["skus"]) > 12)
    s = r.section("pack", "重量与包装", group="trade")
    s.p(f"单品重量：{f['weight_kg'] or na} kg。外箱规格、装箱数、体积重以资料卡或供应商回填为准。")

    s = r.section("factory", "工厂与评价", group="factory")
    s.table(["项", "值"], [["公司", f["company"] or na], ["类型", "源头工厂" if f["factory"] else "非工厂/贸易"],
                          ["3 个月回头率", f["repeat_rate"] or na], ["服务分", f["service_score"] or na],
                          ["好评率", f"{f['good_rate']}%" if f["good_rate"] is not None else na], ["评价数", f["reviews"] or na],
                          ["7 天无理由", "支持" if f["seven_days_refund"] else "未标注"]])
    if f["impressions"]:
        s.raw('<div class="tags">' + "".join(f"<span>{html.escape(str(n))} · {c}</span>" for n, c in f["impressions"]) + "</div>")

    if facts_md and facts_md.is_file():
        r.section("facts", "Amazon 上架数据卡与采购资料（03 报告全文）", group="facts").markdown(facts_md.read_text(encoding="utf-8"))

    out = offer_dir / f"供应商产品报告_{f['offer_id'] or offer_dir.name}.html"
    r.write(str(out))
    return out


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--offer-dir", required=True, type=Path)
    ap.add_argument("--facts", type=Path, help="可选：03_1688产品资料_X.md，附在报告末尾")
    args = ap.parse_args()
    out = build(args.offer_dir, args.facts)
    print(json.dumps({"ok": True, "html": str(out)}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
