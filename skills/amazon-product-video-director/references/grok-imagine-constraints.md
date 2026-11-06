# Grok Imagine 视频约束速查（Amazon 产品视频专用）

> 本文件供 `amazon-product-video-director` 在 `target_platform: grok_imagine` 时引用。  
> 沉淀自案例：`园艺播种移苗器_1031332473737` → `S3_视觉板块/amazon-product-video-director/video_production_v2/`。

## 能力边界（2026-07）

| 能力 | 说明 |
|:---|:---|
| 静图 | `image_gen` / `image_edit`（或 xAI Images API：`grok-imagine-image-quality`） |
| 图生视频 | `image_to_video`（优先）或 API `grok-imagine-video*` |
| 多参考 | `reference_to_video` **慎用**：产品电商片禁止「一次塞全部故事板图」 |
| 单段时长 | 优先 **5–7s**；工具若仅支持 6s/10s，取最接近并在分镜表标注 |
| 画幅 | Premium A+ 默认 **16:9**；主图竖版 **9:16** |
| 分辨率 | API 常用 720p；有 1080p 则动作段优先 720p 控成本 |

## 绝对禁止（实测变形源）

1. **禁止** 一次性上传 8–14 张分镜图做一条长 `reference_to_video` 整片  
2. **禁止** 动作段不带结构锚点（仅一张「美图」）  
3. **禁止** orbit / 环绕产品 / 快速摇镜 / dutch angle  
4. **禁止** 在 prompt 里堆价格、#1、认证、竞品、包装当赠品  
5. **禁止** 清晰可辨识真人正脸（仅手模/背影/虚化侧脸）

## 分段策略（强制）

| 总时长 | 推荐段数 | 每段 |
|:---|:---|:---|
| 15s | 3 段 | 5s |
| 30–35s | 5–6 段 | 5–6s |
| 40–45s | 7–8 段 | 5–7s |

**原则**：一段一传；高风险动作段单独生成 2–3 条选优；不合格只重做该段。

## 每段素材规则

| 风险 | 上传 | 说明 |
|:---|:---|:---|
| low | 1 张 `01_primary` | 开场/收尾/静物 hero |
| medium | 1 张 primary | 放置、配件、场景 |
| high | primary + **结构锚点** | 按压/扭转/折叠/机械动作；锚点优先 1688 结构真图或 A+ 结构模块图 |

可选第三条：1688 `main_01` 作 fallback 锚点（仍变形时替换第二张）。

## Grok 英文 Prompt 结构（每段）

```text
Create a {N} second photorealistic image-to-video clip for Amazon {Listing|Premium A+}.

Action: [一个慢动作，一句说清；禁止复合特技]
Product lock (non-negotiable):
- [颜色/手柄/臂数/筒径/关键部件 — 来自真值表，可量化则量化]
- Match reference images on product shape every frame

Camera: almost static, micro push-in only. NO orbit.
Motion: [手动/真实物理] NO powered drill, NO morphing mechanisms.
Style: soft natural daylight, modern American DTC product editorial.
Text overlay (optional, simple): "Press"   # 复杂字幕建议后期加

CRITICAL: product geometry identical to reference in every frame.
NO morphing, NO stretching, NO redesign, NO logos, NO packaging as free gifts.
REJECT if arms/count/proportions drift in any frame.
```

### 与即梦的差异

| 项 | Grok Imagine | 即梦 Seedance |
|:---|:---|:---|
| Prompt 语言 | **英文为主** | 中文 + @图片N |
| 引用语法 | 上传图顺序 / Agent 本地路径 | `@图片1 作为首帧` |
| 交付包 | `S3_视觉板块/amazon-product-video-director/video_production_v2/` | jimeng 5 件套 |
| 自动化 | Grok Agent 读盘 / xAI API / 网页 | 网页手传为主 |

## 混合快路径（推荐量产）

1. **低风险段**：本地 `ffmpeg` 静图慢推（Ken Burns）→ 零变形  
2. **高风险段**：仅 1–2 段走 Grok Imagine image-to-video  
3. **拼接**：`concat_final.py` 或剪映  

网页上传次数可从 8 次压到 2 次。

## video_production_v2 目录契约

```
S3_视觉板块/amazon-product-video-director/video_production_v2/
├── START.txt                 # 用户一句话触发
├── GROK_AUTO_RUN.md          # Agent 自动执行入口
├── image_manifest.yaml       # clip 清单 + risk + 路径
├── images/clip_XX/
│   ├── 01_primary.jpg
│   ├── 02_anchor_*.jpg       # high 必有
│   └── 03_anchor_*.jpg       # optional fallback
├── prompts/clip_XX.txt       # 纯英文 Imagine prompt
├── exports/                  # clip_XX_*.mp4
├── make_static_clips.py      # 可选：静图段
├── batch_grok_video_api.py   # 可选：XAI_API_KEY 批量
└── concat_final.py           # 可选：拼接
```

## 生成顺序

固定：**高风险动作段优先**（如 press → twist → 其余），合格后再批量低风险段。

## QA 清单（每段必过）

- [ ] 部件数量与真值表一致（如臂数、旋钮数）  
- [ ] 颜色/材质/比例无漂移  
- [ ] 无电钻化、无弹簧、无额外配件  
- [ ] 手部不遮挡关键结构超过 25%  
- [ ] 字幕若画面内生成：简短可辨；否则后期加  

## API 批量（环境具备时）

```powershell
$env:XAI_API_KEY = "xai-..."
python batch_grok_video_api.py --only 02,03 --concat
# 或全量
python batch_grok_video_api.py --concat
```

Key 来源：https://console.x.ai ；模型名以 xAI 当前 Imagine Video 文档为准。
