"""
ECP2.0 国网冀北电力物资采购数据采集流水线

流程:
  Phase 1: 从 noteList API 采集公告元数据 → bid_notices 表
  Phase 2: 关键词分类 (物资/服务/工程)
  Phase 3: 下载货物清单 ZIP → data/excels/{code}.zip
  Phase 4: 解压 → 识别货物清单XLSX → 解析 → bid_items 表
  Phase 5: 复核检查 → 输出差异报告

执行:
  python src/pipeline.py              # 增量模式 (只下载未处理的)
  python src/pipeline.py --full       # 全量模式 (采集全部)
  python src/pipeline.py --verify     # 仅复核检查
"""
import sys, os, io, re, json, time, zipfile, hashlib, subprocess, shutil
from datetime import datetime
from collections import Counter
from pathlib import Path

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')

import requests
import openpyxl

from crawler.ecp_client import EcpClient, ORG_MAP
from db.schema import init_db, get_connection

# ============================================================
# 配置
# ============================================================
TARGET_ORG_ID = ORG_MAP["国网冀北电力有限公司"]
DOWNLOAD_URL = "https://ecp.sgcc.com.cn/ecp2.0/ecpwcmcore//index/downLoadBid"
PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
EXCEL_DIR = os.path.join(PROJECT_ROOT, "data", "excels")
EXTRACT_DIR = os.path.join(PROJECT_ROOT, "data", "extracted")
DB_PATH = os.path.join(PROJECT_ROOT, "data", "ecp_data.db")
FAILED_LOG = os.path.join(PROJECT_ROOT, "data", "failed_parse.json")

HTTP_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) Chrome/120.0.0.0",
    "Referer": "https://ecp.sgcc.com.cn/ecp2.0/portal/",
}

MATERIAL_KW = [
    "物资", "设备", "材料", "电缆", "变压器", "开关柜",
    "组合电器", "GIS", "断路器", "互感器", "避雷器",
    "绝缘子", "电容器", "电抗器", "导线", "光缆",
    "铁塔", "钢管杆", "金具", "线夹", "箱式变电站",
    "环网柜", "配电终端", "电能表",
]

# ============================================================
# Excel 列映射 — 基于表头自动检测, 而非硬编码列号
# ============================================================
GOODS_LIST_SIGNATURE = "物资名称"  # 表头必须包含此列才是货物清单


def detect_column_map(headers: list[str]) -> dict[str, int] | None:
    """
    根据表头名称自动检测列索引。
    支持23列和27列等不同版本的Excel。
    返回 {字段名: 列索引}, 不是货物清单则返回 None。
    """
    if GOODS_LIST_SIGNATURE not in headers:
        return None

    col_map = {}
    for i, h in enumerate(headers):
        h = h.strip().replace(' ', '').replace('\n', '')
        if '分标编号' in h:
            col_map['sub_bid_code'] = i
        elif h == '包名称':
            col_map['package_name'] = i
        elif '分包编号' in h:
            col_map['package_no'] = i
        elif '项目单位' in h:
            col_map['project_org'] = i
        elif '需求单位' in h:
            col_map['demand_org'] = i
        elif '项目名称' in h:
            col_map['project_name'] = i
        elif '电压等级' in h:
            col_map['voltage_level'] = i
        elif h == '物资名称':
            col_map['material_name'] = i
        elif '物资描述' in h:
            col_map['material_desc'] = i
        elif h == '单位':
            col_map['unit'] = i
        elif h == '数量':
            col_map['quantity'] = i
        elif '首批' in h:
            col_map['delivery_first'] = i
        elif '最后' in h:
            col_map['delivery_last'] = i
        elif '交货地点' in h:
            col_map['delivery_place'] = i
        elif '交货方式' in h:
            col_map['delivery_method'] = i
        elif h == '备注':
            col_map['remark'] = i
        elif '物料编码' in h:
            col_map['material_code'] = i
        elif '扩展描述' in h:
            col_map['extended_desc'] = i
        elif '技术规范' in h:
            col_map['tech_spec_code'] = i

    # 核心三列必须存在
    if all(k in col_map for k in ['material_name', 'unit', 'quantity']):
        return col_map
    return None


# 已知的硬编码修正 (notice_id → {sheet_name → {col_name → col_index}})
# 当自动检测失败时使用。从 failed_parse.json 分析后填入。
HARDCODED_FIXES: dict[str, dict] = {}

