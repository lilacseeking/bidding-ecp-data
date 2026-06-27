"""
ECP2.0 数据采集 MVP 流水线

功能：
  Phase 1: 从 noteList API 采集公告元数据 → SQLite (无需登录) ✅
  Phase 2: 关键词分类 + 字段推断 → SQLite (无需登录) ✅
  Phase 3: 详情页采集 → SQLite (需登录认证) ⚠️ 待实现
  Phase 4: 附件下载与解析 → CSV (需登录认证) ⚠️ 待实现

执行: python src/mvp_pipeline.py
"""
import sys
import os
import io
import re
import json
import time
from datetime import datetime
from collections import Counter

# Fix Windows console encoding
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from crawler.ecp_client import EcpClient, ORG_MAP
from db.schema import init_db, get_connection

# ============================================================
# 配置
# ============================================================
TARGET_ORG = "国网冀北电力有限公司"
TARGET_ORG_ID = ORG_MAP[TARGET_ORG]

# 物资关键词
MATERIAL_KW = [
    "物资", "设备", "材料", "电缆", "变压器", "开关柜",
    "组合电器", "GIS", "断路器", "互感器", "避雷器",
    "绝缘子", "电容器", "电抗器", "导线", "光缆",
    "铁塔", "钢管杆", "金具", "线夹", "箱式变电站",
    "环网柜", "配电终端", "电能表"
]

# 服务/工程关键词
SERVICE_KW = [
    "服务", "工程", "设计", "施工", "监理", "运维",
    "修理", "咨询", "劳务", "检修", "维护", "带电作业",
    "勘察", "测量"
]


# ============================================================
# Phase 1: 列表采集 + 分类
# ============================================================
def classify_notice(title: str) -> str:
    """基于标题分类"""
    title_lower = title
    mat_score = sum(1 for kw in MATERIAL_KW if kw in title_lower)
    svc_score = sum(1 for kw in SERVICE_KW if kw in title_lower)

    if mat_score > svc_score:
        return "material"
    elif svc_score > mat_score:
        if "工程" in title_lower and "服务" not in title_lower:
            return "engineering"
        return "service"
    return "other"


def extract_bid_batch(title: str) -> tuple:
    """提取招标批次和年份"""
    batch = ""
    year = None

    # 年份: 2020-2026
    m = re.search(r"(20\d{2})", title)
    if m:
        year = int(m.group(1))

    # 批次: 第X次
    m = re.search(r"第([一二三四五六七八九十\d]+)次", title)
    if m:
        batch = f"第{m.group(1)}次"

    return batch, year


def extract_subsidiary_hints(title: str, code: str) -> list[str]:
    """从标题和项目编号中提取可能的子公司线索"""
    hints = []
    # 项目编号前缀
    if code:
        # JB = 冀北, 后面跟的数字可能表示地市
        pass
    return hints


def crawl_metadata(conn, client: EcpClient):
    """Phase 1+2: 采集冀北全部公告元数据并分类存储"""
    print("\n" + "=" * 70)
    print("Phase 1+2: 采集公告元数据 + 关键词分类")
    print("=" * 70)

    # 获取全部公告
    result = client.query_all(
        org_id=TARGET_ORG_ID, page_size=50, max_pages=None
    )
    notices = result.notices
    print(f"获取到 {len(notices)} 条公告")

    # 分类统计
    cats = Counter()
    stored = 0
    material_notices = []

    cursor = conn.cursor()

    for n in notices:
        cat = classify_notice(n.title)
        batch, year = extract_bid_batch(n.title)
        cats[cat] += 1

        cursor.execute("""
            INSERT OR REPLACE INTO bid_notices
                (notice_id, title, code, publish_org_name, org_id,
                 notice_publish_time, notice_type, notice_type_name,
                 doctype, doc_id, doc_url, zbflag,
                 category, bid_batch, bid_year, fetched_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, datetime('now'))
        """, (
            n.notice_id, n.title, n.code, n.publish_org_name, n.org_id,
            n.notice_publish_time, n.notice_type, n.notice_type_name,
            n.doctype, n.first_page_doc_id, n.doc_url,
            0, cat, batch, year,
        ))
        stored += 1

        if cat == "material":
            material_notices.append(n)

    conn.commit()

    print(f"\n存储完成: {stored} 条")
    print(f"分类分布:")
    for cat, cnt in cats.most_common():
        pct = cnt / len(notices) * 100
        print(f"  {cat}: {cnt} 条 ({pct:.1f}%)")

    # 年+批次分布
    year_counts = Counter()
    for n in notices:
        _, year = extract_bid_batch(n.title)
        if year:
            year_counts[year] += 1
    print(f"\n年度分布:")
    for yr in sorted(year_counts):
        print(f"  {yr}: {year_counts[yr]} 条")

    # 物资公告按批次统计
    batch_counts = Counter()
    for n in material_notices:
        batch, _ = extract_bid_batch(n.title)
        if batch:
            batch_counts[batch] += 1
    print(f"\n物资公告批次分布 (前10):")
    for batch, cnt in batch_counts.most_common(10):
        print(f"  {batch}: {cnt} 条")

    return material_notices


