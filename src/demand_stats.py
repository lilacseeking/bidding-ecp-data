"""
物资需求统计 + Top5物资时间序列绘图

1. 从 bid_items 聚合 → material_demand_stats (物资名+单位+月份 → 需求量求和)
2. 找月份分布最多的 Top5 物资
3. 绘制 5 个子图上下排列的时间序列

执行: python src/demand_stats.py
"""
import sys, os, io
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')

import sqlite3
from collections import Counter
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
import matplotlib.ticker as ticker
from datetime import datetime

from db.schema import init_db

# stdout encoding: 独立运行时需要, 被pipeline调用时pipeline已设置
if 'pipeline' not in sys.modules:
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')

DB_PATH = os.path.join(os.path.dirname(__file__), "..", "data", "ecp_data.db")
OUTPUT_DIR = os.path.join(os.path.dirname(__file__), "..", "outputs", "figures")

# 中文字体 - 使用font_manager显式加载避免权限错误
import matplotlib.font_manager as fm
for fname in ['Microsoft YaHei', 'SimHei', 'KaiTi']:
    try:
        font_path = fm.findfont(fm.FontProperties(family=fname), fallback_to_default=False)
        if font_path:
            plt.rcParams['font.sans-serif'] = [fname] + plt.rcParams['font.sans-serif']
            break
    except Exception:
        continue
plt.rcParams['axes.unicode_minus'] = False
# 清除字体缓存避免PermissionError
fm._load_fontmanager(try_read_cache=False)


def short_name(raw: str) -> str:
    """提取物资简称: 逗号前的部分"""
    return raw.split(',')[0].split('(')[0].strip()


def build_demand_stats(conn):
    """从 bid_items 聚合生成 material_demand_stats"""
    c = conn.cursor()

    # 清理旧数据
    c.execute("DELETE FROM material_demand_stats")

    # 聚合: 物资简称 + 单位 + 月份(YYYYMM) → SUM(需求量)
    c.execute("""
        INSERT OR REPLACE INTO material_demand_stats
            (material_name, unit, demand_month, demand_quantity, notice_count)
        SELECT
            CASE WHEN i.material_name LIKE '%,%'
                 THEN substr(i.material_name, 1, instr(i.material_name, ',') - 1)
                 ELSE i.material_name END,
            i.unit,
            substr(n.notice_publish_time, 1, 4) || substr(n.notice_publish_time, 6, 2),
            SUM(i.demand_quantity),
            COUNT(DISTINCT i.notice_id)
        FROM bid_items i
        JOIN bid_notices n ON i.notice_id = n.notice_id
        WHERE i.demand_quantity IS NOT NULL
          AND i.unit IS NOT NULL
          AND i.material_name IS NOT NULL
        GROUP BY 1, 2, 3
    """)
    conn.commit()

    c.execute("SELECT COUNT(*) FROM material_demand_stats")
    print(f"material_demand_stats: {c.fetchone()[0]} 条记录")


def get_top5_materials(conn) -> list[tuple]:
    """找出月份数最多的 Top5 物资"""
    c = conn.cursor()
    c.execute("""
        SELECT material_name, unit, COUNT(DISTINCT demand_month) as month_cnt,
               SUM(demand_quantity) as total_qty
        FROM material_demand_stats
        GROUP BY material_name, unit
        ORDER BY month_cnt DESC
        LIMIT 5
    """)
    return [(r[0], r[1], r[2], r[3]) for r in c]


