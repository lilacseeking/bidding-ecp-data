"""ECP平台中标候选人公示 + 中标结果公告 数据采集与入库脚本

功能:
  1. 翻页遍历ECP平台所有中标候选人公示和中标结果公告
  2. 自动检测表格类型: 候选人公示(12列) vs 结果公告(6列)
  3. 解析HTML表格，提取结构化数据
  4. 保存CSV并导入SQLite数据库

用法:
  python ecp_bid_scraper.py                 # 采集 + 保存CSV
  python ecp_bid_scraper.py --import-db     # 采集 + 保存CSV + 导入数据库
  python ecp_bid_scraper.py --csv-only      # 仅从已有CSV导入数据库（不采集）

依赖: requests, sqlite3 (标准库)
"""
import argparse
import csv
import os
import re
import sqlite3
import time
from html.parser import HTMLParser

import requests

# ============================================================
# 常量配置
# ============================================================

ECP_BASE = "https://ecp.sgcc.com.cn/ecp2.0/ecpwcmcore/"

# 菜单ID
MENU_CANDIDATE = "2018060501171107"   # 推荐中标候选人公示
MENU_RESULT = "2018060501171111"      # 中标（成交）结果公告

PAGE_SIZE = 20

# 目标组织列表: (orgId, 全称, 简称)
ORG_LIST = [
    ("2019040100044796", "国家电网有限公司", "国网"),
    ("2019061900137008", "国网冀北电力有限公司", "冀北"),
]

# CSV输出列
CSV_COLS = [
    '组织', '公告标题', '公告日期', '公告ID', '来源',
    '分标编号', '包号', '项目单位', '分标名称',
    '中标候选人', '排序', '投标报价(万元)', '中标状态', '评标情况',
]

# 输出路径
OUTPUT_DIR = os.path.join(os.path.dirname(__file__), "outputs")
CSV_PATH = os.path.join(OUTPUT_DIR, "ecp_bid_candidates.csv")
DB_PATH = os.path.join(os.path.dirname(__file__), "data", "ecp_data.db")


# ============================================================
# HTML表格解析器
# ============================================================

class TableParser(HTMLParser):
    """轻量HTML表格解析器，提取所有<table>的结构化数据"""

    def __init__(self):
        super().__init__()
        self.tables = []
        self._in_table = self._in_row = self._in_cell = False
        self._current_table = []
        self._current_row = []
        self._cell_content = ""

    def handle_starttag(self, tag, attrs):
        if tag == 'table':
            self._in_table = True
            self._current_table = []
        elif tag == 'tr' and self._in_table:
            self._in_row = True
            self._current_row = []
        elif tag in ('td', 'th') and self._in_row:
            self._in_cell = True
            self._cell_content = ""

    def handle_endtag(self, tag):
        if tag in ('td', 'th') and self._in_cell:
            self._in_cell = False
            self._current_row.append(self._cell_content.strip())
        elif tag == 'tr' and self._in_row:
            self._in_row = False
            if self._current_row:
                self._current_table.append(self._current_row)
        elif tag == 'table' and self._in_table:
            self._in_table = False
            if self._current_table:
                self.tables.append(self._current_table)

    def handle_data(self, data):
        if self._in_cell:
            self._cell_content += data.strip() + " "


def parse_tables(html):
    """解析HTML中的表格，返回 [table[row[cell[str]]]]"""
    parser = TableParser()
    parser.feed(html)
    return parser.tables


# ============================================================
# ECP API 客户端
# ============================================================

_session = requests.Session()
_session.headers.update({
    'Content-Type': 'application/json',
    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36',
    'Referer': 'https://ecp.sgcc.com.cn/ecp2.0/portal/',
    'Origin': 'https://ecp.sgcc.com.cn',
})


def get_notices_page(menu_id, org_id, org_name, page=1, size=20):
    """获取公告列表（指定页）

    Returns:
        (notices_list, total_count)
    """
    payload = {
        "index": page,
        "size": size,
        "firstPageMenuId": menu_id,
        "orgId": int(org_id),
        "key": "",
        "orgName": org_name,
    }
    r = _session.post(f"{ECP_BASE}/index/noteList", json=payload, timeout=30)
    d = r.json()
    if d.get("successful"):
        rv = d.get("resultValue", {})
        return rv.get("noteList", []), rv.get("count", 0)
    return [], 0


