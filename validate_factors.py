"""
影响因子可行性验证 Demo
1. 计算当前7个因子与3种目标物资的Spearman相关系数
2. 从ECP数据库提取批次事件特征并验证相关性
3. 尝试获取电网投资、距春节天数等新因子并验证
4. 标记低相关因子(建议删除)，标记高相关因子(建议保留)
"""
import sys, os, io, json, sqlite3
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')
import numpy as np
import pandas as pd
from scipy.stats import spearmanr, pearsonr
from datetime import datetime, timedelta
import lunardate

# ---- 配置 ----
BIDDING_ROOT = os.path.dirname(os.path.abspath(__file__))
DB_PATH = os.path.join(BIDDING_ROOT, "data", "ecp_data.db")
DATA_XLSX = r"C:\Users\董文涛\PycharmProjects\vmd-catboost\inputs\data\data.xlsx"

TARGET_MATERIALS = ['交流避雷器', '电容式电压互感器', '交流支柱绝缘子']

print("=" * 70)
print("影响因子可行性验证 Demo")
print("=" * 70)

# ============================================================
# Part 1: 加载现有数据，计算当前7因子 Spearman 相关
# ============================================================
print("\n" + "=" * 70)
print("Part 1: 当前7因子 Spearman 相关性分析")
print("=" * 70)

xls = pd.ExcelFile(DATA_XLSX)
all_data = {}
for sheet in xls.sheet_names:
    df = pd.read_excel(DATA_XLSX, sheet_name=sheet)
    df['date'] = pd.to_datetime(df['日期'])
    all_data[sheet] = df

# 因子列映射
FACTOR_COLS = {
    '项目数量': '项目数量',
    '铜期货月均价(元/吨)': '铜期货月均价(元/吨)',
    '铝期货月均价(元/吨)': '铝期货月均价(元/吨)',
    '第二产业用电量(亿kWh)': '第二产业用电量(亿kWh)',
    '工业用电同比增速(%)': '工业用电同比增速(%)',
    '农历月_sin': '农历月_sin',
    '农历月_cos': '农历月_cos',
}

results = []
for material in TARGET_MATERIALS:
    df = all_data[material]
    demand = df['需求量'].values
    for fname, fcol in FACTOR_COLS.items():
        if fcol in df.columns:
            vals = df[fcol].values
            valid = ~(np.isnan(vals) | np.isnan(demand))
            if valid.sum() > 10:
                rho, pval = spearmanr(vals[valid], demand[valid])
                r_pearson, p_pearson = pearsonr(vals[valid], demand[valid])
                results.append({
                    'material': material, 'factor': fname,
                    'spearman_rho': round(rho, 4), 'spearman_p': round(pval, 4),
                    'pearson_r': round(r_pearson, 4), 'pearson_p': round(p_pearson, 4),
                })

# 打印各因子的跨物资平均相关度
print(f"\n{'因子':<25} {'Spearman ρ(avg)':>15} {'判定':>10}")
print("-" * 55)
factor_avg = {}
for fname in FACTOR_COLS:
    rows = [r for r in results if r['factor'] == fname]
    avg_rho = np.mean([abs(r['spearman_rho']) for r in rows])
    factor_avg[fname] = avg_rho
    if avg_rho >= 0.3:
        verdict = "✅ 保留"
    elif avg_rho >= 0.1:
        verdict = "⚠️ 待论证"
    else:
        verdict = "❌ 建议删除"
    print(f"{fname:<25} {avg_rho:>15.4f} {verdict:>10}")

# 分物资明细
print(f"\n分物资明细:")
print(f"{'物资':<20} {'因子':<25} {'Spearman ρ':>12} {'p值':>10} {'Pearson r':>12}")
print("-" * 80)
for r in results:
    print(f"{r['material']:<20} {r['factor']:<25} {r['spearman_rho']:>12.4f} {r['spearman_p']:>10.4f} {r['pearson_r']:>12.4f}")

# ============================================================
# Part 2: 从ECP数据库提取批次事件特征
# ============================================================
print("\n" + "=" * 70)
print("Part 2: 批次事件特征提取与验证")
print("=" * 70)

conn = sqlite3.connect(DB_PATH)
c = conn.cursor()

# 获取完整月份范围
c.execute("SELECT MIN(demand_month), MAX(demand_month) FROM material_demand_total")
min_m, max_m = c.fetchone()
all_ms = []
y, m = int(min_m[:4]), int(min_m[4:])
ye, me = int(max_m[:4]), int(max_m[4:])
while (y < ye) or (y == ye and m <= me):
    all_ms.append("{:04d}{:02d}".format(y, m))
    m += 1
    if m > 12: m = 1; y += 1

# 1. 每月公告数量
c.execute("""
    SELECT substr(notice_publish_time,1,4)||substr(notice_publish_time,6,2) as ym,
           COUNT(*) as cnt
    FROM bid_notices
    WHERE category='material' AND doctype='doci-bid'
    GROUP BY ym ORDER BY ym
""")
monthly_bid_count = {r[0]: r[1] for r in c.fetchall()}

