#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
视频规格校验 v1.0
====================================================================
把成片逐条对着投放位规格核一遍。

为什么需要
--------------------------------------------------------------------
同一条产品视频要投三个地方，三套规格：主图视频、A+ 视频模块、SBV 广告。
时长、画幅、分辨率、码率、文件大小、音轨要求都不一样，
而这些错**上传时才被拒**，那时候剪映工程可能已经关了。

更隐蔽的是画幅：16:9 的片子传到只认 16:9 的位没问题，
但如果按 9:16 出了主图视频，详情页是两侧加黑边显示 —— 不报错，就是难看。

规格来源
--------------------------------------------------------------------
⛔ 本脚本**不硬编码任何规格**，全部读
   <home>/templates/video_placement_specs.yaml（或产品目录内的副本）。
   亚马逊规格会变，各家资料说法也不完全一致 —— 改那份 YAML，不要改这个脚本。

用法
--------------------------------------------------------------------
    python 视频规格校验.py {产品根}/video_exports
    python 视频规格校验.py <目录> --specs <规格文件>
    python 视频规格校验.py --card              # 只打印规格卡，不校验

文件命名约定（用于自动识别投放位）：
    {任意前缀}__{placement_key}.mp4
    例：dog_raincoat__listing_main.mp4 / dog_raincoat__sbv_horizontal.mp4

退出码：0 全通过｜1 有片子不合规｜2 配置或依赖问题