def get_doc(notice_id):
    """获取公告详情HTML内容

    Returns:
        (html_content, file_flag) or (None, None)
    """
    try:
        r = _session.post(
            f"{ECP_BASE}/index/getNoticeWin",
            data=f'"{notice_id}"',
            headers={'Content-Type': 'application/json'},
            timeout=30,
        )
        d = r.json()
        if d.get("successful"):
            rv = d.get("resultValue", {})
            if isinstance(rv, dict):
                notice = rv.get("notice", {})
                return notice.get("CONT", ""), rv.get("fileFlag", "0")
    except Exception as e:
        print(f"    API错误({notice_id}): {e}")
    return None, None


# ============================================================
# 表格类型检测与数据提取
# ============================================================

def detect_table_type(table_rows):
    """检测表格类型

    Returns:
        'candidate' - 候选人公示(12列)
        'result'    - 结果公告(6列)
        None        - 无法识别
    """
    if not table_rows:
        return None
    header = table_rows[0]
    header_text = ''.join(header)

    if '中标候选人' in header_text or '推荐的' in header_text or '排序' in header_text:
        return 'candidate'
    if '中标状态' in header_text or '中标人' in header_text:
        return 'result'
    if len(header) >= 9:
        return 'candidate'
    if len(header) == 6:
        return 'result'
    return None


def extract_candidate_rows(table_rows):
    """提取候选人公示数据行 (12列，跳过子表头行)

    列: 分标编号|包号|项目单位|分标名称|中标候选人|排序|投标报价(万元)|质量|交货期|资格能力|评标情况|项目负责人
    """
    COLS = [
        '分标编号', '包号', '项目单位', '分标名称', '中标候选人', '排序',
        '投标报价(万元)', '质量', '交货期', '资格能力', '评标情况',
    ]
    records = []
    for row in table_rows:
        if len(row) < 6:
            continue
        # 跳过子表头行 "（万元）|姓名|证书名称|编号"
        if '万元' in ''.join(row) and len(row) <= 2:
            continue

        rec = {}
        for i, col in enumerate(COLS):
            if i < len(row):
                rec[col] = row[i].strip()

        bid_code = rec.get('分标编号', '')
        rank = rec.get('排序', '')
        if (bid_code.startswith('SG') or bid_code.startswith('JB')
                or rank in ('1', '2', '3')
                or re.match(r'^\d+$', rank)):
            records.append(rec)
    return records


def extract_result_rows(table_rows):
    """提取结果公告数据行 (6列)

    列: 分标编号|分标名称|包号|中标状态|项目单位|中标人
    """
    COLS = ['分标编号', '分标名称', '包号', '中标状态', '项目单位', '中标候选人']
    records = []
    for row in table_rows:
        if len(row) < 5:
            continue

        rec = {}
        for i, col in enumerate(COLS):
            if i < len(row):
                rec[col] = row[i].strip()

        bid_code = rec.get('分标编号', '')
        if bid_code.startswith('SG') or bid_code.startswith('JB'):
            records.append(rec)
        elif rec.get('中标候选人', '') and rec.get('项目单位', ''):
            records.append(rec)
    return records


def extract_rows(table_rows):
    """自动检测表格类型并提取数据行"""
    ttype = detect_table_type(table_rows)
    if ttype == 'candidate':
        return extract_candidate_rows(table_rows[1:])  # 跳过表头
    elif ttype == 'result':
        return extract_result_rows(table_rows[1:])
    # fallback: 两种都试
    rows = extract_candidate_rows(table_rows)
    if not rows:
        rows = extract_result_rows(table_rows)
    return rows


# ============================================================
# 全量采集
# ============================================================