def load_hardcoded_fixes():
    """从配置文件加载硬编码修正"""
    global HARDCODED_FIXES
    fix_path = os.path.join(PROJECT_ROOT, "data", "hardcoded_fixes.json")
    if os.path.exists(fix_path):
        with open(fix_path, 'r', encoding='utf-8') as f:
            HARDCODED_FIXES = json.load(f)
    return HARDCODED_FIXES


def save_hardcoded_fixes():
    """保存硬编码修正"""
    fix_path = os.path.join(PROJECT_ROOT, "data", "hardcoded_fixes.json")
    with open(fix_path, 'w', encoding='utf-8') as f:
        json.dump(HARDCODED_FIXES, f, ensure_ascii=False, indent=2)


# ============================================================
# 工具函数
# ============================================================
def safe_cell(row: tuple, idx: int | None) -> str | None:
    if idx is None or idx >= len(row):
        return None
    val = row[idx]
    return str(val).strip() if val is not None else None


def safe_float(val) -> float | None:
    if val is None:
        return None
    try:
        return float(str(val).strip())
    except (ValueError, TypeError):
        return None


def extract_city(org_name: str) -> str | None:
    if not org_name:
        return None
    for city in ['唐山', '承德', '张家口', '秦皇岛', '廊坊', '北京']:
        if city in org_name:
            return city
    return None


# ============================================================
# Phase 1+2: 列表采集
# ============================================================
def phase1_collect(conn):
    print("\n" + "=" * 60)
    print("Phase 1+2: 公告列表采集 + 分类")
    print("=" * 60)

    client = EcpClient(timeout=30, retry=3)
    result = client.query_all(org_id=TARGET_ORG_ID, page_size=50)
    cursor = conn.cursor()
    material_count = 0

    for n in result.notices:
        cat = "material" if any(kw in n.title for kw in MATERIAL_KW) else "service"
        if "工程" in n.title and "服务" not in n.title:
            cat = "engineering"

        batch = ""
        m = re.search(r"第([一二三四五六七八九十\d]+)次", n.title)
        if m:
            batch = f"第{m.group(1)}次"

        year = None
        m = re.search(r"(20\d{2})", n.title)
        if m:
            year = int(m.group(1))

        # 使用 INSERT OR IGNORE + UPDATE 保留 excel_status
        cursor.execute("""
            INSERT OR IGNORE INTO bid_notices
                (notice_id, title, code, publish_org_name, org_id,
                 notice_publish_time, notice_type, notice_type_name,
                 doctype, doc_id, doc_url, zbflag,
                 category, bid_batch, bid_year, fetched_at)
            VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,datetime('now'))
        """, (
            n.notice_id, n.title, n.code, n.publish_org_name, n.org_id,
            n.notice_publish_time, n.notice_type, n.notice_type_name,
            n.doctype, n.first_page_doc_id, n.doc_url, 0,
            cat, batch, year,
        ))
        # 更新元数据（但不覆盖 excel_status）
        cursor.execute("""
            UPDATE bid_notices
            SET title=?, code=?, publish_org_name=?,
                notice_publish_time=?, notice_type=?, notice_type_name=?,
                doctype=?, doc_id=?, doc_url=?,
                category=?, bid_batch=?, bid_year=?,
                updated_at=datetime('now')
            WHERE notice_id=?
        """, (
            n.title, n.code, n.publish_org_name,
            n.notice_publish_time, n.notice_type, n.notice_type_name,
            n.doctype, n.first_page_doc_id, n.doc_url,
            cat, batch, year,
            n.notice_id,
        ))
        if cat == "material":
            material_count += 1

    conn.commit()
    print(f"入库: {len(result.notices)} 条 (物资类: {material_count})")
    return material_count