# 2. 每月项目数量（该月公告中涉及的项目单位数）
c.execute("""
    SELECT i.demand_month, COUNT(DISTINCT i.notice_id) as notice_cnt,
           COUNT(DISTINCT i.project_org_name) as org_cnt
    FROM bid_items i
    WHERE i.demand_month IS NOT NULL
    GROUP BY i.demand_month ORDER BY i.demand_month
""")
monthly_detail = {r[0]: (r[1], r[2]) for r in c.fetchall()}

# 3. 从公告标题提取批次信息
c.execute("""
    SELECT notice_id, title, notice_publish_time
    FROM bid_notices
    WHERE category='material' AND doctype='doci-bid'
""")
batch_info = []
import re
for nid, title, pt in c.fetchall():
    ym = pt[:4] + pt[5:7]
    # 提取第X次
    m = re.search(r'第([一二三四五六七八九十\d]+)次', title)
    batch_num = m.group(1) if m else None
    # 判断公告类型
    is_transformer = any(k in title for k in ['输变电', '变电设备'])
    is_uhv = '特高压' in title
    is_digital = '数字化' in title
    is_meter = any(k in title for k in ['电能表', '计量设备'])
    is_power = '电源' in title
    batch_info.append({'ym': ym, 'nid': nid, 'title': title,
                       'batch_num': batch_num,
                       'is_transformer': is_transformer, 'is_uhv': is_uhv,
                       'is_digital': is_digital, 'is_meter': is_meter,
                       'is_power': is_power})
conn.close()

# 按月份聚合批次事件
monthly_events = {}
for ym in all_ms:
    month_bids = [b for b in batch_info if b['ym'] == ym]
    monthly_events[ym] = {
        'total_bids': len(month_bids),
        'transformer_bids': sum(1 for b in month_bids if b['is_transformer']),
        'uhv_bids': sum(1 for b in month_bids if b['is_uhv']),
        'digital_bids': sum(1 for b in month_bids if b['is_digital']),
        'meter_bids': sum(1 for b in month_bids if b['is_meter']),
        'power_bids': sum(1 for b in month_bids if b['is_power']),
    }

# ============================================================
# Part 3: 计算距春节天数特征
# ============================================================
def get_spring_festival_date(year):
    """返回该年春节的公历日期（查表法）"""
    sf_dates = {
        2019: (2, 5), 2020: (1, 25), 2021: (2, 12), 2022: (2, 1),
        2023: (1, 22), 2024: (2, 10), 2025: (1, 29), 2026: (2, 17),
        2027: (2, 6),
    }
    return sf_dates.get(year, (2, 1))

spring_distances = []
for ym in all_ms:
    y = int(ym[:4])
    m = int(ym[4:])
    month_mid = datetime(y, m, 15)
    sf = get_spring_festival_date(y)
    sf_date = datetime(y, sf[0], sf[1])
    # 距春节天数（负=春节前，正=春节后）
    days_to_sf = (month_mid - sf_date).days
    spring_distances.append({'ym': ym, 'days_to_sf': days_to_sf,
                             'is_sf_month': abs(days_to_sf) <= 15,
                             'sf_year': y})

# ============================================================
# Part 4: 合并所有特征，计算与需求的相关系数
# ============================================================
print("\n" + "=" * 70)
print("Part 3: 全部候选因子 vs 物资需求 Spearman 相关性")
print("=" * 70)

# 为每个月构建特征向量
monthly_features = {}
for i, ym in enumerate(all_ms):
    feat = {}
    # 批次事件特征
    ev = monthly_events.get(ym, {})
    feat['monthly_bid_count'] = ev.get('total_bids', 0)
    feat['transformer_bids'] = ev.get('transformer_bids', 0)
    feat['uhv_bids'] = ev.get('uhv_bids', 0)
    feat['digital_bids'] = ev.get('digital_bids', 0)
    feat['meter_bids'] = ev.get('meter_bids', 0)
    feat['power_bids'] = ev.get('power_bids', 0)
    feat['has_batch'] = 1 if ev.get('total_bids', 0) > 0 else 0

    # 距春节天数
    sd = spring_distances[i]
    feat['days_to_sf'] = abs(sd['days_to_sf'])
    feat['is_sf_month'] = 1 if sd['is_sf_month'] else 0

    # 公历月份编码
    m_num = int(ym[4:])
    feat['month_sin'] = round(np.sin(2 * np.pi * m_num / 12), 6)
    feat['month_cos'] = round(np.cos(2 * np.pi * m_num / 12), 6)

    # 农历月份编码
    try:
        d = datetime(int(ym[:4]), int(ym[4:]), 15)
        lm = lunardate.LunarDate.fromSolarDate(d.year, d.month, d.day).month
    except:
        lm = m_num
    feat['lunar_month_sin'] = round(np.sin(2 * np.pi * lm / 12), 6)
    feat['lunar_month_cos'] = round(np.cos(2 * np.pi * lm / 12), 6)

    monthly_features[ym] = feat

