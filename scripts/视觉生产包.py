#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
视觉生产包 v1.0
====================================================================
把「出图」这一步从 Codex 里搬出去：Codex 只负责出提示词和挑垫图，
真正生图在网页端做，图片下载回来再跑本地闸门。

为什么
--------------------------------------------------------------------
在 Codex 内逐张生图有三重开销：
  ① 每张一轮完整 agent 推理（规划 → 写 prompt → 调用 → 查画幅 → 复核）
  ② 生成的图**永久留在上下文里**，之后每一轮都要重新处理 —— 越跑越慢
  ③ 改图靠文字描述来回猜，不如网页端所见即所得

这正是本工具包 `amazon-product-video-director` 早就在用的模式：
「Codex 负责包，站外负责生成，本地负责验收」。图片链照搬。

⛔ 它治不了「模型把产品画错」—— 那是提示词的事，换个界面还是同一个模型。
   见 references/多产品同框出图约束.md。

用法
--------------------------------------------------------------------
    python 视觉生产包.py 生产包配置.yaml

产出
--------------------------------------------------------------------
    image_production/
      START.md            人类操作卡：传什么、按什么顺序、粘什么、存到哪
      refs/               要上传的参考图，已按上传顺序编号
      prompts/M2D.txt     每张一个纯文本提示词，直接复制
      回填清单.yaml        下载后放哪、叫什么，供本地闸门读取

作者：坚哥｜跨境者说干货 · 星空的跨境圈子｜Codex亚马逊实战
"""
from __future__ import annotations

import shutil
import importlib.util
import json
import sys
from datetime import date
from pathlib import Path

try:
    import yaml
except ImportError:                                          # pragma: no cover
    sys.exit("[缺依赖] pip install pyyaml")


def main() -> None:
    if len(sys.argv)>2 and sys.argv[2] in {'prepare','accept','check'}:
        path=Path(__file__).resolve().parents[1]/'skills/_visual_suite_contract/scripts/visual_returns.py'
        spec=importlib.util.spec_from_file_location('_existing_visual_returns',path)
        returns=importlib.util.module_from_spec(spec);spec.loader.exec_module(returns)
        raise SystemExit(returns.main(sys.argv[1:]))
    if len(sys.argv) < 2:
        sys.exit("用法: python 视觉生产包.py 生产包配置.yaml")
    cfg_path = Path(sys.argv[1]).resolve()
    if not cfg_path.exists():
        sys.exit(f"[配置不存在] {cfg_path}")
    cfg = yaml.safe_load(cfg_path.read_text(encoding="utf-8"))
    base = cfg_path.parent
    product_root = Path(cfg['product_root']).resolve() if cfg.get('product_root') else next((
        p for p in (base,*base.parents)
        if (p/'S3_视觉板块/amazon-listing-image-designer/visual_suite.yaml').is_file()), None)
    if product_root:
        packer_path=Path(__file__).resolve().parents[1]/'skills/_visual_suite_contract/scripts/pack_gpt_web_feeds.py'
        spec=importlib.util.spec_from_file_location('_existing_visual_packer',packer_path)
        packer=importlib.util.module_from_spec(spec);spec.loader.exec_module(packer)
        if packer._modern_plan(product_root):
            print(json.dumps(packer.summary(packer.pack(product_root)),ensure_ascii=False));return
    out = base / cfg.get("output_dir", "image_production")

    for sub in ("refs", "prompts"):
        (out / sub).mkdir(parents=True, exist_ok=True)

    # ---------- 参考图：按上传顺序编号 ----------
    refs = cfg.get("refs", [])
    if not refs:
        sys.exit("[配置缺 refs] 至少要有参考图；比例基准图必须排第 1")
    ref_rows = []
    for i, r in enumerate(refs, start=1):
        src = base / r["file"]
        if not src.exists():
            sys.exit(f"[参考图不存在] {r['file']}")
        dst = out / "refs" / f"{i:02d}_{src.name}"
        shutil.copy2(src, dst)
        ref_rows.append((i, dst.name, r.get("note", "")))
        print(f"  参考图 {i:02d}  {src.name}")

    # ---------- 提示词 ----------
    items = cfg.get("items", [])
    if not items:
        sys.exit("[配置缺 items]")
    backfill = []
    for it in items:
        pid = it["id"]
        txt = it.get("prompt")
        if not txt and it.get("prompt_file"):
            pf = base / it["prompt_file"]
            if not pf.exists():
                sys.exit(f"[提示词文件不存在] {it['prompt_file']}")
            txt = pf.read_text(encoding="utf-8")
        if not txt:
            sys.exit(f"[{pid} 没有 prompt 或 prompt_file]")
        (out / "prompts" / f"{pid}.txt").write_text(txt.strip() + "\n", encoding="utf-8")
        backfill.append({
            "id": pid,
            "canvas": it.get("canvas"),
            "生成尺寸": it.get("gen_size"),
            "下载后存到": it["deliver_to"],
            "状态": "待生成",
        })
        print(f"  提示词  {pid}.txt   生成尺寸 {it.get('gen_size')}")

    # ---------- 回填清单 ----------
    (out / "回填清单.yaml").write_text(yaml.safe_dump({
        "schema_version": "1.0",
        "slug": cfg.get("slug", ""),
        "generated_on": str(date.today()),
        "说明": "每张图在网页端生成后下载，按「下载后存到」的路径与文件名放好，把状态改成 已回填。",
        "items": backfill,
    }, allow_unicode=True, sort_keys=False), encoding="utf-8")

    # ---------- START.md ----------
    ref_tbl = "\n".join(
        f"| {i} | `refs/{name}` | {note} |" for i, name, note in ref_rows)
    item_tbl = "\n".join(
        f"| `{b['id']}` | `prompts/{b['id']}.txt` | **{b['生成尺寸']}** | `{b['下载后存到']}` |"
        for b in backfill)

    start = f"""# 历史叠字模式操作卡 · {cfg.get('slug', '')}