# ============================================================
# Phase 3: 下载
# ============================================================
def phase3_download(conn, full_mode: bool = False) -> list[dict]:
    print("\n" + "=" * 60)
    print("Phase 3: 下载货物清单ZIP")
    print("=" * 60)

    cursor = conn.cursor()
    cursor.execute("""
        SELECT notice_id, title, code, notice_publish_time, doc_id
        FROM bid_notices
        WHERE category = 'material' AND doctype = 'doci-bid'
        ORDER BY notice_publish_time DESC
    """)
    notices = [dict(zip(['notice_id','title','code','pub_time','doc_id'], r))
               for r in cursor]
    print(f"物资正刊公告: {len(notices)} 条")

    os.makedirs(EXCEL_DIR, exist_ok=True)
    tasks = []
    new_downloads = 0

    for n in notices:
        notice_id = n['notice_id']
        code = n['code'] or notice_id

        # 文件名: code唯一则用code, 否则加noticeId后缀
        cursor.execute("SELECT COUNT(*) FROM bid_notices WHERE code=?", (code,))
        if cursor.fetchone()[0] > 1:
            zip_name = f"{code}_{notice_id}"
        else:
            zip_name = code

        zip_path = os.path.join(EXCEL_DIR, f"{zip_name}.zip")

        if os.path.exists(zip_path) and os.path.getsize(zip_path) > 1000:
            tasks.append({**n, 'zip_path': zip_path, 'zip_name': zip_name, 'status': 'exists'})
            # 确保已有文件的状态正确
            cursor.execute(
                "UPDATE bid_notices SET excel_status='downloaded' WHERE notice_id=? AND excel_status='pending'",
                (notice_id,))
            continue

        if full_mode or not os.path.exists(zip_path):
            url = f"{DOWNLOAD_URL}?noticeId={notice_id}&noticeDetId="
            try:
                resp = requests.get(url, headers=HTTP_HEADERS, timeout=30)
                if resp.status_code == 200 and len(resp.content) > 1000:
                    with open(zip_path, 'wb') as f:
                        f.write(resp.content)
                    tasks.append({**n, 'zip_path': zip_path, 'zip_name': zip_name,
                                  'status': 'downloaded'})
                    cursor.execute(
                        "UPDATE bid_notices SET excel_status='downloaded' WHERE notice_id=?",
                        (notice_id,))
                    new_downloads += 1
                    time.sleep(0.3)
                else:
                    tasks.append({**n, 'zip_path': None, 'zip_name': zip_name,
                                  'status': f'empty({len(resp.content)}b)'})
                    cursor.execute(
                        "UPDATE bid_notices SET excel_status='no_file' WHERE notice_id=?",
                        (notice_id,))
            except Exception as e:
                tasks.append({**n, 'zip_path': None, 'zip_name': zip_name,
                              'status': f'error: {str(e)[:50]}'})
                cursor.execute(
                    "UPDATE bid_notices SET excel_status='no_file' WHERE notice_id=?",
                    (notice_id,))
        conn.commit()

    conn.commit()
    done = sum(1 for t in tasks if t['status'] in ('downloaded', 'exists'))
    skipped = sum(1 for t in tasks if t['zip_path'] is None)
    print(f"可处理: {done} 条 (新下载: {new_downloads})")
    print(f"跳过: {skipped} 条")
    return tasks


