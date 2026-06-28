# bidding-ecp-data

国网新一代电子商务平台 (ECP2.0) 物资采购数据采集与分析系统。无需登录即可获取国网冀北电力有限公司（含全部子公司）历年物资招标采购的货物清单明细数据。

## 工程结构

```
bidding-ecp-data/
├── reports/
│   └── verification_report.md        # 验证报告
├── data/
│   ├── ecp_data.db                   # SQLite 数据库 (97公告 + 4万+物资 + 1500+单位)
│   ├── excels/                       # 货物清单 Excel 存档
│   └── unprocessed_notices.xlsx      # 未处理公告清单 (自动生成)
├── outputs/
│   ├── figures/
│   │   └── material_demand_top5_monthly.png  # Top5 物资需求趋势图
│   └── material_demand_stats.csv     # 物资需求统计 CSV
└── src/
    ├── crawler/
    │   └── ecp_client.py             # ECP API 客户端
    ├── db/
    │   └── schema.py                 # 数据库表结构
    ├── pipeline.py                   # 主流水线 (5阶段)
    └── demand_stats.py               # 需求统计 + 绘图
```

## 业务功能

本系统围绕国网冀北电力有限公司的物资采购数据，提供**采集 → 解析 → 存储 → 统计 → 可视化**全链路能力。

### 数据覆盖

| 维度 | 范围 |
|------|------|
| 时间跨度 | 2020年5月 ~ 2026年6月 (6年+) |
| 公告总数 | 492 条 (招标公告 + 采购公告) |
| 物资正刊 | 97 条 |
| 物资明细 | 40,773 条 |
| 设备种类 | 2,000+ 种 |
| 项目单位 | 1,514 个 (覆盖唐山/承德/张家口/秦皇岛/廊坊/北京) |

### 核心数据字段

| 字段 | 说明 | 示例 |
|------|------|------|
| `material_name` | 物资名称 | `交流避雷器,AC10kV,复合,无间隙` |
| `material_desc` | 物资描述/规格 | `YH5WS-17/50` |
| `demand_quantity` | 需求数量 | `329` |
| `unit` | 计量单位 | `台` / `套` / `千米` / `吨` |
| `package_no` | 包号 | `包1` |
| `sub_bid_name` | 分标名称 | `变压器` / `组合电器` / `避雷器` |
| `project_org_name` | 项目单位 | `国网冀北电力有限公司唐山供电公司` |
| `delivery_place` | 交货地点 | `河北省唐山市` |

### 三类公告数据源

```mermaid
%%{init: {'theme': 'base', 'themeVariables': { 'primaryColor': '#009688', 'primaryBorderColor': '#006f6b', 'lineColor': '#4db6ac', 'secondaryColor': '#e0f2f1', 'tertiaryColor': '#f5f5f5'}}}%%
graph TB
    A[ECP2.0 平台] --> B[招标公告<br/>2018032700291334<br/>401条]
    A --> C[中标公告<br/>2018060501171111<br/>392条]
    A --> D[采购公告<br/>2018032900295987<br/>92条]

    B --> B1[物资公开招标<br/>73条正刊]
    B --> B2[变更公告<br/>跳过]

    C --> C1[中标结果<br/>doci-win]
    C --> C2[中标候选人<br/>doci-win]

    D --> D1[竞争性谈判<br/>物资类24条]
    D --> D2[零星物资框架<br/>正刊+流标]
    D --> D3[服务/工程类<br/>不采集]

    B1 --> E[(bid_notices)]
    D1 --> E
    D2 --> E

    E --> F[货物清单下载]
    F --> G[(bid_items<br/>40,773条)]

    style A fill:#009688,color:#fff,stroke:#00695C
    style E fill:#1976D2,color:#fff,stroke:#0D47A1
    style G fill:#388E3C,color:#fff,stroke:#1B5E20
```

## 安装与运行

