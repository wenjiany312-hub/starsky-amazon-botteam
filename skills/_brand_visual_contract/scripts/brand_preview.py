#!/usr/bin/env python3
"""品牌故事 / 品牌旗舰店：方案 YAML → 上传包（从这里开始.html + 上传图片 + 文案与 ASIN + 校验清单）。

  <python> <skill>/scripts/brand_preview.py --run "<产品根>" --kind story|store --init   生成方案待填表
  <python> <skill>/scripts/brand_preview.py --run "<产品根>" --kind story|store          校验 → 出图包 → 上传包

从这里开始.html 一页三块：效果预览（电脑/手机）、后台照填（按后台顺序，一键复制）、核对。
回填夹里有图就用真图并按后台尺寸导出到 01_上传图片/；没有就用参考图或尺寸占位。同一输入同一输出。
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import html
import io
import importlib.util
import json
import os
import re
import shutil
import zipfile
from datetime import date
from pathlib import Path

import yaml

HERE = Path(__file__).resolve()
SPECS = HERE.parents[1] / 'references' / 'brand_specs.yaml'
S3 = Path('S3_视觉板块')
IMG_EXT = {'.jpg', '.jpeg', '.png', '.webp', '.bmp'}
GENERATOR = 'brand_preview.py'
LOADER = getattr(yaml, 'CSafeLoader', yaml.SafeLoader)
KINDS = {
    'story': {'folder': 'amazon-brand-story-designer', 'plan': '10_brand_story.yaml', 'package': '品牌故事上传包',
              'status': '10_preview_status.json', 'texts': '02_文案与ASIN', 'feed': 'GPT网页端投喂_品牌故事',
              'back': 'GPT网页端回填_品牌故事', 'title': '品牌故事', 'say': '品牌故事回填好了'},
    'store': {'folder': 'amazon-brand-store-designer', 'plan': '11_brand_store.yaml', 'package': '旗舰店上传包',
              'status': '11_preview_status.json', 'texts': '02_文案与链接', 'feed': 'GPT网页端投喂_旗舰店',
              'back': 'GPT网页端回填_旗舰店', 'title': '品牌旗舰店', 'say': '旗舰店回填好了'},
}
IMAGES_DIR = '01_上传图片'
HISTORY = '_历史版本'
POSITIONS = ['左上', '右上', '左下', '右下']


def load_yaml(path):
    try:
        data = yaml.load(Path(path).read_text(encoding='utf-8-sig'), Loader=LOADER)  # noqa: S506 - safe loader
    except FileNotFoundError:
        return {}
    return data if isinstance(data, dict) else {}


def esc(value):
    return html.escape('' if value is None else str(value), quote=True)


def slug(text, default):
    words = re.findall(r'[A-Za-z0-9]+', str(text or ''))
    return '_'.join(w[:1].upper() + w[1:] for w in words[:4]) or default


# ---------------------------------------------------------------- inputs

def core_keywords(root, limit=12):
    """Top usable keywords of the S2 package, so alt text and copy reuse the listing's words."""
    if r03_enabled(root):
        plan=load_yaml(Path(root)/S3/'amazon-listing-image-designer/visual_suite.yaml');variant=plan.get('aplus_variant') or plan['variants'][0]
        snapshot=r03_helper('keyword_asset').read_snapshot(Path(plan.get('series_root') or root))
        return [r['term_normalized'] for r in snapshot.select_many(lists=('master',),sku=(plan.get('sku_ids') or {}).get(variant,variant),copy_allowed=True,limit=limit)['master'] if r['marketplace']==plan.get('marketplace','US')]
    words = []
    for pkg_path in sorted((root / 'S2_文案板块').glob('*/keyword_package.yaml')):
        for rec in load_yaml(pkg_path).get('records') or []:
            if rec.get('disposition') != 'retained' or rec.get('claim_restricted'):
                continue
            vol = ((rec.get('metrics') or {}).get('weekly_search_volume') or {}).get('value')
            words.append((-(vol or 0), rec.get('keyword')))
    seen, out = set(), []
    for _, word in sorted(words):
        if word and word not in seen:
            seen.add(word)
            out.append(word)
    return out[:limit]


def reference_images(root, limit=12):
    """Already-approved visuals first (exported listing / A+), then selected pad images."""
    folders = [S3 / 'amazon-listing-image-designer/visuals/listing', S3 / 'amazon-premium-aplus-designer/aplus_visuals',
               S3 / 'amazon-listing-image-designer/产品垫图/主副图']
    out = []
    for folder in folders:
        path = root / folder
        if path.is_dir():
            out += sorted(p.relative_to(root).as_posix() for p in path.iterdir() if p.suffix.lower() in IMG_EXT)
    return out[:limit]


def image(ref='', prompt='', alt=''):
    return {'prompt': prompt, 'ref': ref, 'alt': alt}


def overlay_tile(ref=''):
    """The proven store entry block: scene left, light panel right with baked helper copy; title + button are native."""
    return {'type': 'image_text_overlay', 'overlay': {'size': '中号', 'align': '左对齐', 'position': '右上角'},
            'prefix': '', 'title': '', 'body': '', 'button': 'Shop now', 'link': {'type': 'asin', 'target': ''},
            'baked_text': '', 'image': {'prompt': '', 'ref': ref, 'alt': '', 'master': ''}, 'fallback': '',
            'verified': {'module': False, 'desktop': False, 'mobile': False, 'link': False}}


def skeleton(root, kind):
    kw, refs = core_keywords(root), reference_images(root)
    ref = lambda i: refs[i % len(refs)] if refs else ''  # noqa: E731
    base = {'schema_version': '2.0', 'generator': GENERATOR, 'kind': 'brand_story' if kind == 'story' else 'brand_store',
            'version': 1, 'revision_note': '首版', 'date': date.today().isoformat(), 'marketplace': 'US', 'theme': '',
            'brand': {'name': '', 'tagline': '', 'story': ''},
            'intent': '', 'style': {'name': '', 'palette': ['#1f3b36', '#f4efe6', '#c8a96a'], 'mood': '', 'why': ''},
            'keywords': kw, 'reference_images': refs}
    if kind == 'story':
        base['background'] = {'headline': '', 'body': '', 'desktop': image(ref(0)), 'mobile': image(ref(0))}
        base['modules'] = [
            {'id': 'H1', 'type': 'logo', 'body': '', 'image': image()},
            {'id': 'H2', 'type': 'focus_image', 'name': 'Scene', 'headline': '', 'body': '', 'embedded_text': '', 'image': image(ref(1))},
            {'id': 'H3', 'type': 'asin_showcase', 'headline': '',
             'products': [{'position': pos, 'asin': '', 'label': '', 'image': image(ref(i + 2))} for i, pos in enumerate(POSITIONS)]},
        ]
        base['application_asins'] = [{'asin': '', 'product': '', 'scope': '', 'note': ''}]
        base['store_link'] = {'reference_url': '', 'note': '从编辑器选取当前品牌旗舰店；链接里的 page UUID 不当成已核实的 Store ID'}
    else:
        base['mode'] = 'new_store'
        base['brand'].update({'logo': image(), 'header': image(ref(0)), 'meta_description': ''})
        base['products'] = [{'asin': '', 'name': '', 'series': '', 'variant': '', 'role': '常规', 'status': '待核',
                             'evidence': '', 'image': ref(0), 'simulated': False}]
        base['audit'] = []
        base['sample_block'] = 'P1_T01_image_text_overlay'
        base['accepted'] = False
        base['pages'] = [{'id': 'home', 'name': 'Home', 'task': '一次看清全部产品线，点进对应品类页', 'sections': [
            {'layout': 'full_width', 'tiles': [overlay_tile(ref(1))]},
            {'layout': 'full_width', 'tiles': [overlay_tile(ref(2))]}]}]
        base['revision_log'] = []
    return base


def language_of(doc, specs):
    return doc.get('language') or specs['brand_story']['languages'].get(str(doc.get('marketplace') or 'US').upper(), 'English')


def content_name(doc, specs):
    if doc.get('content_name'):
        return doc['content_name']
    brand = slug((doc.get('brand') or {}).get('name'), 'Brand')
    theme = slug(doc.get('theme'), '')
    parts = [brand, str(doc.get('marketplace') or 'US').upper(), theme,
             'Brand_Story' if doc.get('kind') == 'brand_story' else 'Brand_Store',
             f'V{doc.get("version", 1)}', str(doc.get('date') or '').replace('-', '')]
    return '_'.join(p for p in parts if p)


# ---------------------------------------------------------------- slots

def size_of(specs, kind, key, mobile=False):
    if kind == 'story':
        node = specs['brand_story']['modules'].get(key) or {}
        value = node.get('mobile_size') if mobile else node.get('size')
    elif key in ('hero', 'logo'):
        value = specs['brand_store'][key]['size']
    else:
        value = (specs['brand_store']['tiles'].get(key) or {}).get('size')
    return tuple(value) if value else None


def slots_of(doc, kind, specs):
    """Every image the plan needs, in backend order."""
    out = []

    def add(slot, key, owner, where, mobile=False):
        size = size_of(specs, kind, key, mobile)
        out.append({'slot': slot, 'key': key, 'owner': owner if isinstance(owner, dict) else {}, 'where': where, 'size': size,
                    'file': f'{slot}_{size[0]}x{size[1]}.png' if size else None})
    if kind == 'story':
        bg = doc.get('background') or {}
        add('B0_Desktop', 'background', bg.get('desktop'), 'B0 · 背景（电脑）')
        add('B0_Mobile', 'background', bg.get('mobile'), 'B0 · 背景（手机）', mobile=True)
        for m in doc.get('modules') or []:
            mid, t = str(m.get('id')), m.get('type')
            if t == 'logo':
                add(f'{mid}_Logo', 'logo', m.get('image'), f'{mid} · Logo')
            elif t == 'focus_image':
                add(f'{mid}_{slug(m.get("name"), "Focus")}', 'focus_image', m.get('image'), f'{mid} · 焦点图')
            elif t in ('asin_showcase', 'store_showcase'):
                for i, prod in enumerate(m.get('products') or [], 1):
                    tag = prod.get('asin') or f'P{i}'
                    add(f'{mid}_{i}_{tag}', t, prod.get('image'), f'{mid} · {prod.get("position") or i} · {prod.get("label") or tag}')
    else:
        brand = doc.get('brand') or {}
        add('S0_Logo', 'logo', brand.get('logo'), '品牌 Logo')
        if brand.get('header'):
            add('S0_Header', 'hero', brand.get('header'), '共用页头 Header')
        for p_i, page in enumerate(doc.get('pages') or [], 1):
            name = page.get('name') or page.get('id')
            if page.get('hero'):
                add(f'P{p_i}_Hero', 'hero', page.get('hero'), f'{name} · 本页页头')
            n = 0
            for s_i, sec in enumerate(page.get('sections') or [], 1):
                for tile in sec.get('tiles') or []:
                    n += 1
                    if size_of(specs, kind, tile.get('type')) is None:
                        continue
                    add(f'P{p_i}_T{n:02d}_{tile.get("type")}', tile.get('type'), tile.get('image'), f'{name} · 区块 {s_i} · 图块 {n}')
                    out[-1]['tile'] = tile
    return out