# ============================================================
# Phase 4: 解析
# ============================================================
def phase4_parse(conn, tasks: list[dict]) -> dict:
    print("\n" + "=" * 60)
    print("Phase 4: 解压 + 解析货物清单")
    print("=" * 60)

    cursor = conn.cursor()
    os.makedirs(EXTRACT_DIR, exist_ok=True)
    load_hardcoded_fixes()

    stats = {'success': 0, 'failed': 0, 'skipped': 0, 'total_items': 0}
    failed_entries = []
    all_materials = Counter()
    all_units = Counter()
    all_cities = Counter()

    for i, task in enumerate(tasks):
        notice_id = task['notice_id']
        title = task['title'][:70]

        if task['zip_path'] is None:
            stats['skipped'] += 1
            continue

        zip_size = os.path.getsize(task['zip_path']) / 1024
        if zip_size < 100:
            stats['skipped'] += 1
            continue

        # 检查是否已解析 (增量模式跳过)
        cursor.execute("SELECT COUNT(*) FROM bid_items WHERE notice_id=?", (notice_id,))
        already_parsed = cursor.fetchone()[0]

        if already_parsed > 0:
            print(f"[{i+1}/{len(tasks)}] {task['pub_time']} {title[:55]}... "
                  f"({already_parsed}条已入库, 跳过)")
            stats['success'] += 1
            continue

        print(f"[{i+1}/{len(tasks)}] {task['pub_time']} {title[:55]}...")

        # 解压
        extract_subdir = os.path.join(EXTRACT_DIR, notice_id)
        if os.path.exists(extract_subdir):
            shutil.rmtree(extract_subdir)
        os.makedirs(extract_subdir, exist_ok=True)

        try:
            subprocess.run(['unzip', '-o', '-UU', task['zip_path'], '-d', extract_subdir],
                          capture_output=True, timeout=30)
        except Exception:
            pass

        # 找所有XLSX, 用表头识别货物清单
        goods_list_xlsx = None
        for root, dirs, files in os.walk(extract_subdir):
            for f in files:
                if f.endswith('.xlsx'):
                    fp = os.path.join(root, f)
                    if os.path.getsize(fp) < 2000:  # XLSX最小约5KB, <2KB的不是有效文件
                        continue
                    try:
                        wb = openpyxl.load_workbook(fp, data_only=True)
                        if wb.sheetnames:
                            ws = wb[wb.sheetnames[0]]
                            h_row = [str(c.value or '') for c in
                                     next(ws.iter_rows(min_row=1, max_row=1))]
                            if GOODS_LIST_SIGNATURE in h_row:
                                goods_list_xlsx = fp
                                wb.close()
                                break
                        wb.close()
                    except Exception:
                        continue
            if goods_list_xlsx:
                break

        if not goods_list_xlsx:
            print(f"  -> 未找到货物清单XLSX")
            cursor.execute(
                "UPDATE bid_notices SET excel_status='no_file' WHERE notice_id=?",
                (notice_id,))
            conn.commit()
            stats['failed'] += 1
            failed_entries.append({'notice_id': notice_id, 'title': title,
                                    'reason': 'no_goods_list_xlsx'})
            continue

        # 保存Excel到 data/excels
        excel_name = task['zip_name'] + ".xlsx"
        saved_excel = os.path.join(EXCEL_DIR, excel_name)
        shutil.copy2(goods_list_xlsx, saved_excel)

        # 解析
        items, parse_errors = parse_goods_list(notice_id, goods_list_xlsx)

        if not items and not parse_errors:
            parse_errors.append("all_sheets_no_goods_list_signature")

        if not items:
            print(f"  -> 解析到0条 (原因: {parse_errors[:2]})")
            cursor.execute(
                "UPDATE bid_notices SET excel_status='parse_failed' WHERE notice_id=?",
                (notice_id,))
            conn.commit()
            stats['failed'] += 1
            failed_entries.append({
                'notice_id': notice_id, 'title': title,
                'reason': 'zero_items',
                'errors': parse_errors,
                'excel_path': saved_excel,
                'zip_name': task['zip_name'],
            })
            continue

        # 入库 bid_items
        inserted = 0
        for item in items:
            try:
                cursor.execute("""
                    INSERT OR IGNORE INTO bid_items
                        (notice_id, sub_bid_code, sub_bid_name, package_no,
                         material_name, material_desc, demand_quantity, unit,
                         project_org_name, delivery_place, remark,
                         material_code, extended_desc,
                         source, source_file)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """, (
                    notice_id,
                    item.get('sub_bid_code'), item.get('sub_bid_name'),
                    item.get('package_no'), item.get('material_name'),
                    item.get('material_desc'), item.get('demand_quantity'),
                    item.get('unit'), item.get('project_org_name'),
                    item.get('delivery_place'), item.get('remark'),
                    item.get('material_code'), item.get('extended_desc'),
                    'goods_list_xlsx', excel_name,
                ))
                inserted += cursor.rowcount

                if item.get('material_name'):
                    short = item['material_name'].split(',')[0].split('(')[0].strip()
                    all_materials[short] += 1
                if item.get('unit'):
                    all_units[item['unit']] += 1
                city = extract_city(item.get('project_org_name', '') or '')
                if city:
                    all_cities[city] += 1
            except Exception as e:
                parse_errors.append(f"insert: {str(e)[:100]}")

        conn.commit()

        # 入库项目单位
        orgs_seen = set()
        for item in items:
            for key in ('project_org_name', 'demand_org_name'):
                name = item.get(key)
                if name and name not in orgs_seen:
                    orgs_seen.add(name)
                    city = extract_city(name)
                    cursor.execute("""
                        INSERT OR IGNORE INTO org_units
                            (parent_org_id, parent_org_name, org_name, city, province,
                             first_seen_notice_id, first_seen_date)
                        VALUES (?, ?, ?, ?, ?, ?, ?)
                    """, (TARGET_ORG_ID, "国网冀北电力有限公司", name,
                          city, "河北省" if city and city != "北京" else "北京市",
                          notice_id, task['pub_time']))
        conn.commit()

        # 标记解析成功
        cursor.execute("""
            UPDATE bid_notices
            SET excel_status='parsed', excel_path=?, detail_fetched=1,
                detail_fetched_at=datetime('now')
            WHERE notice_id=?
        """, (excel_name, notice_id))
        conn.commit()

        stats['success'] += 1
        stats['total_items'] += len(items)
        print(f"  -> {inserted}/{len(items)} 条, {len(orgs_seen)} 单位"
              f" (Excel: {excel_name})")
        if parse_errors:
            print(f"  -> ⚠️ {len(parse_errors)} 个警告: {parse_errors[:2]}")

    # 保存失败记录
    if failed_entries:
        with open(FAILED_LOG, 'w', encoding='utf-8') as f:
            json.dump(failed_entries, f, ensure_ascii=False, indent=2)
        print(f"\n失败记录: {FAILED_LOG} ({len(failed_entries)} 条)")

    stats['failed_entries'] = failed_entries
    stats['materials'] = all_materials
    stats['units'] = all_units
    stats['cities'] = all_cities
    return stats