```bash
pip install requests openpyxl matplotlib

# 增量模式 (仅处理新增/未解析的公告)
python src/pipeline.py

# 全量模式 (重新下载全部ZIP)
python src/pipeline.py --full

# 仅复核检查
python src/pipeline.py --verify

# 独立运行需求统计 + 绘图
python src/demand_stats.py
```

## 代码流程图

### 主流水线 (5阶段)

```mermaid
graph TB
    START([开始]) --> P1

    P1["<b>Phase 1 列表采集</b><br/>POST noteList API<br/>分页获取 401 条公告"] --> P2
    P2["<b>Phase 2 分类</b><br/>关键词匹配标题<br/>物资 108 条 | 服务 288 条"] --> P3
    P3["<b>Phase 3 下载</b><br/>GET downLoadBid?noticeId=N<br/>ZIP 保存到 data/excels"] --> P3X{ZIP 有效?}
    P3X -->|是| P4
    P3X -->|否| P5

    P4["<b>Phase 4 解析</b><br/>纯内存解压 ZIP<br/>struct 绕过文件名乱码"] --> P4A{嵌套 ZIP?}
    P4A -->|是| P4B[递归解压最多 3 层]
    P4A -->|否| P4C[openpyxl 读取 XLSX]
    P4B --> P4C
    P4C --> P4D{表头签名匹配?}
    P4D -->|是| P4E[detect_column_map<br/>自动检测列映射]
    P4D -->|否| P4F[跳过该 Sheet]
    P4E --> P4G["逐行解析<br/>→ bid_items<br/>→ org_units"]
    P4G --> P5

    P5["<b>Phase 5 复核</b><br/>状态分布 | 遗漏检查 | 去重验证"] --> O1

    O1["<b>产物更新</b><br/>demand_stats 汇总表"] --> O2
    O2["unprocessed_notices.xlsx<br/>未处理公告清单"] --> O3
    O3["Top5 物资时间序列图<br/>5 子图上下排列"] --> O4
    O4["material_demand_stats.csv<br/>完整统计导出"] --> END([完成])

    style START fill:#009688,color:#fff,stroke:#00695C
    style P1 fill:#1565C0,color:#fff,stroke:#0D47A1
    style P2 fill:#1565C0,color:#fff,stroke:#0D47A1
    style P3 fill:#1565C0,color:#fff,stroke:#0D47A1
    style P4 fill:#E65100,color:#fff,stroke:#BF360C
    style P4B fill:#EF6C00,color:#fff,stroke:#E65100
    style P4E fill:#EF6C00,color:#fff,stroke:#E65100
    style P4G fill:#7B1FA2,color:#fff,stroke:#4A148C
    style P5 fill:#2E7D32,color:#fff,stroke:#1B5E20
    style O1 fill:#00695C,color:#fff,stroke:#004D40
    style O2 fill:#00695C,color:#fff,stroke:#004D40
    style O3 fill:#C62828,color:#fff,stroke:#B71C1C
    style O4 fill:#00695C,color:#fff,stroke:#004D40
    style END fill:#009688,color:#fff,stroke:#00695C
```

### Excel 列映射自动检测

```mermaid
%%{init: {'theme': 'base', 'themeVariables': { 'primaryColor': '#E65100', 'lineColor': '#FF9800'}}}%%
graph LR
    A[读取Sheet首行] --> B{表头含<br/>物资名称?}
    B -->|是 格式1| C[招标公告货物清单<br/>23列/27列]
    B -->|否| D{表头含<br/>物料描述?}
    D -->|是 格式2| E[零星物资格式<br/>8-10列]
    D -->|否| F[跳过]

    C --> C1[物资名称→col 7<br/>物资描述→col 8<br/>单位→col 9<br/>数量→col 10<br/>分包编号→col 2]

    E --> E1[物料描述→material_name<br/>分标名称→sub_bid_name<br/>分包名称→package_no<br/>数量→col 8<br/>计量单位→col 9]

    C1 --> G{核心字段<br/>齐全?}
    E1 --> G
    G -->|是| H[逐行解析入库]
    G -->|否| F

    style C fill:#388E3C,color:#fff,stroke:#1B5E20
    style E fill:#1976D2,color:#fff,stroke:#0D47A1
    style H fill:#009688,color:#fff,stroke:#00695C
    style F fill:#D32F2F,color:#fff,stroke:#B71C1C
```

