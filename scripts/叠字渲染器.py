#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
叠字渲染器 v1.0
====================================================================
把「无字底图」+「叠字任务表」渲染成可直接上架的成图。

为什么要有这个东西
--------------------------------------------------------------------
GPT-image-2 / Nano Banana 渲染文字，是每次重新臆造一遍像素：没有字体文件、
没有样式表、没有跨图记忆。所以七张副图的标题必然是七种字体、七个字号、
七个颜色 —— 这不是提示词写得不够细，是这类模型压根不提供「一致」这个能力。

本脚本把图拆成两层：
    第一层  底图（AI 出，非确定性）—— 产品 + 场景 + 光线，一个字都不许有
    第二层  叠字（代码画，确定性）—— 全部样式来自 视觉样式令牌.yaml

结果：整套图的文字像素级一致，是构造出来的，不是碰运气碰出来的。
附带好处：改错字不用重生成，改 YAML 重跑两秒，产品像素一个不动。

用法
--------------------------------------------------------------------
    python 叠字渲染器.py 叠字任务.yaml              # 渲染
    python 叠字渲染器.py 叠字任务.yaml --check       # 只过闸门，不出图
    python 叠字渲染器.py 叠字任务.yaml --item IMG03  # 只跑一张

退出码
--------------------------------------------------------------------
    0  全部通过
    1  有条目未过闸门（成图落到 _未通过/，不会污染正式目录）
    2  任务表/令牌文件本身有问题