def parse_goods_list(notice_id: str, xlsx_path: str) -> tuple[list[dict], list[str]]:
    """
    解析货物清单Excel。
    自动检测表头列映射 + 硬编码修正。
    """
    items = []
    errors = []

    try:
        wb = openpyxl.load_workbook(xlsx_path, data_only=True)
        fixes = HARDCODED_FIXES.get(notice_id, {})

        for sheet_name in wb.sheetnames:
            ws = wb[sheet_name]
            rows = list(ws.iter_rows(values_only=True))
            if len(rows) < 2:
                continue

            headers = [str(c or '') for c in rows[0]]
            col_map = detect_column_map(headers)
            if col_map is None:
                continue  # 不是货物清单Sheet

            # 应用硬编码修正
            if sheet_name in fixes:
                col_map.update(fixes[sheet_name])

            for row_idx, row in enumerate(rows[1:], 2):
                try:
                    mat_name = safe_cell(row, col_map.get('material_name'))
                    if not mat_name or mat_name == 'None':
                        continue

                    # 合并附加信息到remark
                    remark_parts = []
                    for key, label in [('voltage_level', '电压等级'),
                                       ('delivery_method', '交货方式'),
                                       ('delivery_first', '首批交货'),
                                       ('delivery_last', '最后交货'),
                                       ('project_name', '项目'),
                                       ('tech_spec_code', '技术规范')]:
                        v = safe_cell(row, col_map.get(key))
                        if v:
                            remark_parts.append(f"{label}:{v}")

                    items.append({
                        'sub_bid_code': safe_cell(row, col_map.get('sub_bid_code')),
                        'sub_bid_name': sheet_name,
                        'package_no': (safe_cell(row, col_map.get('package_no')) or
                                       safe_cell(row, col_map.get('package_name'))),
                        'material_name': mat_name,
                        'material_desc': safe_cell(row, col_map.get('material_desc')),
                        'demand_quantity': safe_float(safe_cell(row, col_map.get('quantity'))),
                        'unit': safe_cell(row, col_map.get('unit')),
                        'project_org_name': safe_cell(row, col_map.get('project_org')),
                        'demand_org_name': safe_cell(row, col_map.get('demand_org')),
                        'delivery_place': safe_cell(row, col_map.get('delivery_place')),
                        'remark': '; '.join(remark_parts) if remark_parts else None,
                        'material_code': safe_cell(row, col_map.get('material_code')),
                        'extended_desc': safe_cell(row, col_map.get('extended_desc')),
                    })
                except Exception as e:
                    errors.append(f"[{sheet_name}]R{row_idx}: {e}")

        wb.close()
    except Exception as e:
        errors.append(f"open: {e}")

    return items, errors


