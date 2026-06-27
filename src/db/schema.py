"""
SQLite 数据库表结构定义

表设计原则：
- bid_notices: 公告主表 — 存储从 noteList API 获取的元数据
- bid_items: 物资明细表 — 存储从详情页/货物清单提取的物料级数据（需登录）
- org_units: 项目单位表 — 存储子公司/供电公司等需求单位
- notice_attachments: 附件表 — 记录货物清单等附件信息
- crawl_log: 爬取日志表 — 记录每次数据采集的状态
"""
import sqlite3
import os

DB_PATH = os.path.join(os.path.dirname(__file__), "..", "..", "data", "ecp_data.db")

SCHEMA_SQL = """
-- ============================================================
-- 1. 公告主表
-- 数据来源: noteList API (无需登录)
-- ============================================================
CREATE TABLE IF NOT EXISTS bid_notices (
    id              INTEGER PRIMARY KEY,
    notice_id       TEXT NOT NULL UNIQUE,        -- ECP公告ID (如 2606268991023276)
    title           TEXT NOT NULL,               -- 公告标题
    code            TEXT,                         -- 项目编号 (如 JB26-HW-ZB04-0126W4)
    publish_org_name TEXT,                        -- 发布单位名称 (如 国网冀北电力有限公司)
    org_id          TEXT,                         -- 发布单位orgId
    notice_publish_time TEXT,                     -- 发布时间 (YYYY-MM-DD)
    notice_type     INTEGER,                      -- 公告类型编码 (100063001=公开招标)
    notice_type_name TEXT,                        -- 公告类型名称
    doctype         TEXT,                         -- 文档类型 (doci-bid/doci-change/doc-spec)
    doc_id          TEXT,                         -- 详情文档ID (firstPageDocId)
    doc_url         TEXT,                         -- 详情页URL
    zbflag          INTEGER DEFAULT 0,            -- 招标标志
    category        TEXT,                         -- 分类: material/service/engineering/other
    bid_batch       TEXT,                         -- 招标批次 (如 "第四次", "第五批")
    bid_year        INTEGER,                      -- 招标年份

    -- 元数据
    fetched_at      TEXT DEFAULT (datetime('now')), -- 列表数据获取时间
    detail_fetched  INTEGER DEFAULT 0,              -- 详情是否已爬取
    detail_fetched_at TEXT,                          -- 详情爬取时间
    excel_path      TEXT,                            -- 保存的Excel文件路径

    created_at      TEXT DEFAULT (datetime('now')),
    updated_at      TEXT DEFAULT (datetime('now'))
);

CREATE INDEX IF NOT EXISTS idx_notices_org_id ON bid_notices(org_id);
CREATE INDEX IF NOT EXISTS idx_notices_publish_time ON bid_notices(notice_publish_time);
CREATE INDEX IF NOT EXISTS idx_notices_category ON bid_notices(category);
CREATE INDEX IF NOT EXISTS idx_notices_bid_year ON bid_notices(bid_year);

-- ============================================================
-- 2. 物资明细表
-- 数据来源: 详情页HTML解析 / 货物清单Excel附件 (需登录)
-- 每条记录对应货物清单中的一行物资
-- ============================================================
CREATE TABLE IF NOT EXISTS bid_items (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    notice_id       TEXT NOT NULL,                -- 关联公告ID
    bid_notice_id   INTEGER,                      -- 关联 bid_notices.id

    -- 物资核心字段 (用户最关心的5个字段)
    material_name   TEXT,                         -- 物资名称 (如 "10kV变压器")
    material_desc   TEXT,                         -- 物资描述/规格 (如 "SCB14-800/10 硅钢片")
    demand_quantity REAL,                         -- 需求数量
    unit            TEXT,                         -- 计量单位 (台/套/公里/吨/个/只/面)

    -- 辅助字段
    package_no      TEXT,                         -- 包号 (如 "包1")
    sub_bid_name    TEXT,                         -- 分标名称 (如 "变压器"、"组合电器")
    sub_bid_code    TEXT,                         -- 分标编号 (如 SG2511-1404-11002)
    material_code   TEXT,                         -- 国网物料编码 (MDM编码)
    tech_spec_id    TEXT,                         -- 技术规范书ID
    unit_price_limit REAL,                        -- 限价(含税单价/万元)
    total_price_limit REAL,                       -- 总限价(万元)

    -- 扩展字段
    delivery_place  TEXT,                         -- 交货地点
    delivery_period TEXT,                         -- 交货期
    remark          TEXT,                         -- 备注
    extended_desc   TEXT,                         -- 扩展描述 (来自Excel扩展描述列)

    -- 采购单位 (子公司维度)
    project_org_id  INTEGER,                      -- 关联 org_units.id
    project_org_name TEXT,                        -- 项目单位名称 (如 国网冀北电力有限公司唐山供电公司)

    source          TEXT DEFAULT 'detail_page',   -- 数据来源: detail_page/attachment/manual
    source_file     TEXT,                         -- 来源文件名(如为附件)

    created_at      TEXT DEFAULT (datetime('now'))
);

CREATE INDEX IF NOT EXISTS idx_items_notice_id ON bid_items(notice_id);
CREATE INDEX IF NOT EXISTS idx_items_material_name ON bid_items(material_name);
CREATE INDEX IF NOT EXISTS idx_items_sub_bid ON bid_items(sub_bid_name);
CREATE INDEX IF NOT EXISTS idx_items_project_org ON bid_items(project_org_name);
-- 去重约束: 同一公告+分标+包号+物资名 唯一
CREATE UNIQUE INDEX IF NOT EXISTS idx_items_unique ON bid_items(
    notice_id, COALESCE(sub_bid_code,''), COALESCE(sub_bid_name,''),
    COALESCE(package_no,''), COALESCE(material_name,'')
);

-- ============================================================
-- 3. 项目单位表 (子公司维度)
-- 数据来源: 详情页"项目单位"字段
-- 国网冀北电力有限公司的下属单位如:
--   国网冀北电力有限公司唐山供电公司
--   国网冀北电力有限公司承德供电公司
--   国网冀北电力有限公司张家口供电公司
--   国网冀北电力有限公司秦皇岛供电公司
--   国网冀北电力有限公司廊坊供电公司
--   国网冀北电力有限公司物资分公司
--   北京送变电有限公司
-- ============================================================
CREATE TABLE IF NOT EXISTS org_units (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    parent_org_id   TEXT,                         -- 上级单位orgId (如 2019061900137008)
    parent_org_name TEXT,                         -- 上级单位名称 (如 国网冀北电力有限公司)
    org_name        TEXT NOT NULL,                -- 单位名称
    org_level       TEXT,                         -- 级别: province/city/county
    city            TEXT,                         -- 所在城市
    province        TEXT,                         -- 所在省份

    -- 首次出现
    first_seen_notice_id TEXT,                     -- 首次出现的公告ID
    first_seen_date TEXT,                          -- 首次出现的日期

    created_at      TEXT DEFAULT (datetime('now'))
);

CREATE INDEX IF NOT EXISTS idx_org_units_parent ON org_units(parent_org_id);
CREATE INDEX IF NOT EXISTS idx_org_units_name ON org_units(org_name);

-- ============================================================
-- 4. 附件表
-- 数据来源: 详情页附件下载链接
-- ============================================================
CREATE TABLE IF NOT EXISTS notice_attachments (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    notice_id       TEXT NOT NULL,                -- 关联公告ID
    bid_notice_id   INTEGER,                      -- 关联 bid_notices.id
    file_name       TEXT,                         -- 文件名
    file_url        TEXT,                         -- 下载URL
    file_type       TEXT,                         -- 文件类型: xlsx/pdf/zip/doc
    file_size       INTEGER,                      -- 文件大小(bytes)
    category        TEXT,                         -- 附件类别: goods_list/tech_spec/notice_doc/other
    downloaded      INTEGER DEFAULT 0,            -- 是否已下载
    download_path   TEXT,                         -- 本地下载路径
    parsed          INTEGER DEFAULT 0,            -- 是否已解析
    created_at      TEXT DEFAULT (datetime('now'))
);

CREATE INDEX IF NOT EXISTS idx_attach_notice_id ON notice_attachments(notice_id);
CREATE INDEX IF NOT EXISTS idx_attach_category ON notice_attachments(category);

-- ============================================================
-- 5. 爬取日志表
-- ============================================================
CREATE TABLE IF NOT EXISTS crawl_log (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    task_name       TEXT NOT NULL,                -- 任务名称
    task_type       TEXT,                         -- list/detail/attachment
    status          TEXT DEFAULT 'running',        -- running/completed/failed
    records_count   INTEGER,                      -- 处理记录数
    error_message   TEXT,                         -- 错误信息
    started_at      TEXT DEFAULT (datetime('now')),
    completed_at    TEXT
);

-- ============================================================
-- 6. 登录会话表 (存储Cookie/Token)
-- ============================================================
CREATE TABLE IF NOT EXISTS auth_session (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    cookie_jar      TEXT,                         -- JSON序列化的Cookie
    token           TEXT,                         -- JWT或其他Token
    expires_at      TEXT,                         -- 过期时间
    created_at      TEXT DEFAULT (datetime('now'))
);

-- ============================================================
-- 视图：冀北物资公告汇总
-- ============================================================
CREATE VIEW IF NOT EXISTS v_jibei_material_summary AS
SELECT
    n.bid_year,
    n.bid_batch,
    n.title,
    n.notice_publish_time,
    COUNT(i.id) AS item_count,
    GROUP_CONCAT(DISTINCT i.sub_bid_name) AS sub_bids,
    GROUP_CONCAT(DISTINCT i.project_org_name) AS project_orgs
FROM bid_notices n
LEFT JOIN bid_items i ON n.notice_id = i.notice_id
WHERE n.org_id = '2019061900137008'
  AND n.category = 'material'
GROUP BY n.notice_id
ORDER BY n.notice_publish_time DESC;

-- ============================================================
-- 视图：子公司招标频次
-- ============================================================
CREATE VIEW IF NOT EXISTS v_jibei_subsidiary_freq AS
SELECT
    o.org_name,
    o.city,
    COUNT(DISTINCT i.notice_id) AS notice_count,
    COUNT(i.id) AS item_count,
    MIN(n.notice_publish_time) AS first_seen,
    MAX(n.notice_publish_time) AS last_seen
FROM org_units o
JOIN bid_items i ON o.id = i.project_org_id
JOIN bid_notices n ON i.notice_id = n.notice_id
WHERE o.parent_org_id = '2019061900137008'
GROUP BY o.org_name
ORDER BY notice_count DESC;
"""


def init_db(db_path: str = None) -> sqlite3.Connection:
    """初始化数据库，创建所有表"""
    if db_path is None:
        db_path = DB_PATH

    os.makedirs(os.path.dirname(db_path), exist_ok=True)
    conn = sqlite3.connect(db_path)
    conn.executescript(SCHEMA_SQL)
    conn.commit()
    return conn


def get_connection(db_path: str = None) -> sqlite3.Connection:
    """获取数据库连接"""
    if db_path is None:
        db_path = DB_PATH
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


if __name__ == "__main__":
    conn = init_db()
    print(f"数据库初始化完成: {DB_PATH}")

    # 验证表结构
    cursor = conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name"
    )
    tables = [row[0] for row in cursor]
    print(f"创建的表: {tables}")

    cursor = conn.execute(
        "SELECT name FROM sqlite_master WHERE type='view' ORDER BY name"
    )
    views = [row[0] for row in cursor]
    print(f"创建的视图: {views}")

    conn.close()