def scrape_all():
    """全量采集候选人公示 + 结果公告

    Returns:
        (candidate_records, result_records)
    """
    all_candidates = []
    all_results = []
    stats = {
        'candidate_ok': 0, 'candidate_expired': 0, 'candidate_attachment': 0,
        'result_ok': 0, 'result_attachment': 0,
    }

    # --- Part 1: 中标候选人公示 ---
    print("=" * 70)
    print("Part 1: 中标候选人公示 - 全量翻页采集")
    print("=" * 70)

    for org_id, org_name, org_short in ORG_LIST:
        print(f"\n--- {org_short}: {org_name} ---")
        empty_pages = 0

        for page in range(1, 200):
            notices, _ = get_notices_page(MENU_CANDIDATE, org_id, org_name,
                                          page=page, size=PAGE_SIZE)
            if not notices:
                print(f"  第{page}页无数据，翻页结束")
                break

            page_ok = 0
            for n in notices:
                title = n.get('title', '')
                nid = str(n.get('id', ''))
                date = n.get('noticePublishTime', '')

                cont, file_flag = get_doc(nid)
                if not cont:
                    continue

                # 过期检查
                if '公示内容已于' in cont or '完成公示' in cont:
                    stats['candidate_expired'] += 1
                    continue

                # 附件形式
                if len(cont) < 200 or ('详见附件' in cont[:50] and file_flag == '1'):
                    stats['candidate_attachment'] += 1
                    continue

                tables = parse_tables(cont)
                found = 0
                for table in tables:
                    rows = extract_rows(table)
                    for rec in rows:
                        rec['组织'] = org_short
                        rec['公告标题'] = title
                        rec['公告日期'] = date
                        rec['公告ID'] = nid
                        rec['来源'] = '候选人公示'
                        all_candidates.append(rec)
                        found += 1

                if found > 0:
                    stats['candidate_ok'] += 1
                    page_ok += found

            print(f"  第{page}页: 提取 {page_ok} 条 (累计 {len(all_candidates)} 条)")

            if page_ok == 0:
                empty_pages += 1
                if empty_pages >= 3:
                    print(f"  连续{empty_pages}页无有效数据，停止翻页")
                    break
            else:
                empty_pages = 0

            if len(notices) < PAGE_SIZE:
                print(f"  本页不足{PAGE_SIZE}条，翻页结束")
                break

            if page % 5 == 0:
                time.sleep(1)

    print(f"\n  候选人公示采集完毕: {len(all_candidates)} 条")
    print(f"  统计: 成功={stats['candidate_ok']}, "
          f"过期={stats['candidate_expired']}, 附件={stats['candidate_attachment']}")

    # --- Part 2: 中标结果公告 ---
    print(f"\n{'=' * 70}")
    print("Part 2: 中标（成交）结果公告 - 全量翻页采集")
    print("=" * 70)

    for org_id, org_name, org_short in ORG_LIST:
        print(f"\n--- {org_short}: {org_name} ---")
        empty_pages = 0

        for page in range(1, 200):
            notices, _ = get_notices_page(MENU_RESULT, org_id, org_name,
                                          page=page, size=PAGE_SIZE)
            if not notices:
                print(f"  第{page}页无数据，翻页结束")
                break

            page_ok = 0
            for n in notices:
                title = n.get('title', '')
                nid = str(n.get('id', ''))
                date = n.get('noticePublishTime', '')

                cont, file_flag = get_doc(nid)
                if not cont or len(cont) < 200:
                    continue

                if '详见附件' in cont[:50] and file_flag == '1':
                    stats['result_attachment'] += 1
                    continue

                tables = parse_tables(cont)
                found = 0
                for table in tables:
                    rows = extract_rows(table)
                    for rec in rows:
                        rec['组织'] = org_short
                        rec['公告标题'] = title
                        rec['公告日期'] = date
                        rec['公告ID'] = nid
                        rec['来源'] = '结果公告'
                        all_results.append(rec)
                        found += 1

                if found > 0:
                    stats['result_ok'] += 1
                    page_ok += found

            print(f"  第{page}页: 提取 {page_ok} 条 (累计 {len(all_results)} 条)")

            if page_ok == 0:
                empty_pages += 1
                if empty_pages >= 3:
                    print(f"  连续{empty_pages}页无有效数据，停止翻页")
                    break
            else:
                empty_pages = 0

            if len(notices) < PAGE_SIZE:
                print(f"  本页不足{PAGE_SIZE}条，翻页结束")
                break

            if page % 5 == 0:
                time.sleep(1)

    print(f"\n  结果公告采集完毕: {len(all_results)} 条")

    return all_candidates, all_results


