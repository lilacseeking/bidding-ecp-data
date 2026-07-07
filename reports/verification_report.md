# 国网冀北电力物资采购数据采集 — 验证报告

> 验证日期：2026-06-28  
> 目标：国网冀北电力有限公司（含全部子公司）  
> 数据来源：国家电网电子商务平台 ECP2.0（**无需登录**）

---

## 1. 执行摘要

| 指标 | 数值 |
|------|------|
| 冀北物资正刊公告 | **73 条** |
| 成功解析 | **46 条**（63%） |
| 物资明细条目 | **34,281 条** |
| 项目单位 | **1,091 个** |
| 时间跨度 | **2020-05 ~ 2026-06（6年）** |
| Excel文件保存 | **46 个**（以项目编号命名） |
| 失败/待处理 | 27 条（17条ZIP无XLSX + 10条下载空） |

---

## 2. 数据获取流程

```
noteList API (POST, 公开)
  → 401条冀北公告 → 筛选物资正刊 73条
       │
       ▼
downLoadBid API (GET, 公开)
  URL: /ecp2.0/ecpwcmcore//index/downLoadBid?noticeId={noticeId}&noticeDetId=
  → 下载 ZIP → data/excels/{项目编号}.zip
       │
       ▼
unzip → 识别货物清单XLSX (表头含"物资名称")
  → data/excels/{项目编号}.xlsx
       │
       ▼
openpyxl 逐Sheet解析 → bid_items 表
  自动检测列映射 + 硬编码修正兜底
       │
       ▼
Phase 5 复核: 去重检查 + 遗漏检查 + 差异报告
```

---

## 3. 字段覆盖

| 目标字段 | 来源 | 可用 | Excel列 |
|----------|------|:---:|---------|
| 物资名称 | 货物清单 | ✅ | H |
| 物资描述/规格 | 货物清单 | ✅ | I |
| 需求数量 | 货物清单 | ✅ | K |
| 计量单位 | 货物清单 | ✅ | J |
| 包号（分包编号） | 货物清单 | ✅ | C |
| 分标名称 | Sheet名 | ✅ | Tab名 |
| 分标编号 | 货物清单 | ✅ | A |
| 项目单位（子公司） | 货物清单 | ✅ | D |
| 需求单位 | 货物清单 | ✅ | E |
| 交货地点 | 货物清单 | ✅ | N |
| 物料编码 | 货物清单 | ✅ | T |
| 扩展描述 | 货物清单 | ✅ | U |
| 电压等级/交货方式/项目名称 | 货物清单 → remark | ✅ | G/O/F |

---

## 4. 复核机制

### 4.1 防重复

```sql
-- bid_items 表唯一索引
CREATE UNIQUE INDEX idx_items_unique ON bid_items(
    notice_id, sub_bid_code, sub_bid_name, package_no, material_name
);
```
INSERT OR IGNORE 自动跳过重复记录。

### 4.2 防遗漏

Phase 5 输出未处理公告清单：
```
SELECT notice_id, title FROM bid_notices
WHERE category='material' AND doctype='doci-bid' AND detail_fetched=0
```

### 4.3 防错误

- 表头自动检测：只有含"物资名称"列的Sheet才被解析为货物清单
- 失败记录持久化：`data/failed_parse.json`
- 硬编码修正入口：`data/hardcoded_fixes.json`（当自动检测不生效时填入）

---

## 5. 未处理公告分析

共27条未处理，分三类：

| 类型 | 数量 | 原因 |
|------|------|------|
| ZIP无XLSX | 17 | 公告只有DOC文档，无货物清单Excel（为"新增/补充/打样/流标/融资租赁"等特殊采购类型） |
| 下载空 | 10 | 较早公告（2020-2021年部分）下载接口返回空内容 |

17条无XLSX的公告包括：2023-2021年的新增/补充/打样/流标/煤改电/融资租赁/调控物资专项等，这些特殊采购类型不包含标准化货物清单Excel。

---

## 6. 技术要点

### 列映射自动检测

不同公告的Excel列数不同（23列/27列），使用表头名称匹配而非硬编码列号：

```python
def detect_column_map(headers):
    col_map = {}
    for i, h in enumerate(headers):
        if h == '物资名称': col_map['material_name'] = i
        elif h == '数量':   col_map['quantity'] = i
        # ...
    return col_map if all(k in col_map for k in ['material_name','unit','quantity']) else None
```

### 项目编号唯一性

```
code唯一 → Excel命名为 {code}.xlsx  (例: JB26-HW-ZB04-0126W4.xlsx)
code重复 → Excel命名为 {code}_{noticeId}.xlsx
```

### 货物清单识别

每个ZIP含多个XLSX，通过检查第一个Sheet表头是否含"物资名称"来区分货物清单和投标保证金一览表。

---

## 7. 工程结构

```
bidding-ecp-data/
├── reports/
│   └── verification_report.md
├── data/
│   ├── ecp_data.db              ← 401公告 + 34,281物资 + 1,091单位
│   ├── excels/                  ← 46个Excel (以项目编号命名)
│   ├── failed_parse.json        ← 失败记录
│   └── hardcoded_fixes.json     ← 硬编码修正 (按需填入)
└── src/
    ├── crawler/ecp_client.py    ← ECP API客户端
    ├── db/schema.py             ← 表结构 (6表+去重索引)
    └── pipeline.py              ← 流水线主程序
```

### 运行

```bash
python src/pipeline.py            # 增量 (只处理未解析的)
python src/pipeline.py --full     # 全量 (重新下载全部)
python src/pipeline.py --verify   # 仅复核
```