## 数据采集时序图

```mermaid
%%{init: {'theme': 'base', 'themeVariables': { 'primaryColor': '#009688'}}}%%
sequenceDiagram
    autonumber
    participant P as Pipeline
    participant API as ECP2.0 API
    participant FS as 文件系统
    participant ZIP as ZIP处理器
    participant XL as Excel解析器
    participant DB as SQLite

    rect rgb(0, 150, 136, 0.1)
        Note over P,API: Phase 1+2: 列表采集+分类
        P->>API: POST noteList<br/>(orgId=2019061900137008)
        API-->>P: 401条公告JSON
        P->>P: 关键词分类<br/>(物资/服务/工程)
        P->>DB: INSERT bid_notices
    end

    rect rgb(33, 150, 243, 0.1)
        Note over P,FS: Phase 3: 下载
        P->>DB: SELECT 物资正刊<br/>(category=material, doctype=doci-bid)
        DB-->>P: 97条
        loop 每条公告
            P->>API: GET downLoadBid<br/>?noticeId=N
            API-->>P: ZIP (198KB~42MB)
            P->>FS: 保存 {code}.zip
        end
    end

    rect rgb(255, 152, 0, 0.1)
        Note over P,XL: Phase 4: 解析 (纯内存)
        P->>ZIP: 读取ZIP header偏移
        ZIP-->>P: 原始字节
        P->>ZIP: zlib.decompress(-MAX_WBITS)
        ZIP-->>P: 解压后数据

        alt 发现嵌套ZIP
            P->>ZIP: 递归解压 (最多3层)
            ZIP-->>P: 内层XLSX数据
        end

        P->>XL: openpyxl (BytesIO)
        XL-->>P: Sheet列表 + 表头
        P->>P: detect_column_map<br/>自动检测列映射
        P->>XL: 逐行读取
        XL-->>P: 物资明细列表
        P->>DB: INSERT bid_items (去重)
        P->>DB: INSERT org_units
        P->>DB: UPDATE excel_status=parsed
    end

    rect rgb(76, 175, 80, 0.1)
        Note over P,DB: Phase 5: 复核 + 产物
        P->>DB: 状态分布统计
        P->>DB: 遗漏/重复检查
        P->>P: demand_stats 汇总
        P->>FS: 图表 PNG + CSV + Excel
    end
```

## 数据表结构

```mermaid
%%{init: {'theme': 'base', 'themeVariables': { 'primaryColor': '#1565C0', 'lineColor': '#42A5F5'}}}%%
erDiagram
    bid_notices ||--o{ bid_items : "notice_id"
    bid_items }o--|| org_units : "project_org_name"
    bid_items ||--o{ material_demand_stats : "按月汇总"

    bid_notices {
        TEXT notice_id PK "ECP公告ID"
        TEXT title "公告标题"
        TEXT code "项目编号"
        TEXT publish_org_name "发布单位"
        TEXT notice_publish_time "发布时间"
        TEXT category "material/service/engineering"
        TEXT bid_batch "第X次"
        INTEGER bid_year "年份"
        TEXT excel_status "pending/downloaded/parsed/no_file/parse_failed"
        TEXT excel_path "Excel存档路径"
    }

    bid_items {
        INTEGER id PK
        TEXT notice_id FK "关联公告"
        TEXT material_name "物资名称"
        TEXT material_desc "物资描述/规格"
        REAL demand_quantity "需求数量"
        TEXT unit "计量单位"
        TEXT package_no "包号"
        TEXT sub_bid_name "分标名称"
        TEXT sub_bid_code "分标编号"
        TEXT project_org_name "项目单位(子公司)"
        TEXT delivery_place "交货地点"
        TEXT material_code "国网物料编码"
        TEXT source_file "来源Excel文件名"
    }

    org_units {
        INTEGER id PK
        TEXT org_name "单位名称"
        TEXT city "城市"
        TEXT province "省份"
        TEXT parent_org_name "上级单位"
    }

    material_demand_stats {
        INTEGER id PK
        TEXT material_name "物资名称"
        TEXT unit "计量单位"
        TEXT demand_month "月份YYYYMM"
        REAL demand_quantity "需求量合计"
        INTEGER notice_count "涉及公告数"
    }
```