# ============================================================
# Phase 3: 详情采集 (占位 — 需登录认证)
# ============================================================
def crawl_detail_stub(conn, material_notices: list):
    """
    Phase 3 占位函数

    此阶段需要登录ECP账号后才能执行。
    提供了三种实现路径的伪代码。

    从详情页可提取的字段：
    - material_name (物资名称)
    - material_desc (物资描述/规格)
    - demand_quantity (需求数量)
    - unit (计量单位)
    - package_no (包号)
    - sub_bid_name (分标名称)
    - project_org_name (项目单位/子公司)
    """
    print("\n" + "=" * 70)
    print("Phase 3: 详情页采集 (需登录认证)")
    print("=" * 70)

    print(f"""
    ⚠️  此阶段需要登录ECP账号后执行。

    三种实现方案：

    方案A: 手动登录 + 提取Cookie
    ─────────────────────────────
    1. 在浏览器中登录 https://ecp.sgcc.com.cn
    2. 打开开发者工具 → Application → Cookies
    3. 导出Cookie JSON
    4. 传入: python src/mvp_pipeline.py --cookies cookies.json

    方案B: Playwright 自动化登录
    ─────────────────────────────
    from playwright.sync_api import sync_playwright

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=False)
        page = browser.new_page()
        page.goto("https://ecp.sgcc.com.cn/ecp2.0/portal/")
        page.click("#login")  # 点击登录按钮
        page.fill("input[name=username]", "YOUR_USERNAME")
        page.fill("input[name=password]", "YOUR_PASSWORD")
        page.click("button[type=submit]")
        page.wait_for_url("**/portal/**")
        # 保存登录态
        context.storage_state(path="auth.json")

    方案C: Selenium + Chrome
    ─────────────────────────────
    使用已安装的 Chrome + Selenium WebDriver 自动化登录

    当前环境: Chrome 已安装 (C:/Program Files/Google/Chrome/Application/chrome.exe)

    详情API (已验证):
    POST /ecp2.0/ecpwcmcore//index/getNoticeBid
    返回: {{ resultValue: {{ notice: {{ ... }}, fileFlag: bool }} }}
    """)

    # 记录当前状态
    cursor = conn.cursor()
    cursor.execute("""
        INSERT INTO crawl_log (task_name, task_type, status, records_count, error_message)
        VALUES ('detail_crawl', 'detail', 'pending', ?, '需要登录认证')
    """, (len(material_notices),))
    conn.commit()

    print(f"当前有 {len(material_notices)} 条物资公告等待详情采集")
    print(f"详情API: POST /ecp2.0/ecpwcmcore//index/getNoticeBid")


