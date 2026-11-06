# 美国 FBA 邮编 → 分区 速查表

> 本文件用于在拿到目的仓邮编时快速判定它属于哪个大区（美西 / 美中 / 美东 / 美西北），以及典型仓库代码的归属。下次类似任务里用本表直接对照，避免每次都打开 xlsx 全表搜索。

---

## 1. 邮编分区定义（货代报价通用约定）

| 分区 | 邮编首位 | 邮编段 | 覆盖州（典型） |
|---|---|---|---|
| **美西（美西一/二/三/四/五区）** | 8-9 | 80000-99999（除 97-99） | CA, AZ, NV, UT, OR, WA, ID |
| **美中** | 4-7 | 40000-79999 | TX, OK, KS, MO, AR, LA, IL, IN, MI, OH, KY, TN, AL, MS, MN, WI, IA, NE, ND, SD, CO, NM |
| **美东** | 0-3 | 00000-39999 | NY, NJ, PA, MA, CT, NH, VT, ME, RI, DE, MD, VA, WV, NC, SC, GA, FL |
| **美西北** | 97-99 | 97000-99999（部分） | OR, WA 远郊 + AK + HI（注：航司常作为单独偏远区域报价） |

> 货代报价表的"美西邮编 8-9"通常**不包含 97-99**，后者会单独列出"美西北邮编 97-99"。注意区分。

---

## 2. 中南部（Texas 区域）典型仓库代码

IUSF / FTW1 / DAL2 / DAL3 / FTW2 / FTW6 / FTW7 / FTW9 / DFW6 / DFW7 / DFW1 / XUSB / ITX1 / ITX2 / HOU2 / HOU3 / SAT2 / SAT6

这些都属于**美中 邮编 4-7 分区**（邮编首位 7）。

---

## 3. 常见 Dallas / IUSF 周边仓库（2026 已知）

| FBA 代码 | 邮编 | 城市 | 州 |
|---|---|---|---|
| **IUSF** | 75241 | Dallas | TX |
| FTW1 | 75241 | Fort Worth | TX |
| FTW2 | 75261 | Fort Worth | TX |
| FTW3 | 76177 | Fort Worth | TX |
| FTW6 | 75261 | Fort Worth | TX |
| FTW7 | 76177 | Fort Worth | TX |
| FTW9 | 75019-3989 | Fort Worth | TX |
| DFW6 | 75261 | Dallas-Fort Worth | TX |
| DFW7 | 76177 | Dallas-Fort Worth | TX |
| DFW1 | 75261 | Dallas | TX |
| DAL2 | 75261 | Dallas | TX |
| DAL3 | 75211 | Dallas | TX |
| HOU2 | 77038 | Houston | TX |
| HOU3 | 77423 | Houston | TX |
| SAT2 | 78666 | San Antonio | TX |
| SAT6 | 78666 | San Antonio | TX |
| XUSB | 76155 | Fort Worth | TX |
| ITX1 | 77064 | Houston | TX |
| ITX2 | 77040 | Houston | TX |

> **DAL / DFW / FTW / HOU / SAT 全部邮编首位 7** → 美中分区。

---

## 4. 常见美西 / 美东仓库（用于反例）

### 美西典型
- ONT2/ONT6/ONT8 (CA, 92408/92551)
- LGB3/LGB8 (CA, 91752)
- SBD1/SBD3 (CA, 92407)
- LAX1/LAX2/LAX9 (CA)
- OAK3/OAK4 (CA)
- PHX3/PHX6 (AZ, 85043)
- LAS1/LAS2/LAS7 (NV)
- DEN7 (CO, 80019)
- BFI3/BFI7/SEA6 (WA)

### 美东典型
- TEB3/TEB4/TEB6/TEB9 (NJ, 070xx)
- TTN2 (NJ)
- ABE3/ABE4/ABE8 (PA, 1xxxx)
- ACY2 (NJ)
- AVP1 (PA)
- BNA2 (TN, 37090)
- XCH2 (GA, 31408 = Savannah)
- ATL6/ATL9 (GA)
- TPA9 (FL = 坦帕)
- MIA1 (FL = 迈阿密)

> 美东仓走 **K25 / K21 / D35 / D33 / D36 纽约/坦帕/萨凡纳线**；美中仓（IUSF/DFW/HOU 等）走 **K13/K16/D20/M25 洛杉矶线 + 后端派送**。

---

## 5. 仓库命名规则速记

- **IUS 开头**（IUSP / IUSQ / IUSJ / IUSF）：在 Indiana 的 SHELBYVILLE 区域（邮编 46176/46104/46126/75241），但 461xx 在美中分区
  - 等等：IUSF 邮编是 75241（TX, Dallas），**不是** Indiana 的 IUS 系列。命名相似但地理完全不同，**千万别混淆**
  - IUSP/IUSQ/IUSJ 在 IN, XLX7 也在 IN
- **DAL 开头**：Dallas, TX
- **DFW 开头**：Dallas-Fort Worth, TX
- **FTW 开头**：Fort Worth, TX
- **HOU 开头**：Houston, TX
- **SAT 开头**：San Antonio, TX
- **TEB 开头**：New Jersey（美东）
- **ABE 开头**：Pennsylvania（美东）
- **ORD 开头**：Illinois（美中）

---

## 6. 速查决策树

```
拿到目的仓代码 →
├─ 看邮编首位
│  ├─ 0-3 → 美东 → 优先 K25/D35/K21 纽约线
│  ├─ 4-7 → 美中 → 优先 K13/K16/D20/M25 洛杉矶线（覆盖 DFW/HOU/SAT/IND 等）
│  ├─ 8-9 → 美西 → 优先 K13/K16/D20/M25 洛杉矶线（覆盖 LAX/ONT/SBD/PHX 等）
│  └─ 97-99 → 美西北 → 单询或走"美西北邮编 97.98.99"专项
└─ 看是否被航司/快递标记为偏远
   ├─ 是 → 加 RMB 35/箱
   └─ 否 → 标准报价
```

---

## 7. 常见混淆点

1. **IUSF vs IUSP/IUSQ/IUSJ**：代码相似但**地理完全不同**——IUSF = Dallas, TX；IUSP/IUSQ/IUSJ = Shelbyville, IN
2. **"美西邮编 8-9" vs "美西北邮编 97-99"**：97-99 经常被单独报价且更贵
3. **跨分区混合货件**：如果货件里 5 个 SKU 要发到不同分区，单价要分开算
4. **DAL/DFW/FTW 是同城**：地理上 Dallas-Fort Worth 是一个 metro area，邮编首位都是 7
5. **"美中邮编 4-7"中"7"实际指 7xxxx**，**不**指邮编里有数字 7