# ============================================================
# Phase 5: 复核检查
# ============================================================
def phase5_verify(conn):
    print("\n" + "=" * 60)
    print("Phase 5: 复核检查")
    print("=" * 60)

    cursor = conn.cursor()

    # 物资正刊总数
    cursor.execute("""
        SELECT COUNT(*) FROM bid_notices
        WHERE category='material' AND doctype='doci-bid'
    """)
    total_material = cursor.fetchone()[0]

    # 状态分布
    cursor.execute("""
        SELECT excel_status, COUNT(*) FROM bid_notices
        WHERE category='material' AND doctype='doci-bid'
        GROUP BY excel_status
    """)
    status_dist = {r[0]: r[1] for r in cursor}

    cursor.execute("""
        SELECT COUNT(DISTINCT notice_id) FROM bid_items
    """)
    with_items = cursor.fetchone()[0]

    print(f"\n物资正刊: {total_material} 条")
    print(f"状态分布: pending={status_dist.get('pending',0)} | downloaded={status_dist.get('downloaded',0)} | parsed={status_dist.get('parsed',0)} | no_file={status_dist.get('no_file',0)} | parse_failed={status_dist.get('parse_failed',0)}")
    print(f"有明细: {with_items} 条公告")

    # 未处理列表 (不是 parsed 状态的)
    cursor.execute("""
        SELECT notice_id, title, notice_publish_time, code, excel_status
        FROM bid_notices
        WHERE category='material' AND doctype='doci-bid'
          AND excel_status != 'parsed'
        ORDER BY notice_publish_time DESC
    """)
    pending = [dict(zip(['nid','title','pt','code','status'], r)) for r in cursor]
    if pending:
        print(f"\n未完成解析 ({len(pending)} 条):")
        for p in pending[:10]:
            print(f"  [{p['status']}] [{p['pt']}] {p['title'][:65]}")
        if len(pending) > 10:
            print(f"  ... 还有 {len(pending)-10} 条")

    # 重复检查
    cursor.execute("""
        SELECT COUNT(*) FROM (
            SELECT notice_id, sub_bid_code, sub_bid_name, package_no, material_name
            FROM bid_items
            GROUP BY 1,2,3,4,5 HAVING COUNT(*) > 1
        )
    """)
    dup_groups = cursor.fetchone()[0]
    print(f"\n{'✅ 无重复' if dup_groups == 0 else f'⚠️ {dup_groups} 组重复'}")

    # 统计
    cursor.execute("SELECT COUNT(*) FROM bid_items")
    total_items = cursor.fetchone()[0]

    cursor.execute("SELECT COUNT(*) FROM org_units")
    org_count = cursor.fetchone()[0]

    # 城市维度
    cursor.execute("""
        SELECT o.city, COUNT(DISTINCT i.notice_id), COUNT(i.id)
        FROM bid_items i
        JOIN org_units o ON i.project_org_name = o.org_name
        WHERE o.city IS NOT NULL
        GROUP BY o.city ORDER BY COUNT(i.id) DESC
    """)
    city_stats = cursor.fetchall()

    print(f"\n总明细: {total_items} 条 | 单位: {org_count} 个")
    print(f"城市分布:")
    for city, nc, ic in city_stats:
        print(f"  {city}: {ic} 条, {nc} 公告")

    # 失败记录
    if os.path.exists(FAILED_LOG):
        with open(FAILED_LOG, 'r', encoding='utf-8') as f:
            failed = json.load(f)
        print(f"\n失败记录 ({len(failed)} 条):")
        for fe in failed:
            print(f"  [{fe['notice_id']}] {fe['reason']}: {fe.get('title','')[:60]}")

    return {
        'total_material': total_material,
        'parsed': status_dist.get('parsed', 0),
        'with_items': with_items, 'pending': len(pending),
        'total_items': total_items, 'org_count': org_count,
        'status_dist': status_dist,
        'failed': len(json.load(open(FAILED_LOG, encoding='utf-8')))
                  if os.path.exists(FAILED_LOG) else 0,
    }


# ============================================================
# 主入口
# ============================================================
def main():
    full_mode = '--full' in sys.argv
    verify_only = '--verify' in sys.argv

    print(f"ECP2.0 物资采购数据流水线")
    print(f"模式: {'全量' if full_mode else '增量'}"
          f"{' (仅复核)' if verify_only else ''}")
    print(f"开始: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")

    conn = init_db(DB_PATH)

    if verify_only:
        phase5_verify(conn)
        conn.close()
        return

    # 确保db schema中有下载追踪字段
    try:
        conn.execute("ALTER TABLE bid_notices ADD COLUMN excel_path TEXT")
    except:
        pass

    phase1_collect(conn)
    tasks = phase3_download(conn, full_mode=full_mode)
    stats = phase4_parse(conn, tasks)
    result = phase5_verify(conn)

    print(f"\n{'='*60}")
    print("流水线完成")
    print(f"{'='*60}")
    sd = result.get('status_dist', {})
    print(f"物资公告: {result['total_material']} | parsed={sd.get('parsed',0)} downloaded={sd.get('downloaded',0)} no_file={sd.get('no_file',0)} failed={sd.get('parse_failed',0)} pending={sd.get('pending',0)}")
    print(f"物资明细: {result['total_items']} | 单位: {result['org_count']}")
    print(f"Excel目录: {EXCEL_DIR}")
    print(f"数据库:   {DB_PATH}")
    print(f"完成时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")

    conn.close()


if __name__ == "__main__":
    main()
