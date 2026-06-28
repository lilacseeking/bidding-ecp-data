"""
SQLite 数据库表结构定义

表：
- bid_notices: 公告主表 (noteList API)
- bid_items: 物资明细表 (货物清单Excel)
- material_demand_item: 物资需求明细表 (按月汇总, 保留单位维度)
- material_demand_total: 物资需求总计表 (按月汇总, 跨单位求和)
"""
import sqlite3
import os

DB_PATH = os.path.join(os.path.dirname(__file__), "..", "..", "data", "ecp_data.db")

CLEANUP_SQL = """
DROP TABLE IF EXISTS auth_session;
DROP TABLE IF EXISTS crawl_log;
DROP TABLE IF EXISTS notice_attachments;
DROP TABLE IF EXISTS org_units;
DROP TABLE IF EXISTS material_demand_stats;
DROP VIEW IF EXISTS v_jibei_material_summary;
DROP VIEW IF EXISTS v_jibei_subsidiary_freq;
"""

SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS bid_notices (
    id              INTEGER PRIMARY KEY,
    notice_id       TEXT NOT NULL UNIQUE,
    title           TEXT NOT NULL,
    code            TEXT,
    publish_org_name TEXT,
    org_id          TEXT,
    notice_publish_time TEXT,
    notice_type     INTEGER,
    notice_type_name TEXT,
    doctype         TEXT,
    doc_id          TEXT,
    doc_url         TEXT,
    zbflag          INTEGER DEFAULT 0,
    category        TEXT,
    bid_batch       TEXT,
    bid_year        INTEGER,
    fetched_at      TEXT DEFAULT (datetime('now')),
    detail_fetched  INTEGER DEFAULT 0,
    detail_fetched_at TEXT,
    excel_path      TEXT,
    source_file     TEXT,
    excel_status    TEXT DEFAULT 'pending',
    created_at      TEXT DEFAULT (datetime('now')),
    updated_at      TEXT DEFAULT (datetime('now'))
);
CREATE INDEX IF NOT EXISTS idx_notices_org_id ON bid_notices(org_id);
CREATE INDEX IF NOT EXISTS idx_notices_publish_time ON bid_notices(notice_publish_time);
CREATE INDEX IF NOT EXISTS idx_notices_category ON bid_notices(category);
CREATE INDEX IF NOT EXISTS idx_notices_bid_year ON bid_notices(bid_year);

CREATE TABLE IF NOT EXISTS bid_items (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    notice_id       TEXT NOT NULL,
    bid_notice_id   INTEGER,
    material_name   TEXT,
    material_desc   TEXT,
    demand_quantity REAL,
    unit            TEXT,
    package_no      TEXT,
    sub_bid_name    TEXT,
    sub_bid_code    TEXT,
    material_code   TEXT,
    tech_spec_id    TEXT,
    unit_price_limit REAL,
    total_price_limit REAL,
    delivery_place  TEXT,
    delivery_period TEXT,
    remark          TEXT,
    extended_desc   TEXT,
    project_org_id  INTEGER,
    project_org_name TEXT,
    demand_month    TEXT,
    source          TEXT DEFAULT 'goods_list_xlsx',
    source_file     TEXT,
    created_at      TEXT DEFAULT (datetime('now'))
);
CREATE INDEX IF NOT EXISTS idx_items_notice_id ON bid_items(notice_id);
CREATE INDEX IF NOT EXISTS idx_items_material_name ON bid_items(material_name);
CREATE UNIQUE INDEX IF NOT EXISTS idx_items_unique ON bid_items(
    notice_id, COALESCE(sub_bid_code,''), COALESCE(sub_bid_name,''),
    COALESCE(package_no,''), COALESCE(material_name,'')
);

-- 物资需求明细: 按物资+单位+月份汇总
CREATE TABLE IF NOT EXISTS material_demand_item (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    material_name   TEXT NOT NULL,
    unit            TEXT NOT NULL,
    demand_month    TEXT NOT NULL,
    demand_quantity REAL NOT NULL,
    notice_count    INTEGER,
    created_at      TEXT DEFAULT (datetime('now')),
    UNIQUE(material_name, unit, demand_month)
);
CREATE INDEX IF NOT EXISTS idx_mdi_month ON material_demand_item(demand_month);
CREATE INDEX IF NOT EXISTS idx_mdi_material ON material_demand_item(material_name);

-- 物资需求总计: 按物资+月份汇总 (跨单位求和)
CREATE TABLE IF NOT EXISTS material_demand_total (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    material_name   TEXT NOT NULL,
    demand_month    TEXT NOT NULL,
    demand_quantity REAL NOT NULL,
    notice_count    INTEGER,
    created_at      TEXT DEFAULT (datetime('now')),
    UNIQUE(material_name, demand_month)
);
CREATE INDEX IF NOT EXISTS idx_mdt_month ON material_demand_total(demand_month);
CREATE INDEX IF NOT EXISTS idx_mdt_material ON material_demand_total(material_name);
"""


def init_db(db_path: str = None) -> sqlite3.Connection:
    if db_path is None:
        db_path = DB_PATH
    os.makedirs(os.path.dirname(db_path), exist_ok=True)
    conn = sqlite3.connect(db_path)
    conn.executescript(CLEANUP_SQL)
    conn.executescript(SCHEMA_SQL)
    conn.commit()
    return conn


def get_connection(db_path: str = None) -> sqlite3.Connection:
    if db_path is None:
        db_path = DB_PATH
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    return conn


if __name__ == "__main__":
    conn = init_db()
    cursor = conn.execute("SELECT name FROM sqlite_master WHERE type='table' ORDER BY name")
    tables = [row[0] for row in cursor]
    print(f"表: {tables}")
    conn.close()
