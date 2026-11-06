# Amazon AI Image Content Policy Reference 2026 / 亚马逊 AI 出图内容规范参考

资料快照日期：2026-06-10。本包文件即为执行依据，不依赖包外本机路径。

## 中文执行版

生成 Amazon Listing 图片、A+ 图片、AI 模特场景、生活方式图、信息图、对比图或生图 Prompt 时，必须优先遵守本文件。

### 当前判断

- 本地报告显示，在 `2026-05-01 ~ 2026-06-10` 窗口内，Amazon 没有发布面向卖家的“AI 出图禁令”。
- AI 出图可以用，但最终图片必须真实反映实物。
- 如果同一素材会复用到 TikTok Shop、Google Shopping、Walmart、广告或社媒，按最严格平台标准处理。
- 不要在 Amazon 主图上加可见的“AI-generated”标识或水印，除非 Amazon 后续明确要求；AI 使用记录应放在内部留痕中。

### 图片真实性硬规则

每条 Prompt 都必须以产品参考图为事实源：

1. 不得改变产品形状、尺寸、颜色、材质、数量、包装、配件、控制件、接口、缝线、绑带、Logo、标签或可见结构。
2. 不得虚构功能、认证、安全属性、医疗功效、防水、防火、耐用性、兼容性、容量或环保声明。
3. 不得展示未包含配件，让买家误以为会随货附赠。
4. 不得夸大比例、厚度、柔软度、质感、亮度、透明度或性能效果。
5. 除非有证据且类目允许，不得使用 before/after 效果对比图。
6. 图片文案、图标、产品视觉和 Listing 文案必须在颜色、数量、尺寸、材质、配件和使用边界上保持一致。

### AI 模特/数字人规则

- AI 模特可以作为生活方式场景中的普通模特。
- 不得把 AI 模特包装成真实买家、评论者、网红、医生、专家、员工、名人或平台背书人。
- 不得生成“买家秀”“评论图”“自拍评测图”“开箱证明图”“网红推荐图”，除非人物真实、授权真实、证据可留档。
- 不得模仿真实公众人物、名人、网红或可识别的私人个体。
- 美国站如需人物，优先使用不可识别的多族裔美国生活方式模特。
- 身体接触、宠物、婴童、医疗、美妆、安全防护类产品，AI 模特使用要更保守，不能暗示未经证实的效果。

### 主图规则

- 纯白背景：RGB 255,255,255。
- 产品占画面至少 85%。
- 只展示实际售卖产品和随货配件。
- 不得有文字、徽章、水印、边框、道具、生活场景、模特或“看起来像随货附赠”的非售卖配件。
- 必须是写实产品摄影风格，不要插画、3D 概念图、幻想渲染或明显 AI 风格。

### 禁止生成的文字、徽章和声明

不得在图片或 Prompt 中生成：

- Best Seller、#1、top-rated、Amazon's Choice、guaranteed、risk-free、limited-time、discount、coupon、price、free shipping、warranty promises、refund promises。
- FDA、CE、FCC、BPA-free、organic、eco-friendly、non-toxic、hypoallergenic、antimicrobial、antibacterial、medical、therapeutic、child-safe、pet-safe、fireproof、waterproof 等声明，除非合规文件证明真实持有。
- 星级评分、客户评价、媒体 Logo、平台 UI、Amazon UI、奖项、认证标识、竞品 Logo 或第三方商标。

### 每条 Prompt 必加的英文封印句

以下英文段落是给生图模型直接使用的，不翻译进 Prompt：

```text
[AI CONTENT COMPLIANCE] Use the supplied product reference image as the source of truth. Preserve the exact product shape, color, material, scale, included parts, and visible structure. Do not invent features, accessories, claims, certifications, badges, reviews, ratings, endorsements, platform UI, third-party logos, or unrealistic performance effects. Any people shown must be generic lifestyle models, not customers, reviewers, influencers, experts, celebrities, or recognizable real persons. The final image must truthfully represent the product a buyer will receive.
```

主图还必须追加：

```text
Main image compliance: pure white RGB 255,255,255 background; product only; no text, no props, no model, no badge, no watermark, no border; product fills at least 85% of the frame.
```

### AI 出图内部留痕

每张 AI 生成或 AI 修改图片，都必须输出：

```text
AI Image Provenance:
- ASIN / SKU:
- Image slot:
- AI use: generated / edited / background replacement / text rendering
- Tool / model:
- Prompt version:
- Reference image path:
- Generated date:
- Operator:
- Verified against real product: Y/N
- Risk flags:
```

本地报告建议保留 Prompt、模型、生成时间、操作人记录 2 年。

### 生图后人工复核

交付前逐图检查：

- 产品结构是否与参考图一致。
- 颜色、材质、尺寸、数量、配件和可见功能是否与产品资料一致。
- 是否出现 AI 幻觉生成的按钮、绑带、接口、标签、包装、认证或额外配件。
- 是否出现虚假评论、虚假背书、虚假 UGC、平台 UI 或星级评分。
- AI 模特是否只是普通生活方式模特，没有冒充真实身份。
- 图片文字是否拼写正确、可读、合规。