# ---------------------------------------------------------------- returns and export

def returned_images(back):
    if not back.is_dir():
        return []
    for z in sorted(back.glob('*.zip')):
        target = back / '_解压' / z.stem
        if not target.exists():
            try:
                with zipfile.ZipFile(z) as zf:
                    zf.extractall(target)
            except zipfile.BadZipFile:
                continue
    return sorted(p for p in back.rglob('*') if p.is_file() and p.suffix.lower() in IMG_EXT and '__MACOSX' not in p.parts)


def match_return(slot, files):
    key = slot.lower()
    for p in files:
        stem = p.stem.lower()
        if stem == key or stem.startswith(key + '_') or stem.startswith(key + '-'):
            return p
    return None


def export_image(src, dest, size, contain=False):
    """Cover-crop (contain for logos) to the exact backend size; returns the source width/height."""
    from PIL import Image
    with Image.open(src) as im:
        src_size = im.size
        im = im.convert('RGBA' if contain else 'RGB')
        w, h = size
        if contain:
            im.thumbnail((w, h), Image.LANCZOS)
            out = Image.new('RGBA', (w, h), (255, 255, 255, 0))
            out.paste(im, ((w - im.width) // 2, (h - im.height) // 2), im)
        else:
            scale = max(w / im.width, h / im.height)
            im = im.resize((max(w, round(im.width * scale)), max(h, round(im.height * scale))), Image.LANCZOS)
            left, top = (im.width - w) // 2, (im.height - h) // 2
            out = im.crop((left, top, left + w, top + h))
        dest.parent.mkdir(parents=True, exist_ok=True)
        out.save(dest)
    return src_size


# ---------------------------------------------------------------- checks

def check(doc, kind, specs, slots):
    errors, warnings = [], []

    def limit(text, n, where):
        if text and len(str(text)) > n:
            warnings.append(f'{where}：{len(str(text))} 字符，超过建议上限 {n}')
    if not (doc.get('brand') or {}).get('name'):
        errors.append('缺品牌名 brand.name')
    if not (doc.get('style') or {}).get('name'):
        warnings.append('未写风格 style.name：预览用默认配色')
    if kind == 'story':
        spec = specs['brand_story']['modules']
        bg = doc.get('background') or {}
        if not bg:
            errors.append('缺背景 background（电脑 + 手机两张）')
        limit(bg.get('headline'), spec['background']['text']['headline'], 'B0 背景标题')
        limit(bg.get('body'), spec['background']['text']['body'], 'B0 背景正文')
        mods = doc.get('modules') or []
        types = [m.get('type') for m in mods]
        if 'logo' not in types:
            errors.append('缺 Logo + 品牌介绍模块')
        bad = [t for t in types if t not in spec or t == 'background']
        if bad:
            errors.append('不认识的模块类型：' + '、'.join(map(str, bad)))
        if len(mods) > specs['brand_story']['max_cards']:
            warnings.append(f'模块 {len(mods)} 个，超过建议上限 {specs["brand_story"]["max_cards"]}')
        for m in mods:
            node = spec.get(m.get('type')) or {}
            where = f'{m.get("id")} {node.get("label", m.get("type"))}'
            text = node.get('text') or {}
            limit(m.get('headline'), text.get('headline', 999), where + ' 标题')
            limit(m.get('body'), text.get('description') or text.get('body', 999), where + ' 正文')
            if m.get('type') == 'focus_image' and m.get('embedded_text') and (m.get('headline') or m.get('body')):
                warnings.append(f'{where}：图内已有文字，后台标题和正文建议留空，免得重复')
            if m.get('type') in ('asin_showcase', 'store_showcase'):
                prods = m.get('products') or []
                if any(re.search(r'shop now|button|按钮', str((p.get('image') or {}).get('prompt') or ''), re.I) for p in prods):
                    warnings.append(f'{where}：四宫格图里不要画按钮，店铺入口交给后台原生选取')
                cap = node.get('max_asins', node.get('max_pages', 4))
                if len(prods) > cap:
                    errors.append(f'{where}：超过 {cap} 格')
                for p in prods:
                    if m.get('type') == 'asin_showcase' and not p.get('asin'):
                        warnings.append(f'{where} · {p.get("position")}：缺 ASIN')
                    limit(p.get('label'), text.get('label', 30), f'{where} · {p.get("position")} 标签')
            if m.get('type') == 'qa':
                pairs = m.get('pairs') or []
                if len(pairs) > node.get('max_pairs', 4):
                    errors.append(f'{where}：问答超过 {node["max_pairs"]} 组')
                for i, pair in enumerate(pairs, 1):
                    limit(pair.get('q'), text['question'], f'{where} · Q{i}')
                    limit(pair.get('a'), text['answer'], f'{where} · A{i}')
        if not [a for a in doc.get('application_asins') or [] if a.get('asin')]:
            warnings.append('应用 ASIN 清单为空：后台要选这份品牌故事挂到哪些商品')
    else:
        spec = specs['brand_store']
        tiles_spec = spec['tiles']
        products = doc.get('products') or []
        if doc.get('mode') not in ('new_store', 'redesign', 'partial'):
            warnings.append('mode 未写：new_store（从 0 建店）/ redesign（已有店改版）/ partial（局部优化）')
        if doc.get('mode') == 'redesign' and not doc.get('audit'):
            warnings.append('改版先做现店盘点 audit：逐页记下保留 / 调整 / 新增 / 移除')
        if len(products) < 3 and doc.get('mode') != 'partial':
            warnings.append(f'商品矩阵只有 {len(products)} 个：建店建议至少 3 个同品牌在售产品')
        stopped = [p.get('asin') or p.get('name') for p in products if p.get('status') == '停售']
        if stopped:
            errors.append('停售款仍在商品矩阵里：' + '、'.join(map(str, stopped)) + '（从店内入口、图文、商品模块一起移出）')
        pending = [p.get('asin') or p.get('name') for p in products if p.get('status') in (None, '', '待核')]
        if pending:
            warnings.append('在售状态待核：' + '、'.join(map(str, pending)))
        sims = sum(1 for p in products if p.get('simulated'))
        if sims:
            warnings.append(f'含 {sims} 个模拟产品：只用于看效果，上线前换成真实 ASIN')
        brand = doc.get('brand') or {}
        pages = doc.get('pages') or []
        if not brand.get('header') and not any(pg.get('hero') for pg in pages):
            errors.append('缺页头 Header（brand.header，全店共用一张即可）')
        if not pages:
            errors.append('旗舰店至少要有首页')
        slot_ids = {x['slot'] for x in slots}
        if not doc.get('sample_block'):
            warnings.append('未指定样块 sample_block：先在后台验证一块（模块、字段、链接、手机），认可后再做同类')
        elif doc['sample_block'] not in slot_ids:
            errors.append(f'样块 {doc["sample_block"]} 不是本方案里的图位')
        unverified, mobile_todo = 0, 0
        for page in pages:
            pname = page.get('name')
            if not page.get('task'):
                warnings.append(f'页面「{pname}」没写 task：买家进来要解决什么')
            secs = page.get('sections') or []
            if len(secs) > spec['max_sections_per_page']:
                errors.append(f'页面「{pname}」{len(secs)} 个区块，超过 {spec["max_sections_per_page"]}')
            grids = [t for sec in secs for t in sec.get('tiles') or [] if t.get('type') == 'product_grid']
            if len(grids) > tiles_spec['product_grid']['max_per_page']:
                errors.append(f'页面「{pname}」有 {len(grids)} 个商品网格，每页至多 1 个')
            for g in grids:
                count = len(g.get('asins') or [p for p in products if p.get('status') != '停售'])
                if count < tiles_spec['product_grid']['min_products']:
                    warnings.append(f'页面「{pname}」商品网格只有 {count} 件：少于 4 件改用商品模块逐个放')
            for sec in secs:
                for tile in sec.get('tiles') or []:
                    t = tile.get('type')
                    if t not in tiles_spec:
                        errors.append(f'页面「{pname}」不认识的图块类型：{t}（只用后台真实模块）')
                        continue
                    if t.startswith('image_text'):
                        where = f'页面「{pname}」{tile.get("title") or t}'
                        title, baked = str(tile.get('title') or ''), str(tile.get('baked_text') or '')
                        if not title:
                            warnings.append(f'{where}：缺后台标题')
                        if title and title.lower() in baked.lower():
                            errors.append(f'{where}：图中文字又写了一遍标题，会和后台标题重复')
                        link = tile.get('link') or {}
                        if tile.get('button') and not link.get('target'):
                            warnings.append(f'{where}：按钮「{tile["button"]}」没写链接目标')
                        if link.get('type') == 'asin' and re.search(r'explore|range|collection|all |系列|全部', str(tile.get('button') or ''), re.I):
                            errors.append(f'{where}：按钮写的是系列入口，却链接单品；改链接到店铺页面，或把按钮改成单品说法')
                        verified = tile.get('verified') or {}
                        if not verified.get('module'):
                            unverified += 1
                        if baked and not verified.get('mobile'):
                            mobile_todo += 1
        if unverified:
            warnings.append(f'{unverified} 个图文块的后台模块还没实测：当前是概念预览，先用样块验证')
        if mobile_todo:
            warnings.append(f'{mobile_todo} 个带图内文字的图块还没做手机验收：桌面认可不能代替手机')
    for s in slots:
        if s['key'] != 'logo' and not s['owner'].get('alt'):
            warnings.append(f'{s["slot"]}（{s["where"]}）缺替代文本 alt：建议自然带 1–2 个核心词')
    return errors, warnings


# ---------------------------------------------------------------- page

CSS = """
:root{--ink:#1d2a2a;--muted:#667;--line:#e3e6e6;--bg:#eef1f1;--accent:#0f766e;--p1:__P1__;--p2:__P2__;--p3:__P3__}
*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--ink);font:15px/1.6 -apple-system,system-ui,"PingFang SC","Microsoft YaHei",sans-serif}
header.top{background:#fff;border-bottom:1px solid var(--line);padding:18px 22px 0}header.top h1{margin:0;font-size:22px}header.top p{margin:6px 0;color:var(--muted);font-size:14px}
.meta{display:flex;flex-wrap:wrap;gap:8px;margin:10px 0}.pill{background:#eef6f4;color:var(--accent);border-radius:999px;padding:2px 10px;font-size:12px;font-weight:600}.pill.warn{background:#fff4dc;color:#8a5a00}.pill.err{background:#ffe6e6;color:#b42318}
.tabs{display:flex;flex-wrap:wrap;gap:4px;margin-top:10px}.tabs button{font:inherit;border:0;background:none;padding:10px 14px;cursor:pointer;border-bottom:3px solid transparent;color:var(--muted);font-weight:600}
.tabs button[aria-selected=true]{color:var(--ink);border-color:var(--accent)}.tools{margin-left:auto;display:flex;gap:6px;align-items:center}
.tools button{font:inherit;font-size:12px;border:1px solid #c9d6d4;background:#fff;color:var(--accent);border-radius:7px;padding:4px 10px;cursor:pointer}.tools button[aria-pressed=true]{background:var(--accent);color:#fff}
main{max-width:1240px;margin:0 auto;padding:18px}.panel[hidden]{display:none}
.frame{margin:0 auto;background:#fff;border:1px solid var(--line);border-radius:10px;overflow:hidden;max-width:1200px;transition:max-width .2s}body.mobile .frame{max-width:414px}
.img{position:relative;background:#dfe5e4 center/cover no-repeat;overflow:hidden}.img img{width:100%;height:100%;object-fit:cover;display:block}
.ph{position:absolute;inset:0;display:flex;flex-direction:column;align-items:center;justify-content:center;color:#51605f;font-size:12px;text-align:center;padding:8px;background:repeating-linear-gradient(45deg,#e8eceb,#e8eceb 10px,#dfe5e4 10px,#dfe5e4 20px)}
.ph b{font-size:13px;color:#2f3d3c}.badge{position:absolute;left:6px;top:6px;max-width:calc(100% - 12px);overflow:hidden;text-overflow:ellipsis;white-space:nowrap;background:#0008;color:#fff;font-size:11px;border-radius:4px;padding:1px 6px}
.from{padding:14px 18px 6px;font-size:18px;font-weight:700}
.story{position:relative}.story .bg{aspect-ratio:1464/625;min-height:460px}.story .bgm{display:none}
.story .over{position:absolute;inset:0;display:flex;align-items:center;gap:14px;padding:18px;overflow-x:auto}
.story .lead{flex:0 0 260px;color:#fff;background:linear-gradient(0deg,#0009,#0000);padding:14px;border-radius:8px}.story .lead h2{margin:0 0 6px;font-size:22px}
.card{flex:0 0 181px;background:#fff;border-radius:8px;box-shadow:0 2px 10px #0002;overflow:hidden}.card .img{aspect-ratio:362/453}.card .t{padding:8px 10px;font-size:13px}.card .t b{display:block}
.card.logo .img{aspect-ratio:315/145;background:#fff}.card .emb{position:absolute;left:8px;right:8px;bottom:8px;color:#fff;font-size:12px;text-shadow:0 1px 3px #000;white-space:pre-line}
.card.asin{flex-basis:220px}.card.asin .grid{display:grid;grid-template-columns:1fr 1fr;gap:6px;padding:8px}.card.asin .grid .img{aspect-ratio:166/182;border-radius:4px}.card.asin small{display:block;font-size:11px;padding:2px}
body.mobile .story .bg{display:none}body.mobile .story .bgm{display:block;aspect-ratio:463/625}body.mobile .story .lead{flex-basis:200px}
.store header{display:flex;align-items:center;gap:12px;padding:12px 18px;border-bottom:1px solid var(--line)}.store header .logo{width:64px;height:64px;border-radius:6px}
.store nav{display:flex;gap:4px;padding:0 18px;border-bottom:1px solid var(--line);overflow-x:auto}.store nav button{font:inherit;border:0;background:none;padding:10px 12px;cursor:pointer;border-bottom:3px solid transparent}
.store nav button[aria-selected=true]{border-color:var(--p3);font-weight:700}.page[hidden]{display:none}
.hero{aspect-ratio:3000/600}.safe{position:absolute;top:0;bottom:0;left:15%;right:15%;border-left:2px dashed #fffc;border-right:2px dashed #fffc;pointer-events:none}
.sec{display:grid;gap:8px;padding:8px}.sec.full_width{grid-template-columns:1fr}.sec.split{grid-template-columns:1fr 1fr}.sec.quad{grid-template-columns:repeat(4,1fr)}.sec.large_small{grid-template-columns:2fr 1fr 1fr}
.tile .img{border-radius:4px}.tile.full_width .img,.tile.shoppable .img,.tile.video .img,.tile.medium .img{aspect-ratio:2/1}.tile.large .img,.tile.small .img{aspect-ratio:1}
.tile.image_text{display:grid;grid-template-columns:minmax(0,380px) 1fr;gap:18px;align-items:center}.tile.image_text .img{aspect-ratio:1}.tile .cap{font-size:13px;padding:4px 2px}.tile .cap b{display:block;font-size:15px}
.pgrid{display:grid;grid-template-columns:repeat(4,1fr);gap:10px;padding:8px}.pgrid .p{border:1px solid var(--line);border-radius:6px;padding:8px;font-size:12px}.pgrid .p .img{aspect-ratio:1;margin-bottom:6px}.sim{color:#a16207}
body.mobile .sec,body.mobile .pgrid{grid-template-columns:1fr 1fr}body.mobile .sec.full_width{grid-template-columns:1fr}body.mobile .tile.image_text{grid-template-columns:1fr}
.block{background:#fff;border:1px solid var(--line);border-radius:10px;padding:16px 18px;margin:0 0 14px}.block h2{font-size:17px;margin:0 0 10px}.block h2 small{color:var(--muted);font-weight:400}
.row{display:grid;grid-template-columns:120px minmax(0,1fr) auto;gap:10px;align-items:start;padding:8px 0;border-top:1px dashed var(--line)}
.row .k{color:var(--muted);font-size:13px}.row .v{white-space:pre-wrap;overflow-wrap:anywhere}.row .v.empty{color:var(--muted)}
.copy{font:inherit;font-size:12px;border:1px solid #c9d6d4;background:#fff;color:var(--accent);border-radius:6px;padding:3px 10px;cursor:pointer}
.files{display:flex;flex-wrap:wrap;gap:12px;margin:6px 0}.file{width:180px;font-size:12px}.file .img{border-radius:6px;border:1px solid var(--line);aspect-ratio:4/3}.file b{display:block;overflow-wrap:anywhere}
.state-returned{color:#15803d}.state-ref{color:#a16207}.state-none{color:#b42318}
ul.list{padding-left:18px;margin:6px 0}.note{font-size:13px;color:var(--muted)}#toast{position:fixed;right:18px;bottom:18px;background:var(--ink);color:#fff;padding:8px 14px;border-radius:6px;font-size:13px}#toast:empty{display:none}
.chain{display:flex;flex-wrap:wrap;gap:6px;margin:4px 0 8px}.chain span{font-size:12px;border:1px solid var(--line);border-radius:999px;padding:2px 10px;color:var(--muted);background:#fff}.chain span.on{color:#15803d;border-color:#bfe3cc;background:#f0faf3}.concept{background:#fff8e6;border:1px solid #f2d99a;color:#7a5200;border-radius:8px;padding:8px 12px;font-size:13px;margin:6px 0 8px}.tile.ov .img{aspect-ratio:3000/1280}.ovpanel{position:absolute;top:0;bottom:0;right:0;width:30%;background:#eceff0;color:#3c4b4a;padding:14% 4% 4%;font-size:14px;white-space:pre-line;display:flex;align-items:center}.ovnative{position:absolute;right:0;width:30%;padding:4% 4% 0;display:flex;flex-direction:column;align-items:flex-start;gap:8px}.ovnative.pos-右上角{top:0}.ovnative.pos-左上角{top:0;left:0;right:auto}.ovnative b{font-size:22px;color:#1f3b36;font-weight:600}.btn{display:inline-block;background:#0f7c86;color:#fff;font-size:12px;font-weight:700;padding:6px 12px;border-radius:2px}.tile.txt{padding:14px 18px}.tile.txt p{margin:4px 0;color:var(--muted)}.tilecard{border-top:1px dashed var(--line);padding-top:10px;margin-top:10px}.tilecard h3{font-size:14px;margin:4px 0}table.t{width:100%;border-collapse:collapse;font-size:13px}table.t th,table.t td{border-bottom:1px solid var(--line);padding:6px 8px;text-align:left;vertical-align:top}table.t th{color:var(--muted);font-weight:600}body.mobile .ovpanel,body.mobile .ovnative{position:static;width:auto;padding:10px}body.mobile .tile.ov .img{aspect-ratio:auto}body.mobile .tile.ov img{aspect-ratio:3000/1280;height:auto}@media(max-width:760px){.row{grid-template-columns:1fr}.sec.quad,.pgrid{grid-template-columns:1fr 1fr}.tools{margin-left:0}}
"""

JS = """
(()=>{const b=document.body,toast=document.getElementById('toast');
document.querySelectorAll('[data-tab]').forEach(t=>t.addEventListener('click',()=>{document.querySelectorAll('[data-tab]').forEach(o=>o.setAttribute('aria-selected',String(o===t)));
document.querySelectorAll('.panel').forEach(p=>p.hidden=p.id!==t.dataset.tab);}));
document.querySelectorAll('[data-view]').forEach(x=>x.addEventListener('click',()=>{b.classList.toggle('mobile',x.dataset.view==='mobile');document.querySelectorAll('[data-view]').forEach(y=>y.setAttribute('aria-pressed',String(y===x)));}));
document.querySelectorAll('[data-safe]').forEach(x=>x.addEventListener('click',()=>{const on=x.getAttribute('aria-pressed')!=='true';x.setAttribute('aria-pressed',String(on));document.querySelectorAll('.safe').forEach(s=>s.hidden=!on);}));
const pages=[...document.querySelectorAll('[data-page]')];pages.forEach(t=>t.addEventListener('click',()=>{pages.forEach(o=>o.setAttribute('aria-selected',String(o===t)));document.querySelectorAll('.page').forEach(p=>p.hidden=p.id!==t.dataset.page);}));
document.querySelectorAll('[data-copy]').forEach(btn=>btn.addEventListener('click',async()=>{const text=btn.dataset.copy;try{if(navigator.clipboard&&window.isSecureContext)await navigator.clipboard.writeText(text);
else{const a=document.createElement('textarea');a.value=text;a.style.position='fixed';a.style.left='-9999px';document.body.appendChild(a);a.select();document.execCommand('copy');a.remove();}toast.textContent='已复制';}
catch(_){toast.textContent='复制失败，请手动选中';}setTimeout(()=>{toast.textContent='';},1800);}));})();
"""

STATE_WORD = {'returned': '已出图', 'ref': '参考图，待出图', 'none': '待出图'}


def picture(s, cls='img', label=''):
    if s is None:
        return f'<div class="{cls}"><div class="ph"><b>{esc(label)}</b></div></div>'
    size = f'{s["size"][0]}×{s["size"][1]}' if s.get('size') else ''
    if s.get('src'):
        badge = {'returned': '已出图', 'ref': '参考图'}[s['state']]
        return (f'<div class="{cls}"><img src="{esc(s["src"])}" alt="{esc(s["owner"].get("alt"))}">'
                f'<span class="badge">{esc(s["slot"])} · {badge}</span></div>')
    return (f'<div class="{cls}"><div class="ph"><b>{esc(s["slot"])}</b>{esc(size)}<br>{esc(label)}'
            f'<br><small>{esc((s["owner"].get("prompt") or "")[:90])}</small></div></div>')


def tile_slot(mid, i, prod):
    return f'{mid}_{i}_{prod.get("asin") or f"P{i}"}'


def render_story(doc, by):
    bg = doc.get('background') or {}
    cards = []
    for m in doc.get('modules') or []:
        mid, t = str(m.get('id')), m.get('type')
        if t == 'logo':
            cards.append(f'<div class="card logo">{picture(by.get(mid + "_Logo"), label="Logo")}<div class="t">{esc(m.get("body"))}</div></div>')
        elif t == 'focus_image':
            s = by.get(f'{mid}_{slug(m.get("name"), "Focus")}')
            pic = picture(s)
            if m.get('embedded_text') and s and s.get('src') and s['state'] == 'ref':
                pic = pic.replace('</div>', f'<div class="emb">{esc(m["embedded_text"])}</div></div>', 1)
            text = f'<div class="t"><b>{esc(m.get("headline"))}</b>{esc(m.get("body"))}</div>' if m.get('headline') or m.get('body') else ''
            cards.append(f'<div class="card">{pic}{text}</div>')
        elif t in ('asin_showcase', 'store_showcase'):
            grid = ''.join(f'<div>{picture(by.get(tile_slot(mid, i, p)))}<small>{esc(p.get("label"))}</small></div>'
                           for i, p in enumerate(m.get('products') or [], 1))
            cards.append(f'<div class="card asin"><div class="t"><b>{esc(m.get("headline"))}</b></div><div class="grid">{grid}</div></div>')
        elif t == 'qa':
            qa = ''.join(f'<p><b>Q：{esc(p.get("q"))}</b><br>A：{esc(p.get("a"))}</p>' for p in m.get('pairs') or [])
            cards.append(f'<div class="card"><div class="t">{qa}</div></div>')

    def layer(s, cls):
        if s and s.get('src'):
            return f'<div class="img {cls}" style="background-image:url(&quot;{esc(s["src"])}&quot;)"></div>'
        return f'<div class="img {cls}"><div class="ph"><b>{esc(s["slot"] if s else "")}</b>背景图</div></div>'
    return (f'<div class="from">From the brand</div><div class="story">{layer(by.get("B0_Desktop"), "bg")}{layer(by.get("B0_Mobile"), "bgm")}'
            f'<div class="over"><div class="lead"><h2>{esc(bg.get("headline"))}</h2>{esc(bg.get("body"))}</div>{"".join(cards)}</div></div>')


def store_status(doc, slots):
    """Seven separate states; never one vague 'done'."""
    products = doc.get('products') or []
    tiles = [x.get('tile') or {} for x in slots if (x.get('tile') or {}).get('type', '').startswith('image_text')]
    ver = lambda key: bool(tiles) and all((t.get('verified') or {}).get(key) for t in tiles)  # noqa: E731
    return [('facts_ready', '事实就绪', bool(products) and all(p.get('status') in ('在售', '缺货') and not p.get('simulated') for p in products)),
            ('module_verified', '模块已实测', ver('module')),
            ('design_accepted', '设计已认可', bool(doc.get('accepted'))),
            ('upload_package_ready', '上传图齐', bool(slots) and all(x['state'] == 'returned' for x in slots)),
            ('backend_preview_verified', '后台双端预览', ver('desktop') and ver('mobile') and ver('link')),
            ('submitted', '已提交审核', bool(doc.get('submitted'))),
            ('published_verified', '上线已核', bool(doc.get('published')))]


def render_store(doc, by, product_src):
    brand = doc.get('brand') or {}
    pages = doc.get('pages') or []
    matrix = {p.get('asin'): (i, p) for i, p in enumerate(doc.get('products') or [])}
    tabs = ''.join(f'<button data-page="pg-{i}" aria-selected="{str(i == 1).lower()}">{esc(p.get("name"))}</button>' for i, p in enumerate(pages, 1))

    def product_cards(asins):
        cards = []
        for asin in asins:
            i, p = matrix.get(asin, (None, {'asin': asin}))
            cards.append(f'<div class="p">{picture(product_src.get(i), label=asin or "ASIN")}<b>{esc(p.get("name") or asin)}</b>'
                         f'<br>{esc(p.get("price"))}{" <span class=sim>模拟</span>" if p.get("simulated") else ""}</div>')
        return ''.join(cards)
    live = [p.get('asin') for p in doc.get('products') or [] if p.get('status') != '停售']
    body = []
    for p_i, page in enumerate(pages, 1):
        head = by.get(f'P{p_i}_Hero') or by.get('S0_Header')
        hero = picture(head, cls='img hero', label='页头').replace('</div>', '<div class="safe" hidden></div></div>', 1) if head else ''
        secs, n = [], 0
        for sec in page.get('sections') or []:
            tiles = []
            for tile in sec.get('tiles') or []:
                n += 1
                t = tile.get('type')
                slot = by.get(f'P{p_i}_T{n:02d}_{t}')
                if t == 'image_text_overlay':
                    ov = tile.get('overlay') or {}
                    panel = (f'<div class="ovpanel">{esc(tile.get("baked_text"))}</div>'
                             if tile.get('baked_text') and (slot is None or slot['state'] != 'returned') else '')
                    native = (f'<div class="ovnative {"pos-" + esc(ov.get("position") or "右上角")}"><b>{esc(tile.get("title"))}</b>'
                              + (f'<span class="btn">{esc(tile.get("button"))}</span>' if tile.get('button') else '') + '</div>')
                    pic = picture(slot).replace('</div>', panel + native + '</div>', 1) if slot else ''
                    tiles.append(f'<div class="tile ov">{pic}</div>')
                elif t in ('image_text_beside', 'image_text'):
                    btn = f'<span class="btn">{esc(tile.get("button"))}</span>' if tile.get('button') else ''
                    tiles.append(f'<div class="tile image_text">{picture(slot)}<div class="cap"><b>{esc(tile.get("title"))}</b>{esc(tile.get("body"))}<br>{btn}</div></div>')
                elif t == 'text':
                    tiles.append(f'<div class="tile txt"><b>{esc(tile.get("title"))}</b><p>{esc(tile.get("body"))}</p></div>')
                elif t == 'product':
                    tiles.append(f'<div class="tile" style="grid-column:1/-1"><div class="cap"><b>{esc(tile.get("title"))}</b></div>'
                                 f'<div class="pgrid">{product_cards(tile.get("asins") or [])}</div></div>')
                elif t == 'product_grid':
                    tiles.append(f'<div class="tile" style="grid-column:1/-1"><div class="cap"><b>{esc(tile.get("title"))}</b></div>'
                                 f'<div class="pgrid">{product_cards(tile.get("asins") or live)}</div></div>')
                else:
                    cap = f'<div class="cap"><b>{esc(tile.get("title"))}</b></div>' if tile.get('title') else ''
                    tiles.append(f'<div class="tile {esc(t)}">{picture(slot)}{cap}</div>')
            secs.append(f'<div class="sec {esc(sec.get("layout") or "full_width")}">{"".join(tiles)}</div>')
        body.append(f'<div class="page" id="pg-{p_i}"{"" if p_i == 1 else " hidden"}>{hero}{"".join(secs)}</div>')
    return (f'<div class="store"><header>{picture(by.get("S0_Logo"), cls="img logo", label="Logo")}<div><b style="font-size:20px">{esc(brand.get("name"))}</b>'
            f'<br><small>{esc(brand.get("tagline"))}</small></div></header><nav>{tabs}</nav>{"".join(body)}</div>')


def row(label, value, copy=True):
    value = '' if value is None else str(value)
    shown = esc(value) if value else '留空'
    button = f'<button class="copy" data-copy="{esc(value)}">复制</button>' if copy and value else '<span></span>'
    return f'<div class="row"><div class="k">{esc(label)}</div><div class="v{"" if value else " empty"}">{shown}</div>{button}</div>'


def file_card(s):
    if s is None:
        return ''
    size = f'{s["size"][0]}×{s["size"][1]}' if s.get('size') else ''
    return (f'<div class="file">{picture(s)}<b>{esc(s["file"] or s["slot"])}</b>{esc(size)} · '
            f'<span class="state-{s["state"]}">{STATE_WORD[s["state"]]}</span></div>')


def fill_story(doc, specs, by):
    bg = doc.get('background') or {}
    blocks = ['<div class="block"><h2>基本信息</h2>' + row('内容名称', content_name(doc, specs)) + row('语言', language_of(doc, specs)) + '</div>',
              f'<div class="block"><h2>B0 · 背景 <small>电脑 + 手机两张</small></h2><div class="files">{file_card(by.get("B0_Desktop"))}{file_card(by.get("B0_Mobile"))}</div>'
              + row('后台标题', bg.get('headline')) + row('后台正文', bg.get('body')) + '</div>']
    for m in doc.get('modules') or []:
        mid, t = str(m.get('id')), m.get('type')
        if t == 'logo':
            blocks.append(f'<div class="block"><h2>{esc(mid)} · Brand Logo and Description</h2>'
                          f'<div class="files">{file_card(by.get(mid + "_Logo"))}</div>{row("后台正文", m.get("body"))}</div>')
        elif t == 'focus_image':
            s = by.get(f'{mid}_{slug(m.get("name"), "Focus")}')
            if m.get('embedded_text') and not (m.get('headline') or m.get('body')):
                inner = row('图内文字', m.get('embedded_text'), copy=False) + '<p class="note">后台标题、正文留空；文字已在图中，不再粘贴。</p>'
            else:
                inner = row('后台标题', m.get('headline')) + row('后台正文', m.get('body'))
            blocks.append(f'<div class="block"><h2>{esc(mid)} · Brand Focus Image</h2><div class="files">{file_card(s)}</div>{inner}</div>')
        elif t in ('asin_showcase', 'store_showcase'):
            tiles = ''.join(f'<div class="file">{picture(by.get(tile_slot(mid, i, p)))}<b>{esc(p.get("position"))} · {esc(p.get("label"))}</b>'
                            f'{esc(by[tile_slot(mid, i, p)]["file"])}{row("ASIN", p.get("asin"))}</div>'
                            for i, p in enumerate(m.get('products') or [], 1))
            blocks.append(f'<div class="block"><h2>{esc(mid)} · Brand ASIN and Store Showcase</h2>{row("后台标题", m.get("headline"))}'
                          f'<div class="files">{tiles}</div><p class="note">店铺入口用后台原生功能选取当前品牌旗舰店，不在图上画按钮。</p></div>')
        elif t == 'qa':
            pairs = ''.join(row(f'Q{i}', p.get('q')) + row(f'A{i}', p.get('a')) for i, p in enumerate(m.get('pairs') or [], 1))
            blocks.append(f'<div class="block"><h2>{esc(mid)} · Brand Q&amp;A</h2>{pairs}</div>')
    apps = ''.join(row(a.get('product') or a.get('scope') or 'ASIN', a.get('asin')) for a in doc.get('application_asins') or [] if a.get('asin'))
    blocks.append('<div class="block"><h2>应用 ASIN 候选</h2><p class="note">按产品系列分开应用；在后台核对商品名称、颜色与套装后添加。</p>'
                  + (apps or '<p class="note">未填写。</p>') + '</div>')
    link = doc.get('store_link') or {}
    if link.get('reference_url'):
        blocks.append(f'<div class="block"><h2>店铺入口</h2>{row("旗舰店参考链接", link.get("reference_url"))}<p class="note">{esc(link.get("note"))}</p></div>')
    return blocks


def tile_rows(tile, specs):
    t = tile.get('type')
    node = specs['brand_store']['tiles'].get(t) or {}
    ov = tile.get('overlay') or {}
    link = tile.get('link') or {}
    target = {'asin': '商品 ', 'page': '店铺页面 ', 'none': ''}.get(link.get('type'), '') + str(link.get('target') or '')
    ver = tile.get('verified') or {}
    mark = lambda k: '✓' if ver.get(k) else '○'  # noqa: E731
    rows = row('后台模块', node.get('module') or t, copy=False)
    if t == 'image_text_overlay':
        rows += row('叠加设置', f'{ov.get("size", "中号")} · {ov.get("align", "左对齐")} · {ov.get("position", "右上角")}（以当前账号控件为准）', copy=False)
    if t.startswith('image_text') or t == 'text':
        rows += row('前缀', tile.get('prefix')) + row('标题', tile.get('title')) + row('正文', tile.get('body'))
    if tile.get('button'):
        rows += row('链接样式 · 按钮标题', tile.get('button'))
    if target.strip():
        rows += row('链接到', target)
    if tile.get('baked_text'):
        rows += row('图内文字（已在图里，不要再填进后台）', tile.get('baked_text'), copy=False)
    if tile.get('fallback'):
        rows += row('做不到时的替代', tile.get('fallback'), copy=False)
    if t.startswith('image_text'):
        rows += row('核验', f'模块 {mark("module")}　桌面 {mark("desktop")}　手机 {mark("mobile")}　链接 {mark("link")}', copy=False)
    return rows


def fill_store(doc, by, specs):
    brand = doc.get('brand') or {}
    mode = {'new_store': '从 0 建店', 'redesign': '已有店改版', 'partial': '局部优化'}.get(doc.get('mode'), doc.get('mode') or '未写')
    blocks = [f'<div class="block"><h2>全店 <small>{esc(mode)}</small></h2><div class="files">{file_card(by.get("S0_Logo"))}{file_card(by.get("S0_Header"))}</div>'
              + row('品牌口号', brand.get('tagline')) + row('元描述', brand.get('meta_description'))
              + (row('先验证的样块', doc.get('sample_block'), copy=False) + '<p class="note">先在后台把这一块做出来：检查模块、字段、链接、电脑和手机，认可后再做同类。</p>'
                 if doc.get('sample_block') else '') + '</div>']
    if doc.get('audit'):
        rows = ''.join(f'<tr><td>{esc(a.get("page"))}</td><td>{esc(a.get("observed"))}</td><td>{esc(a.get("action"))}</td><td>{esc(a.get("note"))}</td></tr>'
                       for a in doc['audit'])
        blocks.append(f'<div class="block"><h2>现店盘点</h2><table class="t"><tr><th>页面</th><th>现状</th><th>处理</th><th>说明</th></tr>{rows}</table></div>')
    for p_i, page in enumerate(doc.get('pages') or [], 1):
        parts, n = [], 0
        for s_i, sec in enumerate(page.get('sections') or [], 1):
            for tile in sec.get('tiles') or []:
                n += 1
                t = tile.get('type')
                if t in ('product', 'product_grid'):
                    asins = tile.get('asins') or ([p.get('asin') for p in doc.get('products') or [] if p.get('status') != '停售'] if t == 'product_grid' else [])
                    parts.append(f'<div class="tilecard"><h3>区块 {s_i} · 图块 {n} · {esc((specs["brand_store"]["tiles"].get(t) or {}).get("label", t))}</h3>'
                                 + row('后台模块', (specs['brand_store']['tiles'].get(t) or {}).get('module'), copy=False) + row('标题', tile.get('title'))
                                 + row('ASIN（逐个粘贴）', '\n'.join(a for a in asins if a)) + '<p class="note">粘贴后逐个核对实际显示的卡片数；少了先查可购性和身份，不为凑整齐删商品。</p></div>')
                    continue
                s = by.get(f'P{p_i}_T{n:02d}_{t}')
                parts.append(f'<div class="tilecard"><h3>区块 {s_i} · 图块 {n} · {esc((specs["brand_store"]["tiles"].get(t) or {}).get("label", t))}</h3>'
                             f'<div class="files">{file_card(s)}</div>{tile_rows(tile, specs)}</div>')
        head = by.get(f'P{p_i}_Hero')
        blocks.append(f'<div class="block"><h2>页面 {p_i} · {esc(page.get("name"))} <small>{esc(page.get("task"))}</small></h2>'
                      + (f'<div class="files">{file_card(head)}</div>' if head else '<p class="note">页头用全店共用 Header。</p>') + ''.join(parts) + '</div>')
    matrix = ''.join(f'<tr><td>{esc(p.get("asin"))}</td><td>{esc(p.get("name"))}</td><td>{esc(p.get("series"))}</td><td>{esc(p.get("variant"))}</td>'
                     f'<td>{esc(p.get("role"))}</td><td>{esc(p.get("status"))}{"（模拟）" if p.get("simulated") else ""}</td></tr>' for p in doc.get('products') or [])
    blocks.append(f'<div class="block"><h2>商品矩阵</h2><table class="t"><tr><th>ASIN</th><th>名称</th><th>系列</th><th>款式</th><th>角色</th><th>在售状态</th></tr>{matrix}</table>'
                  '<p class="note">长期主推和当前可购分开看；缺货的主力可以保留位置，停售款从店内入口、图文和商品模块一起移出。</p></div>')
    if doc.get('revision_log'):
        rows = ''.join(f'<tr><td>{esc(r.get("date"))}</td><td>{esc(r.get("feedback"))}</td><td>{esc(r.get("changed"))}</td><td>{esc(r.get("kept"))}</td><td>{esc(r.get("status"))}</td></tr>'
                       for r in doc['revision_log'])
        blocks.append(f'<div class="block"><h2>修改记录</h2><table class="t"><tr><th>日期</th><th>用户反馈</th><th>只改了</th><th>保留</th><th>状态</th></tr>{rows}</table></div>')
    return blocks


def page_html(doc, kind, specs, slots, by, errors, warnings, unmatched, cfg, product_src):
    style = doc.get('style') or {}
    pal = (style.get('palette') or []) + ['#1f3b36', '#f4efe6', '#c8a96a']
    css = CSS.replace('__P1__', pal[0]).replace('__P2__', pal[1]).replace('__P3__', pal[2])
    brand = (doc.get('brand') or {}).get('name') or ''
    preview = render_story(doc, by) if kind == 'story' else render_store(doc, by, product_src)
    fill = fill_story(doc, specs, by) if kind == 'story' else fill_store(doc, by, specs)
    fill.append('<div class="block"><h2>图片替代文本</h2>' + ''.join(row(s['file'] or s['slot'], s['owner'].get('alt')) for s in slots) + '</div>')
    fill.append('<div class="block"><p class="note">文件像素已按规格导出并核对；后台当前上传弹窗、审核结果与上线状态尚未验证。'
                '遇到裁剪或必填字段差异，截图后局部修改。当前只完成本地文件，未上传、未提交审核、未发布。</p></div>')
    done = sum(1 for s in slots if s['state'] == 'returned')
    items = lambda xs, cls: ''.join(f'<li class="{cls}">{esc(x)}</li>' for x in xs) or '<li>无</li>'  # noqa: E731
    status = ''.join(f'<li>{esc(s["slot"])} · {esc(s["where"])} · <span class="state-{s["state"]}">{STATE_WORD[s["state"]]}</span></li>' for s in slots)
    spec = specs['brand_story' if kind == 'story' else 'brand_store']
    check_panel = (f'<div class="block"><h2>必须改</h2><ul class="list">{items(errors, "state-none")}</ul><h2>建议改</h2><ul class="list">{items(warnings, "state-ref")}</ul></div>'
                   + (f'<div class="block"><h2>回填夹里没对上号的图</h2><ul class="list">{items(unmatched, "state-ref")}</ul></div>' if unmatched else '')
                   + f'<div class="block"><h2>图位</h2><ul class="list">{status}</ul></div>'
                   f'<div class="block"><h2>风格与核心词</h2><p>{esc(style.get("name"))}：{esc(style.get("why"))}</p><p>{esc("、".join(doc.get("keywords") or []))}</p>'
                   f'<p class="note">后台位置：{esc(spec["where"])}。尺寸按规格表导出，上传前以后台弹窗为准。</p></div>')
    pills = (f'<span class="pill">V{esc(doc.get("version", 1))}</span><span class="pill">{len(slots)} 张图 · 已出 {done}</span>'
             + (f'<span class="pill err">必须改 {len(errors)}</span>' if errors else '') + (f'<span class="pill warn">建议改 {len(warnings)}</span>' if warnings else ''))
    safe = '<button data-safe aria-pressed="false">安全区</button>' if kind == 'store' else ''
    chain = ''
    if kind == 'store':
        states = store_status(doc, slots)
        chain = '<div class="chain">' + ''.join(f'<span class="{"on" if ok else ""}">{"✓" if ok else "○"} {esc(label)}</span>' for _, label, ok in states) + '</div>'
        if not states[1][2]:
            chain += ('<p class="concept">概念预览：图文块的后台模块还没实测。先按「后台照填」把样块 '
                      f'{esc(doc.get("sample_block") or "")} 做出来，桌面、手机、链接都核过，再做其余同类块。</p>')
    return ('<!doctype html><html lang="zh-CN"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">'
            f'<title>{esc(cfg["title"])} · {esc(brand)} · 从这里开始</title><style>{css}</style></head><body>'
            f'<header class="top"><h1>{esc(brand)} · {esc(cfg["title"])} V{esc(doc.get("version", 1))}</h1><p>{esc(doc.get("revision_note"))}</p>'
            f'<div class="meta">{pills}</div>{chain}<div class="tabs"><button data-tab="t-preview" aria-selected="true">效果预览</button>'
            '<button data-tab="t-fill" aria-selected="false">后台照填</button><button data-tab="t-check" aria-selected="false">核对</button>'
            f'<span class="tools"><button data-view="desktop" aria-pressed="true">电脑端</button><button data-view="mobile" aria-pressed="false">手机端</button>{safe}</span></div></header>'
            f'<main><section class="panel" id="t-preview"><div class="frame">{preview}</div></section>'
            f'<section class="panel" id="t-fill" hidden>{"".join(fill)}</section><section class="panel" id="t-check" hidden>{check_panel}</section></main>'
            f'<div id="toast" role="status"></div><script>{JS}</script></body></html>')


# ---------------------------------------------------------------- text files

def csv_text(header, rows):
    buf = io.StringIO()
    writer = csv.writer(buf, lineterminator='\n')
    writer.writerow(header)
    writer.writerows(rows)
    return '﻿' + buf.getvalue()


def texts_story(doc, specs, by):
    bg = doc.get('background') or {}
    mods = doc.get('modules') or []
    lines = [f'{(doc.get("brand") or {}).get("name", "")} 品牌故事 V{doc.get("version", 1)}', f'内容名称：{content_name(doc, specs)}',
             f'语言：{language_of(doc, specs)}', '顺序：背景 B0 → ' + ' → '.join(str(m.get('id')) for m in mods),
             f'图片数量：{len(by)}；模块数量：{len(mods)}（不含背景）', '',
             'B0｜Brand Carousel Background', f'电脑背景：{by["B0_Desktop"]["file"]}', f'手机背景：{by["B0_Mobile"]["file"]}',
             f'标题：{bg.get("headline", "")}', f'正文：{bg.get("body", "")}', '']
    fill = {'content_name': content_name(doc, specs), 'language': language_of(doc, specs),
            'background': {'type': 'Brand Carousel Background', 'desktop': by['B0_Desktop']['file'], 'mobile': by['B0_Mobile']['file'],
                           'title': bg.get('headline', ''), 'body': bg.get('body', '')}, 'modules': []}
    grid_rows = []
    for m in mods:
        mid, t = str(m.get('id')), m.get('type')
        if t == 'logo':
            lines += [f'{mid}｜Brand Logo and Description', f'图片：{by[mid + "_Logo"]["file"]}', f'正文（{len(m.get("body") or "")} 字符）：', m.get('body', ''), '']
            fill['modules'].append({'id': mid, 'type': 'Brand Logo and Description', 'image': by[mid + '_Logo']['file'], 'body': m.get('body', '')})
        elif t == 'focus_image':
            s = by[f'{mid}_{slug(m.get("name"), "Focus")}']
            lines += [f'{mid}｜Brand Focus Image', f'图片：{s["file"]}']
            if m.get('embedded_text') and not (m.get('headline') or m.get('body')):
                lines += ['后台标题和正文留空；下面文字已在图中，不再粘贴：', m['embedded_text'], '']
            else:
                lines += [f'标题：{m.get("headline", "")}', f'正文：{m.get("body", "")}', '']
            fill['modules'].append({'id': mid, 'type': 'Brand Focus Image', 'image': s['file'], 'headline': m.get('headline', ''),
                                    'body': m.get('body', ''), 'embedded_text': m.get('embedded_text', '')})
        elif t in ('asin_showcase', 'store_showcase'):
            lines += [f'{mid}｜Brand ASIN and Store Showcase', f'后台标题：{m.get("headline", "")}']
            prods = []
            for i, p in enumerate(m.get('products') or [], 1):
                s = by[tile_slot(mid, i, p)]
                lines.append(f'{p.get("position", "")} | {p.get("asin", "")} | {p.get("label", "")} | {s["file"]}')
                item = {'position': p.get('position', ''), 'asin': p.get('asin', ''), 'label': p.get('label', ''), 'file': s['file'],
                        'alt_text': (p.get('image') or {}).get('alt', '')}
                prods.append(item)
                grid_rows.append(list(item.values()))
            lines.append('')
            fill['modules'].append({'id': mid, 'type': 'Brand ASIN and Store Showcase', 'headline': m.get('headline', ''), 'products': prods})
        elif t == 'qa':
            lines += [f'{mid}｜Brand Q&A'] + [f'Q{i}：{p.get("q", "")}\nA{i}：{p.get("a", "")}' for i, p in enumerate(m.get('pairs') or [], 1)] + ['']
            fill['modules'].append({'id': mid, 'type': 'Brand Q&A', 'pairs': m.get('pairs') or []})
    apps = [a for a in doc.get('application_asins') or [] if a.get('asin')]
    lines += ['应用 ASIN 候选：'] + [f'{a["asin"]} | {a.get("product", "")}' for a in apps] + ['']
    link = doc.get('store_link') or {}
    if link.get('reference_url'):
        lines += [f'店铺首页参考：{link["reference_url"]}', link.get('note', ''), '']
    lines += ['尺寸按规格表逐文件导出；未读取当前账户上传弹窗。若后台有不同限制，截图后局部适配。', '当前只完成本地文件，未上传、提交审核或发布。']
    fill['application_asin_candidates'] = apps
    files = {'后台可复制文案.txt': '﻿' + '\n'.join(lines) + '\n',
             '后台填写.json': json.dumps(fill, ensure_ascii=False, indent=2) + '\n',
             '应用ASIN清单.csv': csv_text(['asin', 'product', 'scope', 'note'], [[a.get('asin', ''), a.get('product', ''), a.get('scope', ''), a.get('note', '')] for a in apps])}
    if grid_rows:
        files['四格商品绑定.csv'] = csv_text(['position', 'asin', 'label', 'file', 'alt_text'], grid_rows)
    return files


def texts_store(doc, by, specs):
    brand = doc.get('brand') or {}
    lines = [f'{brand.get("name", "")} 品牌旗舰店 V{doc.get("version", 1)} 搭建步骤', '',
             f'Logo：{by["S0_Logo"]["file"]}' + (f'　共用页头：{by["S0_Header"]["file"]}' if 'S0_Header' in by else ''),
             f'品牌口号：{brand.get("tagline", "")}', f'元描述：{brand.get("meta_description", "")}',
             f'先验证的样块：{doc.get("sample_block", "")}', '']
    pages = []
    for p_i, page in enumerate(doc.get('pages') or [], 1):
        lines += [f'页面 {p_i}｜{page.get("name")}　任务：{page.get("task", "")}']
        secs, n = [], 0
        for s_i, sec in enumerate(page.get('sections') or [], 1):
            tiles = []
            for tile in sec.get('tiles') or []:
                n += 1
                t = tile.get('type')
                node = specs['brand_store']['tiles'].get(t) or {}
                s = by.get(f'P{p_i}_T{n:02d}_{t}')
                link = tile.get('link') or {}
                item = {'block': f'区块 {s_i} · 图块 {n}', 'module': node.get('module') or t, 'file': s['file'] if s else '',
                        'overlay': tile.get('overlay'), 'prefix': tile.get('prefix', ''), 'title': tile.get('title', ''), 'body': tile.get('body', ''),
                        'button': tile.get('button', ''), 'link': link, 'baked_text': tile.get('baked_text', ''),
                        'asins': tile.get('asins') or [], 'alt_text': (tile.get('image') or {}).get('alt', ''),
                        'fallback': tile.get('fallback', ''), 'verified': tile.get('verified') or {}}
                tiles.append(item)
                bits = [f'  {item["block"]}：{item["module"]}']
                if item['file']:
                    bits.append(f'上传 {item["file"]}')
                if tile.get('overlay'):
                    ov = tile['overlay']
                    bits.append(f'叠加 {ov.get("size", "")}/{ov.get("align", "")}/{ov.get("position", "")}')
                for key, label in (('prefix', '前缀'), ('title', '标题'), ('body', '正文'), ('button', '按钮')):
                    if item[key]:
                        bits.append(f'{label}：{item[key]}')
                if link.get('target'):
                    bits.append(f'链接：{link.get("type")} {link.get("target")}')
                if item['asins']:
                    bits.append('ASIN：' + ' '.join(item['asins']))
                if item['baked_text']:
                    bits.append('图内文字（不要再填进后台）：' + item['baked_text'].replace('\n', ' / '))
                lines.append('　'.join(bits))
            secs.append({'layout': sec.get('layout'), 'tiles': tiles})
        pages.append({'name': page.get('name'), 'task': page.get('task', ''), 'sections': secs})
        lines.append('')
    lines += ['每一块都要：电脑、手机分别预览；按钮文字和实际跳转一致；粘贴的 ASIN 与显示卡片数逐个对上。',
              '当前只完成本地文件，未在后台搭建、提交或发布。']
    prods = [[p.get('asin', ''), p.get('name', ''), p.get('series', ''), p.get('variant', ''), p.get('role', ''), p.get('status', ''),
              '模拟，上线前替换' if p.get('simulated') else p.get('evidence', '')] for p in doc.get('products') or []]
    files = {'搭建步骤.txt': '\ufeff' + '\n'.join(lines) + '\n',
             '页面结构.json': json.dumps({'brand': brand.get('name'), 'mode': doc.get('mode'), 'sample_block': doc.get('sample_block'),
                                      'logo': by['S0_Logo']['file'], 'header': by['S0_Header']['file'] if 'S0_Header' in by else '',
                                      'pages': pages}, ensure_ascii=False, indent=2) + '\n',
             '商品矩阵.csv': csv_text(['asin', 'name', 'series', 'variant', 'role', 'status', 'note'], prods)}
    if doc.get('audit'):
        files['现店盘点.csv'] = csv_text(['page', 'observed', 'action', 'note'], [[a.get(k, '') for k in ('page', 'observed', 'action', 'note')] for a in doc['audit']])
    if doc.get('revision_log'):
        files['修改记录.csv'] = csv_text(['date', 'feedback', 'changed', 'kept', 'status'],
                                     [[r.get(k, '') for k in ('date', 'feedback', 'changed', 'kept', 'status')] for r in doc['revision_log']])
    return files


def feed_pack(root, doc, slots, cfg):
    feed, back = root / S3 / cfg['feed'], root / S3 / cfg['back']
    if r03_enabled(root):
        feed.mkdir(parents=True,exist_ok=True);back.mkdir(parents=True,exist_ok=True)
        group=r03_feed_group(root,'story' if cfg['folder']=='amazon-brand-story-designer' else 'store')
        for name,source in group['pads'].items():shutil.copy2(source,feed/name)
        (feed/'提示词.txt').write_text('\n\n'.join(group['prompt_blocks'])+'\n',encoding='utf8')
        (feed/'投喂说明.md').write_text('在同一个网页对话中上传本目录垫图，再整段粘贴提示词.txt。用法不粘进出图提示。生成图片或ZIP原样放回“放这里”，不用预先改名；回填后进行同一套原生验收，未验收不交付。\n\n上传垫图：\n'+'\n'.join(group['pads']),encoding='utf8')
        return
    if feed.exists():
        shutil.rmtree(feed)
    feed.mkdir(parents=True)
    back.mkdir(parents=True, exist_ok=True)
    style = doc.get('style') or {}
    lines = [f'# {cfg["title"]}出图（整夹拖进 ChatGPT 网页端 / Codex App）', '',
             f'品牌：{(doc.get("brand") or {}).get("name", "")}　风格：{style.get("name", "")}　主色：{"、".join(style.get("palette") or [])}', '',
             '每个母版出一张图，文件名用**母版 / 图位编号**（如 S01.png、B0_Desktop.png）；同一母版用在多处时按表里最大的尺寸比例出，其余由脚本裁出。',
             '**后台标题和按钮由后台原生显示，不要画进图**；图内只放「图内文字」列的辅助文案。改图也在网页端 / App 里完成。全部出完打包成一个 zip 下载。', '',
             '| 母版 / 图位 | 用在 | 尺寸 | 参考图 | 出图提示词 | 图内文字 | 替代文本 |', '|---|---|---|---|---|---|---|']
    groups = {}
    for s in slots:
        key = s['owner'].get('master') or s['slot']
        groups.setdefault(key, []).append(s)
    for key, members in groups.items():
        first = members[0]
        ref = first['owner'].get('ref')
        ref_name = ''
        if ref and (root / ref).is_file():
            ref_name = f'{key}_ref{Path(ref).suffix.lower()}'
            shutil.copy2(root / ref, feed / ref_name)
        sizes = '、'.join(sorted({f'{m["size"][0]}×{m["size"][1]}' for m in members if m['size']})) or '—'
        baked = ((first.get('tile') or {}).get('baked_text') or '').replace('\n', ' / ')
        lines.append(f'| {key} | {"；".join(m["where"] for m in members)} | {sizes} | {ref_name or "—"} | '
                     f'{(first["owner"].get("prompt") or "").replace("|", "／")} | {baked or "—"} | {first["owner"].get("alt") or "—"} |')
    lines += ['', f'下载的 zip 或图片原样放进旁边的 `{cfg["back"]}/`，再回对话说「{cfg["say"]}」。', '']
    (feed / '00_把本文件夹全部上传.md').write_text('\n'.join(lines), encoding='utf-8')
    (back / '00_把网页端生成的图放这里.md').write_text('\n'.join([
        f'# {cfg["title"]}回填：网页端下载的图放这里', '',
        '- zip 或单张图原样拖进来，不用解压；文件名以图位编号开头（B0_Desktop… / H2_… / P1_Hero…）就能自动对上号。',
        f'- 放好后回对话说「{cfg["say"]}」：上传包里的图换成真图，并按后台尺寸导出到 `{IMAGES_DIR}/`。',
        '- 本文件夹不会被清掉。', '']), encoding='utf-8')


def archive_old(package, version):
    """Keep the previous version when the plan's version changes; otherwise rebuild in place."""
    marker = package / '.version'
    if package.is_dir() and marker.is_file():
        old = marker.read_text(encoding='utf-8').strip()
        if old and old != str(version):
            target = package.parent / HISTORY / f'{package.name}_v{old}'
            if target.exists():
                shutil.rmtree(target)
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.move(str(package), str(target))
            return target.name
    if package.exists():
        shutil.rmtree(package)
    return None




def r03_helper(name):
    path=HERE.parents[3]/'scripts'/(name+'.py')
    spec=importlib.util.spec_from_file_location('_brand_r03_'+name,path);m=importlib.util.module_from_spec(spec);spec.loader.exec_module(m);return m


def r03_enabled(root):
    path=Path(root)/S3/'amazon-listing-image-designer/visual_suite.yaml'
    return path.is_file() and load_yaml(path).get('schema_version')=='r03.s3'


def r03_safe(root,path):
    root=Path(root).resolve();out=(root/path).resolve();out.relative_to(root);return out


def r03_copy_fields(doc):
    visible={}
    def collect(value,path=''):
        if isinstance(value,dict):
            for key,child in value.items():
                if key in {'headline','body','embedded_text','on_image_copy','alt','tagline','story','meta_description','title','button','baked_text','prefix','q','a','label','name','description','intro','price','series','variant','product','scope','content_name'} and isinstance(child,str):visible[path+'.'+key]=child
                elif key not in {'brand_materials','r03_reviews','brand_copy_review'}:collect(child,path+'.'+key)
        elif isinstance(value,list):
            for i,child in enumerate(value):collect(child,path+'.'+str(i))
    collect(doc);return visible


def r03_copy_current(doc):
    visible=r03_copy_fields(doc);review=doc.get('brand_copy_review') or {}
    copy_hash=hashlib.sha256(json.dumps(visible,ensure_ascii=False,sort_keys=True).encode()).hexdigest()
    return (review.get('source') in {'user','model_review'} and bool(review.get('message')) and (review.get('source')!='model_review' or bool(review.get('task_id'))) and review.get('copy_sha256')==copy_hash and review.get('materials_sha256')==(doc.get('brand_materials') or {}).get('sha256') and review.get('facts_match') is True)


def r03_validate_facts(root,doc):
    material=doc.get('brand_materials') or {};brand=doc.get('brand') or {}
    if not brand.get('name') or any(x in brand['name'] for x in ['待定','<','请填写']):raise ValueError('品牌未定：先补本人品牌名、Logo 和介绍资料')
    if material.get('source')!='user' or not str(material.get('message') or '').strip() or not material.get('path'):raise ValueError('缺本人提交的品牌资料，不能编造品牌事实')
    source=r03_safe(root,material['path']);raw=source.read_bytes()
    if hashlib.sha256(raw).hexdigest()!=material.get('sha256'):raise ValueError('品牌资料变化，需要重新核对依据')
    facts=json.loads(raw) if source.suffix=='.json' else yaml.safe_load(raw);facts=facts.get('brand') or {}
    for key,value in brand.items():
        if key in {'name','story','intro','tagline','description','founded','awards'} and value and value!=facts.get(key):raise ValueError('品牌 '+key+' 缺资料依据，不能编造')
    if brand.get('logo'):
        logo=brand['logo'].get('ref') if isinstance(brand['logo'],dict) else brand['logo']
        given=facts.get('logo');given=given.get('ref') if isinstance(given,dict) else given
        if not logo or logo!=given or not r03_safe(root,logo).is_file():raise ValueError('Logo 必须是本人资料里的实际文件')
    r03_helper('product_manual').read_confirmed(root)
    return {'brand':facts,'source_sha256':material['sha256'],'source_path':material['path']}


def r03_contract(root,kind,doc,slots):
    facts=r03_validate_facts(root,doc)
    plan=load_yaml(Path(root)/S3/'amazon-listing-image-designer/visual_suite.yaml')
    variant=doc.get('r03_variant') or plan['variants'][0]
    rows=[]
    for slot in slots:
        rows.append({'slot_id':kind+'::'+slot['slot'],'canvas':list(slot['size']) if slot.get('size') else None,
            'device':'mobile' if 'mobile' in slot['slot'].lower() else 'desktop','variant':variant,
            'on_image_copy':slot['owner'].get('on_image_copy',''),'structure_ids':slot['owner'].get('structure_ids') or [],
            'props':slot['owner'].get('props') or [],'product_visible':slot['owner'].get('product_visible',slot['key']!='logo'),
            'file':slot['file'],'pad':slot['owner'].get('ref'),'prompt':slot['owner'].get('prompt','')})
    value={'kind':kind,'facts':facts,'plan_sha256':hashlib.sha256(json.dumps(doc,ensure_ascii=False,sort_keys=True).encode()).hexdigest(),'slots':rows}
    value['sha256']=hashlib.sha256(json.dumps(value,ensure_ascii=False,sort_keys=True).encode()).hexdigest()
    return value


def r03_read_slot(root,identity):
    kind,slot_id=identity.split('::',1)
    cfg=KINDS[kind];doc=load_yaml(Path(root)/S3/cfg['folder']/cfg['plan'])
    contract=r03_contract(root,kind,doc,slots_of(doc,kind,load_yaml(SPECS)))
    slot=next(row for row in contract['slots'] if row['slot_id']==identity)
    return slot,contract['sha256']


def r03_feed_group(root,kind):
    cfg=KINDS[kind];doc=load_yaml(Path(root)/S3/cfg['folder']/cfg['plan'])
    contract=r03_contract(root,kind,doc,slots_of(doc,kind,load_yaml(SPECS)));pads={};items=[];prompts=[]
    for row in contract['slots']:
        if not row['pad']:continue
        path=r03_safe(root,row['pad']);name=hashlib.sha256(path.read_bytes()).hexdigest()[:16]+path.suffix.lower();pads[name]=path
        items.append({'slot':row['slot_id'],'variant':row['variant'],'device':row['device'],'pad':name})
        manual=r03_helper('product_manual').read_confirmed(root)
        prompts.append('Slot '+row['slot_id']+' / '+row['device']+' / '+row['variant']+'\nVerified product identity and structure: '+json.dumps({'identity':manual['identity'],'structure':manual.get('structure') or []},ensure_ascii=False)+'\n'+row['prompt']+'\nUse the supplied reference only for actual product structure/color. Render exact approved copy: '+row['on_image_copy']+'. Do not invent brand history or structures. Display props require readable Tools not included. Target canvas '+str(row['canvas'])+'.')
    return {'items':items,'pads':pads,'prompt_blocks':prompts}


def r03_process_slot(root,kind,doc,slot,source,target):
    """Shared normalizer, actual OCR/review and final XMP precede shared atomic publication."""
    post=r03_helper('visual_postprocess');home=HERE.parents[3]
    path=home/'skills/_visual_suite_contract/scripts/visual_returns.py';spec=importlib.util.spec_from_file_location('_brand_returns',path);returns=importlib.util.module_from_spec(spec);spec.loader.exec_module(returns)
    contract=r03_contract(root,kind,doc,slots_of(doc,kind,load_yaml(SPECS)))
    row=next(r for r in contract['slots'] if r['slot_id']==kind+'::'+slot['slot'])
    base=Path(root)/S3/('出图_品牌故事' if kind=='story' else '出图_旗舰店')
    candidate=base/'02_回填/待验收'/row['file'];candidate.parent.mkdir(parents=True,exist_ok=True)
    normalization=post.normalize_image(source,candidate,'standard_aplus',dimensions=row['canvas'])
    shutil.copy2(candidate,target)
    review=(doc.get('r03_reviews') or {}).get(slot['slot']) or slot['owner'].get('review_ref')
    if not review:return {'status':'needs_visual_review','candidate':str(candidate),'brand_contract_sha256':contract['sha256'],'errors':['请实际查看当前品牌图片并绑定本次品牌资料/结构/人物复核']}
    refs={}
    try:
        mains=load_yaml(Path(root)/S3/'出图_主副图/media_manifest.yaml').get('items') or []
        refs={r['variant']:{'variant':r['variant'],'path':r['output_path'],'roi':r['normalization']['product_bbox']} for r in mains if r['slot_id']=='IMG01' and r.get('final_audit')}
    except (ValueError,KeyError):pass
    checked=post.inspect_image(root,candidate,row['slot_id'],row['variant'],row['device'],review,reference=refs)
    if checked['status']!='ready_to_finalize':
        repair=returns.repair_pack(root,kind,{k:row[k] for k in ['slot_id','variant','device']},checked['errors'],post.sha(source))
        return {**checked,'repair':repair}
    audit=post.finalize_image(root,candidate,row['slot_id'],row['variant'],row['device'],review,reference=refs)
    final=base/'02_回填/最终版'/row['file']
    result={k:row[k] for k in ['slot_id','variant','device']};result.update(output_path=final.relative_to(root).as_posix(),source_return_path=source.relative_to(root).as_posix(),normalization=normalization,sha256=post.sha(candidate),review_ref=review,final_audit=audit)
    returns.publish_candidate(root,candidate,base/'media_manifest.yaml',result)
    shutil.copy2(final,target) # Same basename and exact tagged bytes, no later image reencoding.
    return {'status':'pass','audit':audit,'final':str(final),'errors':[]}



def verify_r03_package(root,kind):
    root=Path(root).resolve();cfg=KINDS[kind];doc=load_yaml(root/S3/cfg['folder']/cfg['plan']);errors=[]
    try:
        contract=r03_contract(root,kind,doc,slots_of(doc,kind,load_yaml(SPECS)))
        if not r03_copy_current(doc):errors.append('品牌文案尚未完成当前资料事实复核')
        base=root/S3/('出图_品牌故事' if kind=='story' else '出图_旗舰店')
        rows=load_yaml(base/'media_manifest.yaml').get('items') or [];post=r03_helper('visual_postprocess')
        expected={(r['slot_id'],r['variant'],r['device']) for r in contract['slots']}
        actual=[(r['slot_id'],r['variant'],r['device']) for r in rows]
        if set(actual)!=expected or len(actual)!=len(expected):errors.append('品牌最终版尚未全部按当前规划验收')
        mains=load_yaml(root/S3/'出图_主副图/media_manifest.yaml').get('items') or []
        refs={r['variant']:{'variant':r['variant'],'path':r['output_path'],'roi':r['normalization']['product_bbox']} for r in mains if r['slot_id']=='IMG01' and r.get('final_audit')}
        for row in rows:
            file=r03_safe(root,row['output_path']);file.relative_to((base/'02_回填/最终版').resolve())
            if post.sha(file)!=row['sha256'] or post.sha(r03_safe(root,row['source_return_path']))!=row['normalization']['source_sha256']:errors.append('品牌成品或回填来源变化')
            checked=post.inspect_image(root,file,row['slot_id'],row['variant'],row['device'],row['review_ref'],reference=refs,final_proof=row['final_audit'])
            errors.extend(checked['errors'])
        return {'status':'fail' if errors else 'pass','errors':errors,'contract_sha256':contract['sha256']}
    except (OSError,ValueError,KeyError,TypeError) as error:return {'status':'fail','errors':[str(error)]}


def run(root, kind, init=False, force=False):
    root = Path(root).expanduser().resolve()
    if kind not in KINDS:
        raise ValueError('kind 只能是 story 或 store')
    cfg = dict(KINDS[kind])
    modern=r03_enabled(root)
    if modern:
        base='出图_品牌故事' if kind=='story' else '出图_旗舰店'
        cfg.update(feed=base+'/01_投喂包',back=base+'/02_回填/放这里')
        for part in ['01_投喂包','02_回填/放这里','02_回填/最终版','03_返修包']:(root/S3/base/part).mkdir(parents=True,exist_ok=True)
    folder = root / S3 / cfg['folder']
    plan = folder / cfg['plan']
    if init:
        if plan.exists() and not force:
            return {'ok': True, 'action': 'kept_existing', 'plan': str(plan)}
        folder.mkdir(parents=True, exist_ok=True)
        plan.write_text(yaml.safe_dump(skeleton(root, kind), allow_unicode=True, sort_keys=False), encoding='utf-8')
        return {'ok': True, 'action': 'initialized', 'plan': str(plan)}
    if not plan.is_file():
        raise ValueError(f'缺方案 {cfg["plan"]}：先 --init')
    raw = plan.read_bytes()
    doc = yaml.safe_load(raw.decode('utf-8-sig')) or {}
    if modern:
        r03_validate_facts(root,doc)
        visible=r03_copy_fields(doc)
        scanned=r03_helper('compliance_scan').scan_fields(visible,series=root)
        if scanned['blocked']:raise ValueError('品牌文案合规 block，先修正，不交付')
    specs = load_yaml(SPECS)
    slots = slots_of(doc, kind, specs)
    if modern:
        contract=r03_contract(root,kind,doc,slots)
        (folder/'品牌视觉规划.json').write_text(json.dumps(contract,ensure_ascii=False,indent=2),encoding='utf8')
    errors, warnings = check(doc, kind, specs, slots)
    if modern:
        copy_hash=hashlib.sha256(json.dumps(visible,ensure_ascii=False,sort_keys=True).encode()).hexdigest()
        if not r03_copy_current(doc):
            errors.append('品牌文案尚未对照本人资料及产品事实完成本次复核，不能编造品牌经历/奖项')
        (folder/'品牌文案待核.json').write_text(json.dumps({'copy_sha256':copy_hash,'materials_sha256':doc['brand_materials']['sha256'],'visible_copy':visible,'source':'current user brand material and product manual'},ensure_ascii=False,indent=2),encoding='utf8')
    package = folder / cfg['package']
    archived = archive_old(package, doc.get('version', 1))
    images = package / IMAGES_DIR
    images.mkdir(parents=True)
    if modern:
        bridge=HERE.parents[3]/'skills/_visual_suite_contract/scripts/visual_returns.py';spec=importlib.util.spec_from_file_location('_brand_zip',bridge);safe_returns=importlib.util.module_from_spec(spec);spec.loader.exec_module(safe_returns)
        for archive in sorted((root/S3/cfg['back']).glob('*.zip')):safe_returns.extract_images(root,archive,root/S3/cfg['back']/'_解压'/archive.stem)
        files=sorted(p for p in (root/S3/cfg['back']).rglob('*') if p.is_file() and p.suffix.lower() in IMG_EXT)
    else:files = returned_images(root / S3 / cfg['back'])
    used, manifest = set(), []
    for s in slots:
        hit = match_return(s['slot'], files) or (match_return(s['owner']['master'], files) if s['owner'].get('master') else None)
        s['state'], s['src'] = 'none', None
        if hit and s['size']:
            used.add(hit)
            w, h = export_image(hit, images / s['file'], s['size'], contain=(s['key'] == 'logo'))
            s['state'], s['src'] = 'returned', f'{IMAGES_DIR}/{s["file"]}'
            if s['key'] != 'logo' and abs(w / h - s['size'][0] / s['size'][1]) / (s['size'][0] / s['size'][1]) > 0.15:
                warnings.append(f'{s["slot"]} 回填图 {w}×{h} 与后台 {s["size"][0]}×{s["size"][1]} 比例相差大，导出时裁掉较多，建议按比例重出')
            if modern:
                checked=r03_process_slot(root,kind,doc,s,hit,images/s['file'])
                if checked['status']!='pass':errors.extend(s['slot']+' '+e for e in checked.get('errors') or ['该图片待验收'])
            data = (images / s['file']).read_bytes()
            manifest.append({'file': s['file'], 'width': s['size'][0], 'height': s['size'][1], 'bytes': len(data),
                             'sha256': hashlib.sha256(data).hexdigest(), 'alt_text': s['owner'].get('alt', ''),
                             'source_return': hit.relative_to(root).as_posix()})
        else:
            ref = s['owner'].get('ref')
            if ref and (root / ref).is_file():
                s['state'], s['src'] = 'ref', os.path.relpath(root / ref, package)
            manifest.append({'file': s['file'], 'status': '待出图', 'alt_text': s['owner'].get('alt', '')})
    for s in slots:
        if s['state'] != 'returned' and not s['owner'].get('prompt') and not s['owner'].get('ref'):
            warnings.append(f'{s["slot"]}（{s["where"]}）还没出图，也没有出图提示词或参考图')
    product_src = {}
    if kind == 'store':
        for i, p in enumerate(doc.get('products') or []):
            if p.get('image') and (root / p['image']).is_file():
                product_src[i] = {'slot': p.get('asin') or 'ASIN', 'state': 'ref', 'src': os.path.relpath(root / p['image'], package),
                                  'owner': {'alt': p.get('name')}, 'size': None}
    if modern and any(s['state']!='returned' for s in slots):errors.append('品牌图片尚未全部回填并验收，不是可交付成品')
    unmatched = [p.name for p in files if p not in used]
    by = {s['slot']: s for s in slots}
    feed_pack(root, doc, slots, cfg)
    page = page_html(doc, kind, specs, slots, by, errors, warnings, unmatched, cfg, product_src)
    (package / '从这里开始.html').write_text(page, encoding='utf-8', newline='\n')
    texts = package / cfg['texts']
    texts.mkdir()
    for name, content in (texts_story(doc, specs, by) if kind == 'story' else texts_store(doc, by, specs)).items():
        (texts / name).write_text(content, encoding='utf-8')
    (texts / '图片替代文本.csv').write_text(csv_text(['file', 'alt_text'], [[s['file'], s['owner'].get('alt', '')] for s in slots]), encoding='utf-8')
    (package / '图片清单与校验.json').write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    done = sum(1 for s in slots if s['state'] == 'returned')
    (package / '使用说明.txt').write_text('﻿' + '\n'.join([
        '打开 从这里开始.html：「效果预览」看成品，「后台照填」按顺序上传图片、复制文字，「核对」看还差什么。',
        f'本包共 {len(slots)} 张图，已出 {done} 张；图片在 {IMAGES_DIR}，文案在 {cfg["texts"]}。',
        f'还没出的图：用 S3_视觉板块/{cfg["feed"]}/ 去网页端生成，下载后放 S3_视觉板块/{cfg["back"]}/，再回对话说「{cfg["say"]}」。',
        '当前只完成本地文件，未上传、未提交审核、未发布。']) + '\n', encoding='utf-8')
    (package / '修改说明.txt').write_text('﻿' + f'V{doc.get("version", 1)}：{doc.get("revision_note", "")}\n', encoding='utf-8')
    (package / '.version').write_text(str(doc.get('version', 1)), encoding='utf-8')
    counts = {k: sum(1 for s in slots if s['state'] == k) for k in ('returned', 'ref', 'none')}
    status = {'generator': GENERATOR, 'kind': kind, 'version': doc.get('version', 1), 'plan_sha256': hashlib.sha256(raw).hexdigest(),
              'page_sha256': hashlib.sha256(page.encode('utf-8')).hexdigest(), 'package': package.relative_to(root).as_posix(),
              'errors': errors, 'warnings': warnings, 'slots': len(slots), 'counts': counts, 'unmatched_returns': unmatched,**({'r03_contract_sha256':contract['sha256']} if modern else {})}
    (folder / cfg['status']).write_text(json.dumps(status, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    return {'ok': not errors, 'start_here': str(package / '从这里开始.html'), 'package': str(package), 'archived': archived,
            'feed': str(root / S3 / cfg['feed']), 'back': str(root / S3 / cfg['back']),
            **{k: status[k] for k in ('errors', 'warnings', 'slots', 'counts', 'unmatched_returns')}}


def main(argv=None):
    parser = argparse.ArgumentParser(description='品牌故事 / 品牌旗舰店上传包')
    parser.add_argument('--run', required=True)
    parser.add_argument('--kind', required=True, choices=sorted(KINDS))
    parser.add_argument('--init', action='store_true')
    parser.add_argument('--force', action='store_true')
    args = parser.parse_args(argv)
    try:
        result = run(args.run, args.kind, args.init, args.force)
    except ValueError as error:
        print(json.dumps({'ok': False, 'error': str(error)}, ensure_ascii=False))
        return 1
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result.get('ok', True) else 2


if __name__ == '__main__':
    raise SystemExit(main())