仅适用于已明确选择后期叠字的旧配置；R03 默认图文一次生成，并在同一个网页对话出整套。

生成日期：{date.today()}

---

## 一、每次新开对话都要先传这些参考图（顺序不能乱）

| 上传顺序 | 文件 | 作用 |
|---:|---|---|
{ref_tbl}

⛔ **第 1 张是比例基准图，管的是产品之间的相对大小，不传比例必错。**
⛔ 每张图**单独开一个新对话**再传，不要在同一个对话里连着出好几张 ——
   上一张的结果会带偏下一张。

---

## 二、逐张生成

| 图位 | 提示词 | **生成尺寸** | 下载后存到 |
|---|---|---|---|
{item_tbl}

⛔ **按「生成尺寸」出大图，不要直接出交付尺寸。**
   实测直接生成 600×450 会糊；大图降采样反而更锐。
   交付尺寸由本地渲染器导出，不用你操心。

⛔ 生成的是**无字底图**。一个字都不要让它写 —— 文字由本地渲染器统一叠，
   这样五张 A+ 的字体字号颜色才会完全一致。

---

## 三、下载回来之后（本地跑，不用再进网页）

```powershell
# ① 看落字区够不够干净，决定用哪个积木
python <home>/scripts/扫描底图落字区.py <底图路径>

# ② 叠字 + 查尺寸/安全区/对比度/缩略图可读性
python <home>/scripts/叠字渲染器.py S3_视觉板块/amazon-listing-image-designer/overlay_tasks.yaml --item <图位>

# ③ 产品主色有没有漂（跨图一致性）
python <home>/scripts/产品色彩审计.py S3_视觉板块/amazon-listing-image-designer/overlay_tasks.yaml
```

**人工再看两条（脚本查不了）：**
1. 与 `比例基准图` 并排，产品高度关系对不对
2. 放大看产品底缘，有没有落实（不是硬边贴上去）

---

## 四、⛔ 产品被画错时不要靠"再传一次正确图片"

实测无效。原因是提示词里既说"参考图是唯一权威"、又用文字描述了一遍产品外观，
**两者冲突时模型偏向文字**。

正确做法：把提示词里对产品外观的文字描述**删干净**，只留身份对应
（Item A = reference image 2），让照片当唯一来源。
细则见 `skills/amazon-listing-image-designer/references/多产品同框出图约束.md`。
"""
    (out / "START.md").write_text(start, encoding="utf-8")

    print(f"\n生产包 → {out}")
    print(f"  参考图 {len(ref_rows)} 张 · 提示词 {len(backfill)} 条")
    print("  先看 START.md")


if __name__ == "__main__":
    main()