---

## English Prompt Reference

Use this reference whenever generating Amazon Listing images, A+ images, AI model scenes, lifestyle scenes, infographics, comparison images, or image-generation prompts.

## Current Position

- The local report found no Amazon seller-wide ban on AI-generated images during the 2026-05-01 to 2026-06-10 window.
- Treat AI generation as allowed only when the final image truthfully represents the real product.
- Apply the strictest platform logic when the same asset may be reused on TikTok Shop, Google Shopping, Walmart, ads, social media, or EU marketplaces.
- Do not add visible "AI-generated" labels or watermarks to Amazon main images unless Amazon later explicitly requires it. Keep AI disclosure and provenance in internal records instead.

## Non-Negotiable Visual Truth Rules

Every prompt must preserve real product truth:

1. Use product reference photos as ground truth.
2. Do not change the product's shape, dimensions, color, material, quantity, packaging, accessories, controls, ports, seams, straps, logos, labels, or visible structure.
3. Do not invent features, certifications, safety properties, medical effects, waterproofing, durability, compatibility, capacity, or environmental claims.
4. Do not show non-included accessories in a way that implies they are included.
5. Do not exaggerate scale, thickness, softness, texture, brightness, transparency, or performance beyond product evidence.
6. Do not use before/after transformation visuals unless the claim is supported and allowed for the category.
7. Ensure image copy, icons, product visuals, and Listing text agree on color, pack count, dimensions, material, included items, and use limits.

## AI Model Rules

AI models may be used for lifestyle context, but prompts must avoid fake social proof:

- Do not present an AI model as a real customer, reviewer, influencer, doctor, expert, celebrity, employee, or buyer.
- Do not create UGC-style "customer photo", "review photo", selfie, testimonial, unboxing proof, or influencer endorsement scenes unless the person and usage rights are real and documented.
- Do not imitate a real person's likeness, public figure, celebrity, influencer, or recognizable private person.
- Do not use minors unless the product category requires child/family context and all safety, age, and guardian implications are appropriate.
- Prefer generic, non-identifiable, multi-ethnic American lifestyle models for US listings when people are needed.
- For body-contact, pet, baby, medical, beauty, safety, or protective products, keep model usage conservative and evidence-based.

## Main Image Rules

For Amazon main images:

- Pure white background: RGB 255,255,255.
- Product should fill at least 85% of the frame.
- Product only: no text, badge, watermark, border, props, lifestyle scene, model, or included-looking non-included accessory.
- Photorealistic product photography, not illustration, 3D concept art, fantasy rendering, or stylized mockup.
- The main image must show the actual sold product and included components only.

## Text, Badges, and Claims

Do not generate or ask the model to render:

- Best Seller, #1, top-rated, Amazon's Choice, guaranteed, risk-free, limited-time, discount, coupon, price, free shipping, warranty promises, or refund promises.
- FDA, CE, FCC, BPA-free, organic, eco-friendly, non-toxic, hypoallergenic, antimicrobial, antibacterial, medical, therapeutic, child-safe, pet-safe, fireproof, waterproof, or similar claims unless documented in the product compliance file.
- Review stars, customer quotes, press logos, platform UI, Amazon UI, badges, awards, certification seals, competitor logos, or third-party trademarks.
- Fine legal copy that cannot be verified after generation.

## Prompt Clause To Add

Add this clause, or a category-specific variation, to every AI image prompt:

```text
[AI CONTENT COMPLIANCE] Use the supplied product reference image as the source of truth. Preserve the exact product shape, color, material, scale, included parts, and visible structure. Do not invent features, accessories, claims, certifications, badges, reviews, ratings, endorsements, platform UI, third-party logos, or unrealistic performance effects. Any people shown must be generic lifestyle models, not customers, reviewers, influencers, experts, celebrities, or recognizable real persons. The final image must truthfully represent the product a buyer will receive.
```

For main images, add:

```text
Main image compliance: pure white RGB 255,255,255 background; product only; no text, no props, no model, no badge, no watermark, no border; product fills at least 85% of the frame.
```

## AI Provenance Log

For every AI-generated or AI-edited image, output an internal log block:

```text
AI Image Provenance:
- ASIN / SKU:
- Image slot:
- AI use: generated / edited / background replacement / text rendering
- Tool / model:
- Prompt version:
- Reference image path:
- Generated date:
- Operator:
- Verified against real product: Y/N
- Risk flags:
```

Keep this provenance record with the image brief. The local report recommends retaining prompt/model/date/operator records for 2 years.

## Post-Generation Review

After image generation, inspect each image before delivery:

- Product structure matches the reference photo.
- Color, material, size, pack count, accessories, and visible features match product facts.
- No hallucinated buttons, straps, holes, labels, ports, handles, seams, packaging, certifications, or extra items.
- No fake review/testimonial/endorsement signals.
- No misleading model usage.
- Any text is legible, spelled correctly, and compliant.
- For live listings, monitor CTR, CVR, returns, and review terms such as "not as pictured", "looks different", "missing accessory", or "smaller than shown" after launch.