## excel_status 状态流转

```mermaid
%%{init: {'theme': 'base', 'themeVariables': { 'primaryColor': '#009688'}}}%%
stateDiagram-v2
    [*] --> pending: 公告入库

    pending --> downloaded: Phase 3<br/>ZIP下载成功
    pending --> no_file: Phase 3<br/>ZIP为空

    downloaded --> parsed: Phase 4<br/>XLSX解析成功
    downloaded --> parse_failed: Phase 4<br/>解析0条

    parsed --> [*]: 完成
    no_file --> [*]: 放弃(无货物清单)
    parse_failed --> [*]: 放弃(需人工)

    note right of parsed: 97/97 (100%)
    note right of no_file: 0条
    note right of parse_failed: 0条
```

## 关键API接口

| 接口 | 方法 | 认证 | 用途 |
|------|------|------|------|
| `/ecpwcmcore//index/noteList` | POST | 无需 | 公告列表 (3个菜单ID) |
| `/ecpwcmcore//index/downLoadBid?noticeId=` | GET | 无需 | 下载货物清单ZIP |
| `/ecpwcmcore//index/getNoticeBid` | POST | 需登录 | 公告详情 (未使用) |

### 菜单ID

| 菜单 | ID | 冀北公告数 |
|------|-----|----------|
| 招标公告 | `2018032700291334` | 401 |
| 中标公告 | `2018060501171111` | 392 |
| 采购公告 | `2018032900295987` | 92 |

## 技术要点

### 纯内存ZIP解压

ECP货物清单ZIP的文件名使用GBK编码，Python `zipfile` 模块无法正确处理（`BadZipFile`异常）。解决方案：

1. 使用 `struct` 直接读取ZIP header偏移量，绕过文件名编码校验
2. `zlib.decompress(data, -zlib.MAX_WBITS)` 处理Deflate压缩
3. 递归处理嵌套ZIP（采购公告类型ZIP内含另一个ZIP，最多3层）

### 两种Excel格式兼容

| 特性 | 招标公告格式 | 零星物资格式 |
|------|------------|------------|
| 表头签名 | `物资名称` | `物料描述` |
| 列数 | 23/27列 | 8-10列 |
| 单位列名 | `单位` | `计量单位` |
| 分标来源 | Sheet名 | 分标名列 |
| 数量列 | col 10 | col 8 |
| 示例 | 公开招标/协议库存 | 竞争性谈判/零星框架 |

### 去重机制

```sql
CREATE UNIQUE INDEX idx_items_unique ON bid_items(
    notice_id, COALESCE(sub_bid_code,''), COALESCE(sub_bid_name,''),
    COALESCE(package_no,''), COALESCE(material_name,'')
);
```

同一公告+分标+包号+物资名 → 自动跳过重复插入。

## 数据库状态

| 表 | 记录数 | 说明 |
|----|--------|------|
| `bid_notices` | 492 | 冀北全部公告 |
| `bid_items` | 40,773 | 物资明细 |
| `org_units` | 1,514 | 项目单位 (含县区分公司) |
| `material_demand_stats` | 5,839 | 物资需求按月汇总 |

### 解析状态

| 状态 | 数量 | 说明 |
|------|------|------|
| `parsed` | 97 | 物资正刊全部解析成功 |
| `pending` | 395 | 服务/变更/特殊文档 (无物资数据) |