# 读取外部因子
df_first = all_data[TARGET_MATERIALS[0]]
ext_factors = {}
for i, row in df_first.iterrows():
    d = pd.Timestamp(row['日期'])
    ym = d.strftime('%Y%m')
    ext_factors[ym] = {
        'project_count': row.get('项目数量', np.nan),
        'copper_price': row.get('铜期货月均价(元/吨)', np.nan),
        'aluminum_price': row.get('铝期货月均价(元/吨)', np.nan),
        'industrial_elec': row.get('第二产业用电量(亿kWh)', np.nan),
        'industrial_elec_yoy': row.get('工业用电同比增速(%)', np.nan),
    }

# 合并所有特征
ALL_FEATURES = {}
for ym in all_ms:
    f = dict(monthly_features.get(ym, {}))
    ef = ext_factors.get(ym, {})
    f.update(ef)
    # 添加lag12（去年同期需求量）- 对每种物资分别算
    ALL_FEATURES[ym] = f

# 对每种物资计算相关性
print(f"\n{'因子':<30}", end='')
for mat in TARGET_MATERIALS:
    print(f"{mat[:8]:>12}", end='')
print(f" {'|ρ|avg':>10} {'判定':>10}")
print("-" * 110)

corr_results = {}
for fname in ALL_FEATURES[all_ms[0]].keys():
    rhos = []
    for mat in TARGET_MATERIALS:
        df = all_data[mat]
        demand_dict = {}
        for i, row in df.iterrows():
            d = pd.Timestamp(row['日期'])
            ym = d.strftime('%Y%m')
            demand_dict[ym] = row['需求量']

        fvals = []
        dvals = []
        for ym in all_ms:
            if ym in ALL_FEATURES and ym in demand_dict and fname in ALL_FEATURES[ym]:
                fv = ALL_FEATURES[ym][fname]
                dv = demand_dict[ym]
                if not (np.isnan(fv) or np.isnan(dv)):
                    fvals.append(fv)
                    dvals.append(dv)
        if len(fvals) > 10:
            rho, pval = spearmanr(fvals, dvals)
            rhos.append(abs(rho))
        else:
            rhos.append(0)

    avg_rho = np.mean(rhos) if rhos else 0
    corr_results[fname] = {'rhos': rhos, 'avg': avg_rho}

# 排序打印
for fname, cr in sorted(corr_results.items(), key=lambda x: x[1]['avg'], reverse=True):
    if cr['avg'] >= 0.3:
        verdict = "✅ 高相关"
    elif cr['avg'] >= 0.1:
        verdict = "⚠️ 弱相关"
    else:
        verdict = "❌ 不相关"
    print(f"{fname:<30}", end='')
    for r in cr['rhos']:
        print(f"{r:>12.4f}", end='')
    print(f" {cr['avg']:>10.4f} {verdict:>10}")

# ============================================================
# Part 5: 结论与建议
# ============================================================
print("\n" + "=" * 70)
print("Part 4: 因子筛选结论")
print("=" * 70)

print("\n### ❌ 建议删除的因子 (|ρ| < 0.1):")
for fname, cr in sorted(corr_results.items(), key=lambda x: x[1]['avg']):
    if cr['avg'] < 0.1:
        print(f"  - {fname}: |ρ|avg={cr['avg']:.4f}")

print("\n### ⚠️ 待论证的因子 (0.1 ≤ |ρ| < 0.3):")
for fname, cr in sorted(corr_results.items(), key=lambda x: x[1]['avg']):
    if 0.1 <= cr['avg'] < 0.3:
        print(f"  - {fname}: |ρ|avg={cr['avg']:.4f}")

print("\n### ✅ 保留的高相关因子 (|ρ| ≥ 0.3):")
for fname, cr in sorted(corr_results.items(), key=lambda x: x[1]['avg'], reverse=True):
    if cr['avg'] >= 0.3:
        print(f"  - {fname}: |ρ|avg={cr['avg']:.4f}")

print("\n### 铜铝期货专门论证:")
cu_avg = corr_results.get('copper_price', {}).get('avg', 0)
al_avg = corr_results.get('aluminum_price', {}).get('avg', 0)
print(f"  铜期货月均价 |ρ|avg = {cu_avg:.4f}")
print(f"  铝期货月均价 |ρ|avg = {al_avg:.4f}")
if cu_avg < 0.2 and al_avg < 0.2:
    print("  结论: 铜铝期货价格与物资需求量的线性相关度极低。")
    print("  业务解释: 国网采用长协价格+框架协议，采购价格不随期货市场短期波动。")
    print("  期货月均价应在论文中作为辅助讨论（如'Granger因果检验表明无显著预测力'），不作为预测因子。")
    print("  建议: ✅ 删除 copper_price 和 aluminum_price 两个因子。")

print("\n完成!")