作者：坚哥｜跨境者说干货 · 星空的跨境圈子｜Codex亚马逊实战
"""
from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import sys
from datetime import datetime
from pathlib import Path

try:
    import yaml
except ImportError:                                          # pragma: no cover
    sys.exit("[缺依赖] 请先安装：pip install pyyaml")

DEFAULT_SPECS = (Path(__file__).resolve().parent.parent
                 / "templates" / "video_placement_specs.example.yaml")
VIDEO_EXT = {".mp4", ".mov", ".m4v"}
# 这些目录里的是原料和中间产物，不是交付件，别拿投放位规格去套
SKIP_DIRS = {"clips", "subtitles", "_未通过", "_色彩校正", "temp", "tmp", "raw"}
# 母版是留档用的，本身不投任何位
MASTER_PREFIXES = ("master",)


# ------------------------------------------------------- ffprobe

def have_ffprobe() -> bool:
    return shutil.which("ffprobe") is not None


def probe(path: Path) -> dict | None:
    try:
        r = subprocess.run(
            ["ffprobe", "-v", "error", "-print_format", "json",
             "-show_format", "-show_streams", str(path)],
            capture_output=True, text=True, encoding="utf-8", errors="replace",
            timeout=120)
        if r.returncode != 0:
            return None
        return json.loads(r.stdout)
    except (OSError, ValueError, subprocess.SubprocessError):
        return None


def parse_fps(s: str) -> float:
    try:
        if "/" in s:
            n, d = s.split("/")
            return float(n) / float(d) if float(d) else 0.0
        return float(s)
    except (ValueError, ZeroDivisionError):
        return 0.0


def approx(a: float, b: float, tol: float = 0.02) -> bool:
    return b and abs(a - b) / b <= tol


# ------------------------------------------------------- 校验

def check(path: Path, spec: dict) -> tuple[list[str], list[str], dict]:
    """返回 (错误, 提醒, 实测值)。"""
    errs: list[str] = []
    warns: list[str] = []
    info: dict = {}

    size_mb = path.stat().st_size / 1024 / 1024
    info["file_mb"] = round(size_mb, 1)
    if size_mb > spec["max_file_mb"]:
        errs.append(f"文件 {size_mb:.0f}MB 超过上限 {spec['max_file_mb']}MB")

    ext = path.suffix.lstrip(".").lower()
    info["container"] = ext
    if ext not in [c.lower() for c in spec["container"]]:
        errs.append(f"容器 .{ext} 不在允许列表 {spec['container']}")

    data = probe(path)
    if data is None:
        errs.append("ffprobe 读不出这个文件（损坏，或不是有效视频）")
        return errs, warns, info

    v = next((s for s in data.get("streams", []) if s.get("codec_type") == "video"), None)
    a = next((s for s in data.get("streams", []) if s.get("codec_type") == "audio"), None)
    if v is None:
        errs.append("没有视频轨")
        return errs, warns, info

    w, h = int(v.get("width", 0)), int(v.get("height", 0))
    info["resolution"] = f"{w}x{h}"
    if w < spec["min_width"] or h < spec["min_height"]:
        errs.append(f"分辨率 {w}x{h} 低于下限 "
                    f"{spec['min_width']}x{spec['min_height']}")

    want_w, want_h = spec["width"], spec["height"]
    if h and not approx(w / h, want_w / want_h):
        errs.append(f"画幅 {w}x{h}（{w/h:.3f}）与要求的 {spec['aspect_ratio']}"
                    f"（{want_w/want_h:.3f}）不符 —— 平台会加黑边或直接拒审")

    codec = (v.get("codec_name") or "").lower()
    info["video_codec"] = codec
    if codec not in [c.lower() for c in spec["video_codec"]]:
        errs.append(f"视频编码 {codec} 不在允许列表 {spec['video_codec']}")

    # ⛔ 优先看 r_frame_rate（容器声明的恒定帧率，平台核的是这个）。
    #   avg_frame_rate 是「实际帧数 ÷ 时长」算出来的平均值，
    #   哪怕是标准 30fps CFR 也常算出 29.81 之类，拿它判会误报。
    fps = parse_fps(v.get("r_frame_rate") or v.get("avg_frame_rate") or "0")
    avg = parse_fps(v.get("avg_frame_rate") or "0")
    info["fps"] = round(fps, 3)
    if avg and abs(avg - fps) / fps > 0.02:
        info["fps_avg"] = round(avg, 3)
    if not any(approx(fps, float(f), 0.005) for f in spec["fps_allowed"]):
        errs.append(f"帧率 {fps:.3f} 不在允许列表 {spec['fps_allowed']}")

    dur = float(data.get("format", {}).get("duration", 0) or 0)
    info["duration_s"] = round(dur, 2)
    dur_ok = spec["duration_min_s"] <= dur <= spec["duration_max_s"]
    if not dur_ok:
        errs.append(f"时长 {dur:.1f}s 不在 "
                    f"{spec['duration_min_s']}–{spec['duration_max_s']}s 之内")
    sweet = spec.get("duration_sweet_spot_s")
    # 硬范围都没过就别再提甜点区，否则一条说超范围一条说在范围内，自相矛盾
    if dur_ok and sweet and not (sweet[0] <= dur <= sweet[1]):
        warns.append(f"时长 {dur:.1f}s 在允许范围内，但实测 {sweet[0]}–{sweet[1]}s 完播更好")

    vbr = int(v.get("bit_rate") or 0) // 1000
    if not vbr:
        total = int(data.get("format", {}).get("bit_rate") or 0) // 1000
        vbr = max(0, total - (int(a.get("bit_rate") or 0) // 1000 if a else 0))
    info["video_kbps"] = vbr
    if vbr and vbr < spec.get("min_video_bitrate_kbps", 0):
        errs.append(f"视频码率 {vbr}kbps 低于下限 "
                    f"{spec['min_video_bitrate_kbps']}kbps")

    if a is None:
        info["audio"] = "无"
        msg = "没有音轨"
        (errs if spec.get("audio_required") else warns).append(
            msg + ("（本投放位要求必须有音轨）" if spec.get("audio_required")
                   else "（本位不强制，但建议加）"))
    else:
        ac = (a.get("codec_name") or "").lower()
        sr = int(a.get("sample_rate") or 0)
        abr = int(a.get("bit_rate") or 0) // 1000
        info["audio"] = f"{ac} {sr}Hz {abr}kbps"
        if ac not in [c.lower() for c in spec["audio_codec"]]:
            errs.append(f"音频编码 {ac} 不在允许列表 {spec['audio_codec']}")
        if sr and sr < spec.get("min_audio_sample_rate", 0):
            errs.append(f"音频采样率 {sr}Hz 低于 {spec['min_audio_sample_rate']}Hz")
        if abr and abr < spec.get("min_audio_bitrate_kbps", 0):
            errs.append(f"音频码率 {abr}kbps 低于 {spec['min_audio_bitrate_kbps']}kbps")

    return errs, warns, info


# ------------------------------------------------------- 规格卡

def print_card(specs: dict) -> None:
    print("投放位规格卡"
          f"（规格文件核实日期 {specs.get('last_verified', '未标注')}）\n")
    for key, s in specs["placements"].items():
        if s.get("enabled") is False:
            print(f"  [{key}] {s['display_name']}  —— 已停用（enabled: false）")
            continue
        sweet = s.get("duration_sweet_spot_s")
        print(f"  [{key}] {s['display_name']}")
        print(f"      画幅 {s['aspect_ratio']}  推荐 {s['width']}x{s['height']}"
              f"  下限 {s['min_width']}x{s['min_height']}")
        print(f"      时长 {s['duration_min_s']}–{s['duration_max_s']}s"
              + (f"（{sweet[0]}–{sweet[1]}s 更好）" if sweet else ""))
        print(f"      ≤{s['max_file_mb']}MB · {'/'.join(s['container'])}"
              f" · {'/'.join(s['video_codec'])} · {s['fps_allowed']}fps")
        for n in s.get("notes", []):
            print(f"      · {n}")
        print()


# ------------------------------------------------------- 入口

def main() -> None:
    ap = argparse.ArgumentParser(description="视频规格校验：成片逐条对投放位规格")
    ap.add_argument("target", nargs="?", help="成片目录（video_exports/）")
    ap.add_argument("--specs", help="规格 YAML，默认用 templates 里的样例")
    ap.add_argument("--placement", help="强制指定投放位（目录内文件名没带 __key 时用）")
    ap.add_argument("--card", action="store_true", help="只打印规格卡")
    args = ap.parse_args()

    sp = Path(args.specs) if args.specs else DEFAULT_SPECS
    if not sp.exists():
        sys.exit(f"[规格文件不存在] {sp}")
    specs = yaml.safe_load(sp.read_text(encoding="utf-8"))

    if args.card or not args.target:
        print_card(specs)
        if not args.target:
            print("提示：给一个成片目录即可开始校验，例如")
            print("      python 视频规格校验.py {产品根}/video_exports")
        sys.exit(0)

    root = Path(args.target)
    if not root.exists():
        sys.exit(f"[目录不存在] {root}")

    if not have_ffprobe():
        print("⚠️  没找到 ffprobe，无法读取视频实际参数。\n")
        print_card(specs)
        print("装上后本脚本即可自动校验（Windows）：")
        print("      winget install --id Gyan.FFmpeg -e\n")
        print("在此之前，请对照上面的规格卡在剪映导出设置里手工核对。")
        sys.exit(0)

    vids = sorted(
        p for p in root.rglob("*")
        if p.is_file() and p.suffix.lower() in VIDEO_EXT
        and not (SKIP_DIRS & set(p.relative_to(root).parts[:-1]))
        and not p.stem.lower().startswith(MASTER_PREFIXES))
    if not vids:
        print(f"{root} 下没有找到视频文件（{'/'.join(sorted(VIDEO_EXT))}）")
        sys.exit(0)

    print(f"视频规格校验 · 规格核实日期 {specs.get('last_verified', '未标注')}"
          f" · 共 {len(vids)} 个文件\n")

    rows, bad = [], 0
    for p in vids:
        key = args.placement
        if not key:
            stem = p.stem
            key = next((k for k in specs["placements"] if stem.endswith(f"__{k}")), None)
        if key is None:
            print(f"  – {p.name}  未识别投放位，跳过")
            print(f"      文件名请写成 xxx__<placement>.mp4，"
                  f"或加 --placement 指定")
            rows.append({"file": p.name, "status": "unknown_placement"})
            continue
        spec = specs["placements"].get(key)
        if spec is None:
            print(f"  ✗ {p.name}  规格文件里没有投放位 {key!r}")
            bad += 1
            continue
        if spec.get("enabled") is False:
            print(f"  – {p.name}  投放位 {key} 已停用，跳过")
            continue

        errs, warns, info = check(p, spec)
        mark = "✗" if errs else "✓"
        print(f"  {mark} {p.name}  [{key}]")
        print(f"      {info.get('resolution','?')} · {info.get('duration_s','?')}s"
              f" · {info.get('fps','?')}fps · {info.get('video_codec','?')}"
              f" · {info.get('file_mb','?')}MB · 音轨 {info.get('audio','?')}")
        for e in errs:
            print(f"      ✗ {e}")
        for w in warns:
            print(f"      ! {w}")
        rows.append({"file": p.name, "placement": key,
                     "status": "failed" if errs else "ok",
                     "measured": info, "errors": errs, "warnings": warns})
        if errs:
            bad += 1

    out = root / "视频规格校验报告.yaml"
    out.write_text(yaml.safe_dump({
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "specs_file": str(sp),
        "specs_last_verified": specs.get("last_verified"),
        "items": rows,
    }, allow_unicode=True, sort_keys=False), encoding="utf-8")
    print(f"\n报告 → {out.name}")

    if bad:
        print(f"\n不合规 {bad}/{len(vids)} —— 修好再上传，"
              f"平台拒审的返工成本比这里高得多。")
        sys.exit(1)
    print(f"\n全部通过 {len(vids)}/{len(vids)}")
    sys.exit(0)


if __name__ == "__main__":
    main()