# ============================================================
# Phase 4: 附件下载解析 (占位)
# ============================================================
def crawl_attachments_stub(conn, material_notices: list):
    """Phase 4 占位 — 下载并解析货物清单Excel"""
    print("\n" + "=" * 70)
    print("Phase 4: 附件下载与解析 (需登录)")
    print("=" * 70)

    print(f"""
    此阶段从公告详情页下载货物清单附件并解析。

    附件API (已验证):
    POST /ecp2.0/ecpwcmcore//index/downLoad
    POST /ecp2.0/ecpwcmcore//index/downLoadBid

    解析流程:
    1. 从 getNoticeBid 响应中获取附件列表
    2. 筛选"货物清单"类附件 (.xlsx/.zip)
    3. 下载附件到本地 data/attachments/
    4. 用 openpyxl 解析Excel:
       - Sheet1 通常包含: 物料编码/物资名称/规格/数量/单位/限价/包号/项目单位
    5. 写入 bid_items 表
    """)


# ============================================================
# 汇总报告
# ============================================================
def generate_summary(conn, material_notices: list):
    """生成数据采集汇总报告"""
    print("\n" + "=" * 70)
    print("MVP 数据采集汇总报告")
    print("=" * 70)

    cursor = conn.cursor()

    # 总统计
    cursor.execute("SELECT COUNT(*) FROM bid_notices")
    total = cursor.fetchone()[0]

    cursor.execute("SELECT category, COUNT(*) FROM bid_notices GROUP BY category")
    cats = {row[0]: row[1] for row in cursor}

    cursor.execute("SELECT COUNT(*) FROM bid_items")
    items = cursor.fetchone()[0]

    cursor.execute("SELECT COUNT(*) FROM org_units")
    orgs = cursor.fetchone()[0]

    print(f"""
    ┌──────────────────────────────────────────────────────┐
    │              数据库状态                                │
    ├──────────────────────────────────────────────────────┤
    │  公告总数:        {total:>5} 条                        │
    │    - 物资类:      {cats.get('material', 0):>5} 条      │
    │    - 服务类:      {cats.get('service', 0):>5} 条      │
    │    - 工程类:      {cats.get('engineering', 0):>5} 条   │
    │    - 其他:        {cats.get('other', 0):>5} 条        │
    │  物资明细:        {items:>5} 条 (需登录后填充)        │
    │  项目单位:        {orgs:>5} 个 (需登录后填充)         │
    ├──────────────────────────────────────────────────────┤
    │  Phase 1 (列表):  ✅ 已完成                          │
    │  Phase 2 (分类):  ✅ 已完成                          │
    │  Phase 3 (详情):  ⚠️ 需登录                          │
    │  Phase 4 (附件):  ⚠️ 需登录                          │
    └──────────────────────────────────────────────────────┘
    """)

    # 物资公告样例
    print("物资公告样例 (前5条):")
    cursor.execute("""
        SELECT title, notice_publish_time, bid_batch, bid_year
        FROM bid_notices
        WHERE category = 'material'
        ORDER BY notice_publish_time DESC
        LIMIT 5
    """)
    for row in cursor:
        print(f"  [{row[1]}] [{row[2]}] {row[0][:80]}")

    # 缺失字段评估
    print(f"""
    ╔══════════════════════════════════════════════════════╗
    ║  缺失字段填充评估                                     ║
    ╠══════════════════════════════════════════════════════╣
    ║  物资名称:     ⚠️ 列表API不可用                      ║
    ║               → getNoticeBid详情API (需登录)          ║
    ║               → 货物清单Excel附件 (需登录下载)        ║
    ║                                                      ║
    ║  物资描述/规格: ⚠️ 同上                               ║
    ║                                                      ║
    ║  需求数量:     ⚠️ 同上                               ║
    ║                                                      ║
    ║  计量单位:     ⚠️ 同上                               ║
    ║                                                      ║
    ║  包号:         ⚠️ 同上                               ║
    ║                                                      ║
    ║  子公司(唐山/承德):                                   ║
    ║               ⚠️ 项目单位信息在详情页中               ║
    ║               子公司清单 (从ECP orglist+推测):         ║
    ║               - 国网冀北电力有限公司唐山供电公司       ║
    ║               - 国网冀北电力有限公司承德供电公司       ║
    ║               - 国网冀北电力有限公司张家口供电公司     ║
    ║               - 国网冀北电力有限公司秦皇岛供电公司     ║
    ║               - 国网冀北电力有限公司廊坊供电公司       ║
    ║               - 国网冀北电力有限公司物资分公司         ║
    ║               - 北京送变电有限公司                     ║
    ╚══════════════════════════════════════════════════════╝
    """)

    # 登录指引
    print("""
    ╔══════════════════════════════════════════════════════╗
    ║  下一步: 如何完成Phase 3+4                            ║
    ╠══════════════════════════════════════════════════════╣
    ║                                                      ║
    ║  1. 在浏览器中登录 ECP 平台                           ║
    ║     https://ecp.sgcc.com.cn                           ║
    ║     (需国网供应商账号或CA证书)                         ║
    ║                                                      ║
    ║  2. 导出Cookie到 auth.json                           ║
    ║     F12 → Application → Cookies → Export             ║
    ║                                                      ║
    ║  3. 运行详情采集:                                     ║
    ║     python src/mvp_pipeline.py --phase 3             ║
    ║       --cookies auth.json                            ║
    ║                                                      ║
    ║  4. 运行附件下载解析:                                  ║
    ║     python src/mvp_pipeline.py --phase 4             ║
    ║       --cookies auth.json                            ║
    ║                                                      ║
    ╚══════════════════════════════════════════════════════╝
    """)


