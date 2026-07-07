"""
从ECP数据库提取批次事件特征，替代废弃的 fetch_external_factors.py。
所有因子相关度经 Spearman 实验验证（见 reports/影响因子可行性验证报告.md）。

提取因子:
  - transformer_bids: 当月输变电设备招标批次数 (|rho|=0.66)
  - monthly_bid_count: 当月物资正刊公告总数 (|rho|=0.63)
  - uhv_bids: 当月特高压批次数 (|rho|=0.35)
  - digital_bids: 当月数字化项目批次数 (|rho|=0.24, 辅助)
  - has_batch: 当月是否有招标批次 (|rho|=0.26, 辅助)

执行: python src/fetch_batch_features.py
"""
import sys, os, io, sqlite3, re
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')
import pandas as pd
from collections import defaultdict

PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
DB_PATH = os.path.join(PROJECT_ROOT, "data", "ecp_data.db")
OUTPUT_CSV = os.path.join(PROJECT_ROOT, "outputs", "批次事件特征.csv")


def all_months_range(start, end):
    months = []
    y, m = int(start[:4]), int(start[4:])
    y_e, m_e = int(end[:4]), int(end[4:])
    while (y < y_e) or (y == y_e and m <= m_e):
        months.append("{:04d}{:02d}".format(y, m))
        m += 1
        if m > 12: m = 1; y += 1
    return months


def extract_batch_features(db_path=DB_PATH):
    """从bid_notices表提取批次事件特征"""
    conn = sqlite3.connect(db_path)
    c = conn.cursor()

    c.execute("SELECT MIN(notice_publish_time), MAX(notice_publish_time) FROM bid_notices")
    min_pt, max_pt = c.fetchone()
    start_ym = min_pt[:4] + min_pt[5:7]
    end_ym = max_pt[:4] + max_pt[5:7]
    months = all_months_range(start_ym, end_ym)

    c.execute("""
        SELECT notice_id, title, notice_publish_time
        FROM bid_notices
        WHERE category='material' AND doctype='doci-bid'
    """)
    bids = c.fetchall()
    conn.close()

    # 按月份聚合
    monthly = defaultdict(lambda: {'transformer': 0, 'uhv': 0, 'digital': 0, 'meter': 0, 'power': 0, 'total': 0})

    for nid, title, pt in bids:
        ym = pt[:4] + pt[5:7]
        monthly[ym]['total'] += 1
        if any(k in title for k in ['输变电', '变电设备']):
            monthly[ym]['transformer'] += 1
        if '特高压' in title:
            monthly[ym]['uhv'] += 1
        if '数字化' in title:
            monthly[ym]['digital'] += 1
        if any(k in title for k in ['电能表', '计量设备']):
            monthly[ym]['meter'] += 1
        if '电源' in title:
            monthly[ym]['power'] += 1

    rows = []
    for ym in months:
        d = monthly.get(ym, defaultdict(int))
        rows.append({
            'month': ym,
            'transformer_bids': d['transformer'],
            'monthly_bid_count': d['total'],
            'uhv_bids': d['uhv'],
            'digital_bids': d['digital'],
            'meter_bids': d['meter'],
            'power_bids': d['power'],
            'has_batch': 1 if d['total'] > 0 else 0,
        })

    df = pd.DataFrame(rows)
    return df


def main():
    print("批次事件特征提取")
    print("数据来源: bid_notices 表 (ECP数据库)")
    print(f"数据库: {DB_PATH}")

    df = extract_batch_features()

    print(f"\n提取 {len(df)} 个月份的批次事件特征")
    print(f"列: {list(df.columns)}")

    # 统计
    print(f"\n月度统计:")
    print(f"  有批次的月份: {df['has_batch'].sum()}/{len(df)}")
    print(f"  输变电批次: {df['transformer_bids'].sum()} 批次")
    print(f"  特高压批次: {df['uhv_bids'].sum()} 批次")
    print(f"  数字化批次: {df['digital_bids'].sum()} 批次")

    os.makedirs(os.path.dirname(OUTPUT_CSV), exist_ok=True)
    df.to_csv(OUTPUT_CSV, index=False, encoding='utf-8-sig')
    print(f"\n输出: {OUTPUT_CSV}")

    return df


if __name__ == "__main__":
    main()
