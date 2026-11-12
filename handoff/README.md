# handoff 文件夹契约（v1）

底座（OpenBot、StarSky、Grok Bot 等）与星空广告终端之间的数据交换约定。两版（基础版、旗舰版）共用同一份契约。

本文档只定义格式和规则，不含业务逻辑。对应方案见 [docs/20_旗舰版架构方案.md](../docs/20_旗舰版架构方案.md)。

## 一、位置

默认位于 OpenBot 共享文件夹内：

```
~/OpenBot/Shared/StarSky/handoff/
```

- 选这个位置的原因：OpenBot 助手默认只能读写自己的工作区和共享文件夹（见 [adapters/openbot/SETUP.md](../adapters/openbot/SETUP.md)）。放在共享文件夹里，OpenBot 不用额外授权就能写 `outbox/`；终端在同一台 Mac 上也能读。
- 不在本仓库内，不进 Git。
- 第一阶段（OpenBot、StarSky）与终端在同一台 Mac，直接读写。
- 2026-11-12 实测：OpenBot 运营总监按本文档写入 `outbox/products/sample.json` 与 `manifest.json`，`validate_handoff.py` 通过（0 错误、0 警告）。
- 第二阶段（Grok Bot）若在云电脑上运行，本目录由同步或执行桥接生成，路径另定。

## 二、目录结构

```
handoff/
  manifest.json          底座产出清单（只由底座写）
  outbox/                底座 → 终端（终端只读）
    products/*.json      选品、上架数据
    reports/*.md         运营日报等说明文件
  inbox/                 终端 → 底座
    plans/*.json         待审核方案（终端写，底座只读）
    results/*.json       推送结果回执（终端写，底座只读）
```

谁写谁负责格式。终端不改写 `outbox/`，底座不改写 `inbox/`。

## 三、写入规则

1. **原子写入。** 先写到同目录下的临时文件，文件名以 `.` 开头、以 `.tmp` 结尾，写完、`fsync` 后再 `rename` 成目标名。读方只看到完整文件。
2. **临时文件不算数据。** 校验时忽略 `.tmp` 文件，但会给出警告。
3. **JSON 使用 UTF-8。** 中文不转义（`ensure_ascii=false`）。
4. **不覆盖已确认的文件。** `inbox/plans/` 中的方案一旦被终端确认过，底座不得原地修改，只能生成新文件（新 `plan_id`）。

参考实现：[handoff/atomic_write.py](atomic_write.py)。

## 四、JSON 信封（每个 JSON 文件必须有）

| 字段 | 类型 | 说明 |
|---|---|---|
| `schema_version` | 字符串 | 固定为 `"1"` |
| `source` | 字符串 | `grokbot` / `openbot` / `starsky` / `terminal` |
| `created_at` | 字符串 | ISO 8601，必须带时区，例如 `2026-11-12T10:30:00+08:00` |

`source` 取值的含义：
- 底座写入的文件：`grokbot` / `openbot` / `starsky`。
- 终端写入的文件：`terminal`。

信封之外的业务字段由各自的写入方定义。

## 五、manifest.json

只登记 `outbox/` 下的文件，由底座在写完一批文件后生成。

```json
{
  "schema_version": "1",
  "source": "openbot",
  "edition": "flagship",
  "license_id": "示例-0001",
  "created_at": "2026-11-12T10:30:00+08:00",
  "files": [
    {"path": "outbox/products/sample.json", "sha256": "<64 位十六进制>"}
  ]
}
```

字段：
- `edition`：`base` 或 `flagship`，表示生成这批数据的底座所在版本。
- `license_id`：可选，授权编号。
- `files`：每项的 `path` 必须是相对路径，不能以 `/` 开头，不能含 `..`。`sha256` 为该文件内容的摘要。

`manifest.json` 本身在写入时也使用原子写入。

## 六、校验

```bash
python3 handoff/validate_handoff.py ~/OpenBot/Shared/StarSky/handoff
```

- 退出码 `0`：通过（可能有警告）。
- 退出码 `1`：有错误，列出具体文件和原因。
- 退出码 `2`：参数错误。

校验只用 Python 标准库，不需要额外安装。

校验内容：
- `manifest.json` 存在、字段完整、版本与取值合法。
- `files` 中每个文件存在，且 `sha256` 与内容一致。
- `outbox/` 下的 JSON 文件都已在 manifest 中登记（未登记视为错误）。
- 每个 JSON 满足第四节的信封要求。
- `inbox/`、`outbox/` 下只允许规定的子目录，其他目录给出警告。
- `.tmp` 临时文件给出警告，不计为错误。

## 七、版本与兼容

- `schema_version` 变化时，校验脚本必须能识别旧版本或明确拒绝。
- 基础版未装终端时，底座照常写 `outbox/`，契约不要求终端在线。
- 终端只支持它认识的 `schema_version`，遇到未知版本显示提示，不静默跳过。

## 八、不包含的内容

- 不包含店铺凭据、API 密钥、ERP 账号。这些只在终端的本机密钥库里。
- 不包含推送指令。推送只由老板在终端界面输入「确认推送」后执行。