作者：坚哥｜跨境者说干货 · 星空的跨境圈子｜Codex亚马逊实战
"""
from __future__ import annotations

import argparse
import platform
import sys
from datetime import datetime
from pathlib import Path

try:
    import yaml
    from PIL import Image, ImageDraw, ImageFont, ImageStat
except ImportError as e:                                    # pragma: no cover
    sys.exit(f"[缺依赖] {e}\n  请先安装：pip install pillow pyyaml")


# ==================================================================== 基础工具

def hex_to_rgb(h: str) -> tuple[int, int, int]:
    h = h.lstrip("#")
    if len(h) != 6:
        raise ValueError(f"颜色值必须是 #RRGGBB，收到 {h!r}")
    return tuple(int(h[i:i + 2], 16) for i in (0, 2, 4))     # type: ignore[return-value]


# 工具包自带字体目录（Inter / Archivo，SIL OFL，可随学员包分发）。
# 令牌里只写文件名即可，跨平台、跨产品目录都能找到。
BUNDLED_FONTS = Path(__file__).resolve().parent.parent / "assets" / "fonts"

# 字体解析：令牌路径 → 工具包自带 → 平台兜底。
# 学员可能在 Mac 上跑（设备 C），所以不能把 Windows 路径写死。
_FALLBACKS = {
    "Windows": ["C:/Windows/Fonts/arialbd.ttf", "C:/Windows/Fonts/arial.ttf",
                "C:/Windows/Fonts/segoeui.ttf"],
    "Darwin": ["/System/Library/Fonts/Supplemental/Arial Bold.ttf",
               "/System/Library/Fonts/Supplemental/Arial.ttf",
               "/System/Library/Fonts/Helvetica.ttc"],
    "Linux": ["/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
              "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"],
}


class FontBook:
    """按需加载并缓存字体，同时记录实际用到的文件，写进运行日志。"""

    def __init__(self, font_paths: dict, tokens_dir: Path):
        self.declared = font_paths
        self.tokens_dir = tokens_dir
        self._cache: dict[tuple[str, int], ImageFont.FreeTypeFont] = {}
        self.resolved: dict[str, str] = {}
        self.substituted: list[str] = []

    def _resolve(self, role: str) -> str:
        if role in self.resolved:
            return self.resolved[role]

        declared = self.declared.get(role, "")
        candidates = []
        if declared:
            p = Path(declared)
            if p.is_absolute():
                candidates.append((p, "declared"))
            else:
                candidates.append((self.tokens_dir / p, "declared"))    # 相对令牌文件
                candidates.append((BUNDLED_FONTS / p.name, "bundled"))  # 工具包自带（正常路径）
        candidates += [(Path(x), "platform")
                       for x in _FALLBACKS.get(platform.system(), [])]

        for c, kind in candidates:
            if c.exists():
                # 只有退回系统字体才值得警告 —— 那意味着成图观感和设计稿不一致。
                # 命中工具包自带字体是设计如此，不是替换。
                if kind == "platform":
                    self.substituted.append(
                        f"{role}: 找不到 {declared or '(未声明)'}，"
                        f"退回系统字体 {c.name} —— 成图字形会与设计稿不一致")
                self.resolved[role] = str(c)
                return str(c)

        raise SystemExit(
            f"[字体找不到] 角色 {role}，令牌里写的是 {declared!r}，平台兜底也全部缺失。\n"
            f"  请修 视觉样式令牌.yaml 的 fonts 段，或把字体文件放进 assets/fonts/")

    def get(self, role: str, size: int) -> ImageFont.FreeTypeFont:
        key = (role, size)
        if key not in self._cache:
            self._cache[key] = ImageFont.truetype(self._resolve(role), size)
        return self._cache[key]


def rel_luminance(rgb) -> float:
    """WCAG 相对亮度。"""
    def ch(v):
        v /= 255.0
        return v / 12.92 if v <= 0.04045 else ((v + 0.055) / 1.055) ** 2.4
    r, g, b = (ch(x) for x in rgb[:3])
    return 0.2126 * r + 0.7152 * g + 0.0722 * b


def contrast_ratio(a, b) -> float:
    la, lb = rel_luminance(a), rel_luminance(b)
    hi, lo = max(la, lb), min(la, lb)
    return (hi + 0.05) / (lo + 0.05)


def blend(fg, bg, alpha: float):
    return tuple(round(f * alpha + b * (1 - alpha)) for f, b in zip(fg, bg))


def region_stats(img, box):
    """返回 (平均RGB, 灰度标准差)。用 ImageStat，C 速度，不需要 numpy。"""
    x0, y0, x1, y1 = (max(0, int(box[0])), max(0, int(box[1])),
                      min(img.width, int(box[2])), min(img.height, int(box[3])))
    if x1 <= x0 or y1 <= y0:
        return None, None
    crop = img.crop((x0, y0, x1, y1))
    mean = tuple(round(v) for v in ImageStat.Stat(crop).mean[:3])
    sd = ImageStat.Stat(crop.convert("L")).stddev[0]
    return mean, sd


def text_width(draw, text: str, font, tracking_px: float) -> float:
    if not text:
        return 0.0
    return sum(draw.textlength(c, font=font) for c in text) + tracking_px * (len(text) - 1)


def draw_tracked(draw, xy, text: str, font, fill, tracking_px: float,
                 stroke: int = 0, stroke_fill=None) -> None:
    """逐字符绘制，PIL 原生不支持字距控制。
    stroke 用于视频字幕：叠在未知画面上时，描边比衬底更不挡画面。"""
    x, y = xy
    for c in text:
        if stroke:
            draw.text((x, y), c, font=font, fill=fill,
                      stroke_width=stroke, stroke_fill=stroke_fill)
        else:
            draw.text((x, y), c, font=font, fill=fill)
        x += draw.textlength(c, font=font) + tracking_px


def wrap(draw, text: str, font, tracking_px: float, max_px: float) -> list[str]:
    lines: list[str] = []
    cur = ""
    for w in text.split():
        trial = f"{cur} {w}".strip()
        if text_width(draw, trial, font, tracking_px) <= max_px or not cur:
            cur = trial
        else:
            lines.append(cur)
            cur = w
    if cur:
        lines.append(cur)
    return lines


# ==================================================================== 渲染器

class Renderer:
    def __init__(self, tokens_path: Path):
        try:
            self.t = yaml.safe_load(tokens_path.read_text(encoding="utf-8"))
        except Exception as e:
            sys.exit(f"[令牌文件读不了] {tokens_path}\n  {e}")
        self.tokens_path = tokens_path
        self.fonts = FontBook(self.t["fonts"], tokens_path.parent)
        self.log: list[dict] = []

    # ---------------------------------------------------------------- 辅助
    def color(self, name: str):
        try:
            return hex_to_rgb(self.t["palette"][name])
        except KeyError:
            raise SystemExit(f"[调色板里没有] {name!r}；"
                             f"可用：{', '.join(self.t['palette'])}")

    def _check_profile(self, item, overlays, fails):
        profile = item.get("profile")
        if not profile:
            return
        p = self.t["profiles"].get(profile)
        if p is None:
            fails.append(f"档位 {profile} 未在令牌里定义")
            return
        texts = [o for o in overlays if o.get("type", "text") == "text"]
        if len(texts) > p["max_text_blocks"]:
            fails.append(f"{profile} 档最多 {p['max_text_blocks']} 块文字，实际 {len(texts)} 块")
        for o in texts:
            if o["style"] not in p["allowed_styles"]:
                fails.append(f"{profile} 档不允许 {o['style']} 样式"
                             f"（可用：{', '.join(p['allowed_styles'])}）")
            if o.get("color") == "accent" and not p["accent_allowed"]:
                fails.append(f"{profile} 档不允许使用强调色")

    # ---------------------------------------------------------------- 主流程
    def render(self, item: dict, base: Path, check_only: bool):
        item_id = item["item_id"]
        fails: list[str] = []

        preset = self.t["canvas"]["presets"].get(item["canvas"])
        if preset is None:
            fails.append(f"未知画布 {item['canvas']!r}")
            self.log.append({"item_id": item_id, "status": "failed", "failures": fails})
            return fails
        W, H = preset["w"], preset["h"]
        gates = self.t["gates"]
        overlays = item.get("overlays", [])

        # 闸门 1 —— 主图不得叠字
        if item["canvas"] == "main" and overlays and gates["main_image_text_forbidden"]:
            fails.append(f"主图不得叠字（亚马逊主图规则），却有 {len(overlays)} 块内容")

        # 闸门 2 —— 档位样式与块数
        self._check_profile(item, overlays, fails)

        # plate: transparent —— 视频字幕模式。输出透明底 PNG，拖进剪映当叠加层。
        # 这样视频字幕与 Listing 图、A+ 共用同一份令牌，字体色板完全同源。
        transparent = str(item.get("plate", "")).strip().lower() == "transparent"

        if transparent:
            img = Image.new("RGBA", (W, H), (0, 0, 0, 0))
        else:
            plate_path = base / item["plate"]
            if not plate_path.exists():
                fails.append(f"底图不存在：{item['plate']}")
                self.log.append({"item_id": item_id, "status": "failed",
                                 "failures": fails})
                return fails
            img = Image.open(plate_path).convert("RGB")
            if img.size != (W, H):
                # 闸门 5 —— 底图比例必须与画布一致。
                # 轻微拉伸把产品压变形，肉眼不易察觉，直接就上架了。必须拦。
                src_ar, dst_ar = img.width / img.height, W / H
                if abs(src_ar - dst_ar) / dst_ar > 0.01:
                    fails.append(
                        f"底图 {img.width}x{img.height} 与画布 {W}x{H} 比例不符"
                        f"（{src_ar:.3f} vs {dst_ar:.3f}），拉伸会把产品压变形；"
                        f"请按画布比例重出底图，不要在这里凑合")
                img = img.resize((W, H), Image.LANCZOS)

        draw = ImageDraw.Draw(img, "RGBA")

        # ⛔ 只按宽度缩放对扁横幅/小画布都不对，见令牌 canvas.presets 的 type_scale 注释。
        #   A+ Desktop 1464×600 若不收，h1 会占到画布高度的 16.5%（副图上只占 5.4%）。
        k = W / self.t["canvas"]["reference_width"] * float(preset.get("type_scale", 1.0))
        m = gates["safe_margin_pct"] / 100.0
        safe = (W * m, H * m, W * (1 - m), H * (1 - m))
        drawn_bottom: dict[str, float] = {}

        for o in overlays:
            kind = o.get("type", "text")

            # ---------- 色带（B 档统一底栏用） ----------
            if kind == "band":
                x0, y0, bw, bh = (o["rect"][0] * W / 100, o["rect"][1] * H / 100,
                                  o["rect"][2] * W / 100, o["rect"][3] * H / 100)
                fill = self.color(o.get("color", "plate_bg"))
                alpha = int(round(o.get("opacity", 1.0) * 255))
                draw.rectangle([x0, y0, x0 + bw, y0 + bh], fill=(*fill, alpha))
                continue

            # ---------- 分隔线 ----------
            if kind == "rule":
                x0, y0 = o["rect"][0] * W / 100, o["rect"][1] * H / 100
                draw.rectangle([x0, y0, x0 + o["rect"][2] * W / 100,
                                y0 + max(1, round(o.get("thickness", 2) * k))],
                               fill=self.color(o.get("color", "rule")))
                continue

            # ---------- 文字 ----------
            style = self.t["type_scale"].get(o["style"])
            if style is None:
                fails.append(f"未知样式 {o['style']!r}")
                continue
            block = self.t["blocks"].get(o["block"])
            if block is None:
                fails.append(f"未知版式积木 {o['block']!r}")
                continue

            size = int(round(style["size"] * k))
            font = self.fonts.get(style["font"], size)
            tracking = style["tracking"] * size
            fill = self.color(o.get("color", style["color"]))
            text = o["text"].upper() if style["case"] == "upper" else o["text"]

            # 闸门 3 —— 缩略图可读性
            eff = style["size"] * gates["thumbnail_width"] / self.t["canvas"]["reference_width"]
            if eff < gates["min_thumbnail_px"]:
                fails.append(f"「{text[:22]}」缩到 {gates['thumbnail_width']}px 后仅 "
                             f"{eff:.1f}px，低于可读下限 {gates['min_thumbnail_px']}px")

            lines = wrap(draw, text, font, tracking, W * block["max_width"] / 100.0)
            lh = size * style["line_height"]

            follows = block.get("follows")
            if follows and follows in drawn_bottom:
                y = drawn_bottom[follows] + block.get("gap", 0.8) * size
            else:
                y = H * block["y"] / 100.0

            # 文字整块外接框：底图质量检查与衬底都按它算
            line_ws = [text_width(draw, ln, font, tracking) for ln in lines]
            widest = max(line_ws)
            ax0 = {"left": 0.0, "center": -widest / 2, "right": -widest}[block["align"]]
            bx0 = W * block["x"] / 100.0 + ax0
            bbox = (bx0, y, bx0 + widest, y + lh * len(lines))

            # 描边：视频字幕叠在未知画面上时，描边比衬底更不挡画面
            stroke_px = int(round(float(o.get("stroke", 0)) * k))
            if stroke_px:
                # 没指定描边色就自动取与文字对比的那一端
                default_stroke = "on_dark" if rel_luminance(fill) < 0.5 else "ink"
                stroke_col = self.color(o.get("stroke_color", default_stroke))
            else:
                stroke_col = None

            # 透明画布没有底图可测，改为强制要求描边或衬底
            if transparent:
                mean_rgb, sd = None, None
                if not stroke_px and not o.get("scrim"):
                    fails.append(
                        f"「{text[:22]}」是视频字幕但既无 stroke 也无 scrim；"
                        f"叠到未知画面上必然有读不清的镜头 —— "
                        f"加 stroke: 3（推荐）或 scrim: true")
            else:
                # ⛔ 必须在画衬底之前测，否则测到的是衬底自己
                mean_rgb, sd = region_stats(img, bbox)

            # 可选衬底：底图那块区域太花时保证文字能读
            scrim_rgb = None
            if o.get("scrim"):
                pad = o.get("scrim_pad", 0.5) * size
                scrim_rgb = self.color(o.get("scrim_color", "plate_bg"))
                draw.rectangle(
                    [bbox[0] - pad, bbox[1] - pad * 0.6, bbox[2] + pad, bbox[3] + pad * 0.4],
                    fill=(*scrim_rgb, int(round(o.get("scrim_opacity", 0.85) * 255))))

            # 闸门 6 / 7 —— 底图质量。
            # 这正是这套方案几个月前翻车的地方：模型给的底图留白区不够干净，
            # 字压上去糊成一片，只能靠人眼一张张看。现在脚本自己判。
            if mean_rgb is not None:
                if scrim_rgb is None:
                    eff_bg = mean_rgb
                    flat_max = gates.get("plate_flatness_max_stddev", 18.0)
                    min_cr0 = gates.get("min_contrast_ratio", 4.0)
                    cr_pre = contrast_ratio(fill, eff_bg)
                    # 平整度只是可读性的**代理指标**，真正决定读不读得清的是对比度。
                    # 2026-08-10 实拍浴室场景校准：副标压在窗影带上，平整度 21.7 超限，
                    # 但对比度 13.7:1（下限 4.0）—— 深色字压浅墙，实际清清楚楚。
                    # 只看平整度会把「有柔和光影的高级场景图」整片误杀。
                    # 规则：对比度达到下限 2 倍以上时，平整度上限放宽到 1.5 倍。
                    relax = gates.get("flatness_relax_when_high_contrast", 1.5)
                    eff_flat = flat_max * relax if cr_pre >= min_cr0 * 2 else flat_max
                    if sd > eff_flat:
                        note = (f"（对比度 {cr_pre:.1f}:1 已放宽上限至 {eff_flat:.0f}）"
                                if eff_flat > flat_max else "")
                        fails.append(
                            f"「{text[:22]}」落位处底图不平整（灰度标准差 {sd:.1f} > "
                            f"{eff_flat:.0f}）{note}；这块太花，字压上去读不清 —— "
                            f"让底图把该区域留干净，或给这块加 scrim: true")
                else:
                    eff_bg = blend(scrim_rgb, mean_rgb, o.get("scrim_opacity", 0.85))
                min_cr = gates.get("min_contrast_ratio", 4.0)
                cr = contrast_ratio(fill, eff_bg)
                if cr < min_cr:
                    fails.append(
                        f"「{text[:22]}」与背景对比度只有 {cr:.2f}:1，低于下限 {min_cr}:1；"
                        f"换文字色或加 scrim")

            for line, lw in zip(lines, line_ws):
                ax = {"left": 0.0, "center": -lw / 2, "right": -lw}[block["align"]]
                x = W * block["x"] / 100.0 + ax

                # 闸门 4 —— 安全区
                if x < safe[0] - 1 or x + lw > safe[2] + 1:
                    fails.append(f"「{line[:22]}」越过左右安全区（{gates['safe_margin_pct']}%）")
                if y < safe[1] - 1 or y + lh > safe[3] + 1:
                    fails.append(f"「{line[:22]}」越过上下安全区（{gates['safe_margin_pct']}%）")

                draw_tracked(draw, (x, y), line, font, fill, tracking,
                             stroke_px, stroke_col)
                y += lh

            drawn_bottom[o["block"]] = y
            if follows:
                drawn_bottom[follows] = y

        # ---------------------------------------------------------- 落盘
        status = "failed" if fails else "ok"
        out = base / item["output"]
        if fails:
            out = out.parent / "_未通过" / out.name          # 不污染正式目录
        if not check_only:
            out.parent.mkdir(parents=True, exist_ok=True)
            img.save(out)

        self.log.append({
            "item_id": item_id,
            "status": status,
            "canvas": item["canvas"],
            "profile": item.get("profile"),
            "plate": item["plate"],
            "output": str(out.relative_to(base)) if not check_only else None,
            "overlay_count": len(overlays),
            "failures": fails,
        })
        return fails


# ==================================================================== 入口

def main() -> None:
    ap = argparse.ArgumentParser(description="叠字渲染器：无字底图 + 叠字任务表 → 成图")
    ap.add_argument("task", help="叠字任务 YAML")
    ap.add_argument("--check", action="store_true", help="只过闸门，不出图")
    ap.add_argument("--item", help="只处理指定 item_id")
    args = ap.parse_args()

    task_path = Path(args.task).resolve()
    if not task_path.exists():
        sys.exit(f"[任务表不存在] {task_path}")
    try:
        spec = yaml.safe_load(task_path.read_text(encoding="utf-8"))
    except Exception as e:
        sys.exit(f"[任务表解析失败] {task_path}\n  {e}")

    base = task_path.parent
    tokens = base / spec["style_tokens"]
    if not tokens.exists():
        sys.exit(f"[令牌文件不存在] {tokens}")

    r = Renderer(tokens)
    items = spec.get("items", [])
    if args.item:
        items = [i for i in items if i["item_id"] == args.item]
        if not items:
            sys.exit(f"[任务表里没有] {args.item}")

    mode = "闸门检查" if args.check else "渲染"
    print(f"{mode} · 令牌 {tokens.name} · 共 {len(items)} 条\n")

    total_fail = 0
    for it in items:
        fails = r.render(it, base, args.check)
        if fails:
            total_fail += 1
            print(f"  ✗ {it['item_id']}")
            for f in fails:
                print(f"      {f}")
        else:
            print(f"  ✓ {it['item_id']}")

    for note in r.fonts.substituted:
        print(f"\n  ! 字体替换 —— {note}")

    # 运行日志：和 visual_prompt_iterations 一样留痕，方便回溯
    if not args.check:
        log_path = base / spec.get("run_log", "叠字渲染日志.yaml")
        log_path.write_text(yaml.safe_dump({
            "generated_at": datetime.now().isoformat(timespec="seconds"),
            "style_tokens": str(tokens.name),
            "fonts_resolved": r.fonts.resolved,
            "font_substitutions": r.fonts.substituted,
            "items": r.log,
        }, allow_unicode=True, sort_keys=False), encoding="utf-8")
        print(f"\n运行日志 → {log_path.name}")

    ok = len(items) - total_fail
    print(f"\n通过 {ok}/{len(items)}")
    if total_fail:
        print("未通过的成图放在 _未通过/，正式目录未被覆盖。")
        sys.exit(1)
    print("闸门全部通过：主图无叠字 / 档位样式 / 缩略图可读性 / 安全区 / 底图比例")
    sys.exit(0)


if __name__ == "__main__":
    main()