def plot_top5(conn, top5: list[tuple]):
    """绘制Top5物资需求量时间序列，5个子图上下排列"""
    c = conn.cursor()
    os.makedirs(OUTPUT_DIR, exist_ok=True)

    fig, axes = plt.subplots(5, 1, figsize=(18, 22), sharex=False)
    fig.suptitle('国网冀北电力 — 物资需求数量月度变化 (Top5 月份覆盖最多)',
                 fontsize=16, fontweight='bold', y=0.99)

    colors = ['#2196F3', '#FF5722', '#4CAF50', '#FF9800', '#9C27B0']

    for idx, (mat_name, unit, month_cnt, total_qty) in enumerate(top5):
        ax = axes[idx]

        # 查询该物资的月度数据
        c.execute("""
            SELECT demand_month, demand_quantity
            FROM material_demand_stats
            WHERE material_name = ? AND unit = ?
            ORDER BY demand_month
        """, (mat_name, unit))
        rows = c.fetchall()

        months = [r[0] for r in rows]
        quantities = [r[1] for r in rows]

        # 格式化为可读标签 (每隔4个标注)
        month_labels = []
        for i, m in enumerate(months):
            if i % max(1, len(months) // 10) == 0:
                month_labels.append(f"{m[:4]}-{m[4:]}")
            else:
                month_labels.append('')

        ax.fill_between(range(len(months)), quantities, alpha=0.3, color=colors[idx])
        ax.plot(range(len(months)), quantities, 'o-', color=colors[idx],
                linewidth=2, markersize=4, markerfacecolor='white')

        # 标注峰值
        if quantities:
            max_idx = quantities.index(max(quantities))
            ax.annotate(f'{quantities[max_idx]:,.0f}',
                       xy=(max_idx, quantities[max_idx]),
                       xytext=(0, 10), textcoords='offset points',
                       fontsize=9, ha='center', color=colors[idx],
                       fontweight='bold')

        ax.set_title(f'{mat_name} ({unit})  |  覆盖 {month_cnt} 个月, 累计 {total_qty:,.0f} {unit}',
                    fontsize=13, loc='left')
        ax.set_ylabel(f'需求量 ({unit})', fontsize=11)
        ax.grid(axis='y', alpha=0.4, linestyle='--')
        ax.set_xlim(-0.5, len(months) - 0.5)

        # X轴标签
        tick_positions = list(range(len(months)))
        tick_labels = [f"{m[:4]}-{m[4:]}" if i % max(1, len(months)//8) == 0 else ''
                       for i, m in enumerate(months)]
        ax.set_xticks(tick_positions)
        ax.set_xticklabels(tick_labels, fontsize=8, rotation=30, ha='right')

    # X轴标签
    axes[-1].set_xlabel('时间 (月)', fontsize=12)

    plt.tight_layout(rect=[0, 0, 1, 0.98])
    output_path = os.path.join(OUTPUT_DIR, 'material_demand_top5_monthly.png')
    fig.savefig(output_path, dpi=150, bbox_inches='tight', facecolor='white')
    plt.close()
    print(f'\n图表已保存: {output_path}')

    return output_path


def main():
    print("物资需求统计 + Top5 绘图")
    print(f"时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")

    init_db(DB_PATH)
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row

    # Step 1: 聚合
    build_demand_stats(conn)

    # Step 2: Top5
    top5 = get_top5_materials(conn)
    print(f"\n月份覆盖最多的 Top5 物资:")
    for mat, unit, mc, total in top5:
        print(f"  {mat[:50]}: {mc} 个月, {total:,.0f} {unit}")

    # Step 3: 绘图
    if top5:
        plot_top5(conn, top5)

    # Step 4: 导出统计CSV
    csv_path = os.path.join(os.path.dirname(__file__), "..", "outputs",
                            "material_demand_stats.csv")
    os.makedirs(os.path.dirname(csv_path), exist_ok=True)
    c = conn.cursor()
    c.execute("""
        SELECT material_name, unit, demand_month, demand_quantity, notice_count
        FROM material_demand_stats ORDER BY demand_month, material_name
    """)
    import csv
    with open(csv_path, 'w', encoding='utf-8-sig', newline='') as f:
        w = csv.writer(f)
        w.writerow(['material_name', 'unit', 'demand_month', 'demand_quantity', 'notice_count'])
        w.writerows(c)
    print(f"\nCSV导出: {csv_path} ({c.execute('SELECT COUNT(*) FROM material_demand_stats').fetchone()[0]} 行)")

    conn.close()
    print(f"\n完成: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")


if __name__ == "__main__":
    main()