# ============================================================
# 主入口
# ============================================================
def main():
    print("ECP2.0 数据采集 MVP 流水线")
    print(f"目标: {TARGET_ORG} (orgId: {TARGET_ORG_ID})")
    print(f"开始时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")

    # 初始化数据库
    conn = init_db()
    conn.row_factory = None
    print("SQLite 数据库初始化完成")

    # 记录爬取开始
    conn.execute("""
        INSERT INTO crawl_log (task_name, task_type, status, records_count)
        VALUES ('mvp_pipeline', 'full', 'running', 0)
    """)
    conn.commit()

    # Phase 1+2
    client = EcpClient(timeout=30, retry=3)
    material_notices = crawl_metadata(conn, client)

    # Phase 3 (占位)
    crawl_detail_stub(conn, material_notices)

    # Phase 4 (占位)
    crawl_attachments_stub(conn, material_notices)

    # 更新爬取状态
    conn.execute("""
        UPDATE crawl_log SET status = 'completed',
        records_count = (SELECT COUNT(*) FROM bid_notices),
        completed_at = datetime('now')
        WHERE task_name = 'mvp_pipeline' AND status = 'running'
    """)
    conn.commit()

    # 生成报告
    conn.row_factory = None
    generate_summary(conn, material_notices)

    # 导出JSON样本
    export_sample(conn, material_notices)

    conn.close()
    print(f"\n完成时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print(f"数据库位置: {os.path.abspath(os.path.join(os.path.dirname(__file__), '..', 'data', 'ecp_data.db'))}")


def export_sample(conn, material_notices: list):
    """导出结构化的物资公告JSON样本"""
    cursor = conn.cursor()
    cursor.execute("""
        SELECT notice_id, title, code, notice_publish_time, bid_batch, bid_year, doc_url
        FROM bid_notices
        WHERE category = 'material'
        ORDER BY notice_publish_time DESC
    """)
    rows = cursor.fetchall()

    sample = []
    for row in rows:
        sample.append({
            "notice_id": row[0],
            "title": row[1],
            "code": row[2],
            "publish_time": row[3],
            "bid_batch": row[4],
            "bid_year": row[5],
            "doc_url": row[6],
            "status": "pending_detail",  # 待详情采集
        })

    out_path = os.path.join(
        os.path.dirname(__file__), "..", "data", "samples", "jibei_material_notices.json"
    )
    os.makedirs(os.path.dirname(out_path), exist_ok=True)
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(sample, f, ensure_ascii=False, indent=2)

    print(f"\n物资公告JSON导出: {out_path} ({len(sample)} 条)")


if __name__ == "__main__":
    main()