def save_csv(candidates, results):
    """保存采集结果到CSV"""
    combined = candidates + results
    os.makedirs(OUTPUT_DIR, exist_ok=True)

    with open(CSV_PATH, 'w', newline='', encoding='utf-8-sig') as f:
        writer = csv.DictWriter(f, fieldnames=CSV_COLS, extrasaction='ignore')
        writer.writeheader()
        writer.writerows(combined)

    print(f"\n已保存CSV: {CSV_PATH} ({len(combined)} 条)")
    print(f"  候选人公示: {len(candidates)} 条")
    print(f"  结果公告:   {len(results)} 条")
    return CSV_PATH


# ============================================================
# 数据库导入
# ============================================================

CREATE_TABLE_SQL = """
CREATE TABLE IF NOT EXISTS ecp_bid_records (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    org             TEXT NOT NULL,
    notice_title    TEXT,
    notice_date     TEXT,
    notice_id       TEXT,
    source          TEXT NOT NULL,
    bid_code        TEXT,
    package_no      TEXT,
    project_unit    TEXT,
    bid_name        TEXT,
    candidate       TEXT,
    rank            TEXT,
    bid_price       TEXT,
    bid_status      TEXT,
    evaluation      TEXT,
    created_at      TEXT DEFAULT (datetime('now'))
);
CREATE INDEX IF NOT EXISTS idx_ecp_org ON ecp_bid_records(org);
CREATE INDEX IF NOT EXISTS idx_ecp_source ON ecp_bid_records(source);
CREATE INDEX IF NOT EXISTS idx_ecp_notice_date ON ecp_bid_records(notice_date);
CREATE INDEX IF NOT EXISTS idx_ecp_bid_code ON ecp_bid_records(bid_code);
CREATE INDEX IF NOT EXISTS idx_ecp_candidate ON ecp_bid_records(candidate);
CREATE INDEX IF NOT EXISTS idx_ecp_bid_name ON ecp_bid_records(bid_name);
CREATE INDEX IF NOT EXISTS idx_ecp_notice_id ON ecp_bid_records(notice_id);
"""


def import_to_db():
    """将CSV数据导入SQLite数据库"""
    if not os.path.exists(CSV_PATH):
        print(f"错误: CSV文件不存在 {CSV_PATH}")
        return

    os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    conn.executescript(CREATE_TABLE_SQL)
    conn.commit()

    # 清空旧数据
    conn.execute("DELETE FROM ecp_bid_records")
    conn.commit()

    # 导入
    rows = list(csv.DictReader(open(CSV_PATH, 'r', encoding='utf-8-sig')))
    print(f"CSV记录数: {len(rows)}")

    insert_sql = """
    INSERT INTO ecp_bid_records (org, notice_title, notice_date, notice_id, source,
        bid_code, package_no, project_unit, bid_name, candidate, rank, bid_price,
        bid_status, evaluation)
    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    """

    batch = []
    for r in rows:
        batch.append((
            r.get('组织', ''), r.get('公告标题', ''), r.get('公告日期', ''),
            r.get('公告ID', ''), r.get('来源', ''), r.get('分标编号', ''),
            r.get('包号', ''), r.get('项目单位', ''), r.get('分标名称', ''),
            r.get('中标候选人', ''), r.get('排序', ''), r.get('投标报价(万元)', ''),
            r.get('中标状态', ''), r.get('评标情况', ''),
        ))
        if len(batch) >= 1000:
            conn.executemany(insert_sql, batch)
            batch = []

    if batch:
        conn.executemany(insert_sql, batch)
    conn.commit()

    total = conn.execute("SELECT COUNT(*) FROM ecp_bid_records").fetchone()[0]
    print(f"数据库导入完成: {total} 条 → {DB_PATH}")
    conn.close()


# ============================================================
# 入口
# ============================================================

def main():
    parser = argparse.ArgumentParser(description='ECP平台中标候选人/结果公告采集工具')
    parser.add_argument('--import-db', action='store_true', help='采集后导入数据库')
    parser.add_argument('--csv-only', action='store_true', help='仅导入已有CSV（不采集）')
    args = parser.parse_args()

    if args.csv_only:
        import_to_db()
        return

    candidates, results = scrape_all()
    save_csv(candidates, results)

    if args.import_db:
        import_to_db()


if __name__ == "__main__":
    main()
