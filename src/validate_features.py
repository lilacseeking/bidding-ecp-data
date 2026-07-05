"""
验证脚本: 物资特征画像相似 → 需求量相似 的假设是否成立
"""
import sqlite3, re, sys, os, io
import numpy as np
from collections import defaultdict

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')

DB_PATH = os.path.join(os.path.dirname(__file__), "..", "data", "ecp_data.db")


def parse_bid_item_features(name):
    """从 bid_items 的物资全名中提取结构化特征"""
    f = {}
    parts = [p.strip() for p in name.split(",")]

    # 电压等级
    for p in parts:
        m = re.search(r"(AC|DC)?(\d+)\s*kV", p)
        if m:
            f["voltage"] = int(m.group(2))
            break

    # 型号
    for p in parts:
        if p in ("YJV", "YJLV", "YJY", "YJLY", "VV"):
            f["model"] = p
            break

    # 截面 (独立的数字, 10~1000之间)
    for p in parts:
        m = re.search(r"^(\d{2,4})$", p.strip())
        if m:
            val = int(m.group(1))
            if 10 <= val <= 1000:
                f["cross_section"] = val
                break

    # 芯数
    for p in parts:
        m = re.search(r"(\d+)芯", p)
        if m:
            f["cores"] = int(m.group(1))
            break

    # 阻燃
    for fr in ("ZA", "ZB", "ZC", "ZR"):
        if fr in parts:
            f["flame_retardant"] = 1
            f["fr_level"] = fr
            break

    # 铠装22
    if "22" in parts:
        f["armor_22"] = 1

    # 无阻水/阻水
    for p in parts:
        if "阻水" in p:
            f["water_blocking"] = 1 if "无阻水" not in p else 0
            break

    # 铝芯 vs 铜芯
    if "铝" in name or "YJLV" in name or "YJLY" in name:
        f["conductor"] = "铝"
    elif "铜" in name or "YJV" in name or "YJY" in name:
        f["conductor"] = "铜"

    return f


def validate_1_within_category_pattern():
    """
    验证1: 在"电力电缆"这个简化类别内,
    不同规格(截面)在同一时段的需求是否呈现系统差异.
    """
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()

    c.execute("""
        SELECT material_name, demand_month, demand_quantity
        FROM bid_items
        WHERE material_name LIKE '%%电缆%%'
          AND demand_quantity IS NOT NULL
          AND demand_quantity > 0
        ORDER BY material_name, demand_month
    """)
    rows = c.fetchall()
    conn.close()

    # 按(截面, 型号, 电压)分组
    groups = defaultdict(list)
    for name, month, qty in rows:
        f = parse_bid_item_features(name)
        cross = f.get("cross_section", 0)
        voltage = f.get("voltage", 0)
        model = f.get("model", "")
        key = "V{}_M{}_C{}".format(voltage, model, cross)
        groups[key].append(qty)

    # 过滤只出现1次的
    groups = {k: v for k, v in groups.items() if len(v) >= 3}

    if len(groups) < 5:
        print("验证1: 组数不足,跳过")
        return

    group_data = [(k, np.mean(v), np.std(v), len(v)) for k, v in groups.items()]
    group_data.sort(key=lambda x: x[1], reverse=True)

    print("=" * 60)
    print("验证1: 同大类(电缆)内, 不同规格的需求系统差异")
    print("=" * 60)
    print("{:<30} {:>10} {:>10} {:>6}".format(
        "规格组(电压_型号_截面)", "均值", "标准差", "样本"))
    print("-" * 60)
    for key, mean_val, std_val, cnt in group_data[:15]:
        print("{:<30} {:>10.0f} {:>10.0f} {:>6}".format(
            key, mean_val, std_val, cnt))

    # 组间变异 vs 组内变异
    means = [d[1] for d in group_data]
    stds = [d[2] for d in group_data]
    between_var = np.std(means)
    within_var_mean = np.mean(stds)
    cv_between = between_var / (np.mean(means) + 1e-8)
    cv_within = within_var_mean / (np.mean(means) + 1e-8)

    print("\n组间需求离散度 CV_between = {:.3f}".format(cv_between))
    print("组内需求离散度 CV_within  = {:.3f}".format(cv_within))

    if cv_between > cv_within * 1.5:
        print(">>> 结论: 规格特征能有效区分需求量，组间差异 > 组内差异 <<<")
    elif cv_between > cv_within:
        print(">>> 结论: 有一定区分度，方向可行 <<<")
    else:
        print(">>> 结论: 当前特征拆分粒度下区分度不足，需要更多维度 <<<")

    # 关键验证: 同一截面下，不同月份的需求是否稳定
    print("\n--- 关键: 同规格在同年同月的需求量一致性 ---")
    # 选 TOP 规格
    top_groups = group_data[:3]
    for key, _, _, _ in top_groups:
        v_str, m_str, c_str = key.split("_")
        voltage = v_str[1:] if v_str[1:] else "?"
        model = m_str[1:] if m_str[1:] else "?"
        cross = c_str[1:] if c_str[1:] else "?"

        # 重新查询该规格每月需求
        conn2 = sqlite3.connect(DB_PATH)
        c2 = conn2.cursor()
        c2.execute("""
            SELECT demand_month, SUM(demand_quantity), COUNT(DISTINCT material_name)
            FROM bid_items
            WHERE demand_quantity IS NOT NULL
            GROUP BY demand_month
            ORDER BY demand_month
        """)
        monthly_totals = {}
        for row in c2:
            monthly_totals[row[0]] = row[1]

        # 该规格各月需求
        pattern = "%{}%{}%{}%".format(voltage, model, cross)
        c2.execute("""
            SELECT demand_month, SUM(demand_quantity)
            FROM bid_items
            WHERE material_name LIKE ?
              AND demand_quantity IS NOT NULL
            GROUP BY demand_month
            ORDER BY demand_month
        """, (pattern,))
        detail_rows = c2.fetchall()
        conn2.close()

        if len(detail_rows) >= 3:
            qty_values = [r[1] for r in detail_rows]
            # 看该规格占总需求的比例是否稳定
            ratios = []
            for r in detail_rows:
                month_total = monthly_totals.get(r[0], 1)
                ratios.append(r[1] / month_total if month_total > 0 else 0)
            print("  规格 [V{}, M{}, C{}]: {}/月, 占总比CV={:.3f}".format(
                voltage, model, cross, len(detail_rows),
                np.std(ratios) / (np.mean(ratios) + 1e-8)))


def validate_2_correlation_within_category():
    """
    验证2: 同一大类物资，不同简化品种在时间维度上是否同涨同跌
    取几组高频简化物资，检查它们在共有月份的需求量 Spearman 相关性
    """
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()

    # 找高频物资(>=8个月)的月需求序列
    c.execute("""
        SELECT material_name, COUNT(DISTINCT demand_month) as mcnt
        FROM material_demand_total
        GROUP BY material_name
        HAVING mcnt >= 8
        ORDER BY mcnt DESC
    """)
    high_freq = [r[0] for r in c.fetchall()]

    if len(high_freq) < 5:
        print("\n验证2: 高频物资不足,跳过")
        conn.close()
        return

    # 取TOP 20高频物资的月度需求矩阵
    top_20 = high_freq[:20]
    placeholders = ",".join(["?"] * len(top_20))
    c.execute("""
        SELECT material_name, demand_month, demand_quantity
        FROM material_demand_total
        WHERE material_name IN ({})
        ORDER BY material_name, demand_month
    """.format(placeholders), top_20)
    rows = c.fetchall()
    conn.close()

    # 构建20×51矩阵
    all_months = sorted(set(r[1] for r in rows))
    mat_dict = defaultdict(lambda: [np.nan] * len(all_months))
    month_idx = {m: i for i, m in enumerate(all_months)}
    for name, month, qty in rows:
        mat_dict[name][month_idx[month]] = qty

    mat_names = list(mat_dict.keys())
    n = len(mat_names)
    matrix = np.array([mat_dict[nm] for nm in mat_names])

    # 计算成对相关性 (只在共有的非NaN月份上)
    print("\n" + "=" * 60)
    print("验证2: 高频物资(>=8月)的需求时间序列两两相关性")
    print("=" * 60)

    same_cat_corrs = []
    diff_cat_corrs = []

    for i in range(n):
        for j in range(i + 1, n):
            vi = matrix[i]
            vj = matrix[j]
            mask = ~np.isnan(vi) & ~np.isnan(vj)
            if mask.sum() >= 5:
                corr = np.corrcoef(vi[mask], vj[mask])[0, 1]
                if np.isnan(corr):
                    continue
                # 判断是否同类
                cat_i = mat_names[i].split(",")[0]
                cat_j = mat_names[j].split(",")[0]
                if cat_i == cat_j:
                    same_cat_corrs.append(corr)
                else:
                    diff_cat_corrs.append(corr)

    if same_cat_corrs:
        print("同大类物资对相关性: mean={:.3f}, median={:.3f}, n={}".format(
            np.mean(same_cat_corrs), np.median(same_cat_corrs), len(same_cat_corrs)))

    if diff_cat_corrs:
        print("不同大类物资对相关性: mean={:.3f}, median={:.3f}, n={}".format(
            np.mean(diff_cat_corrs), np.median(diff_cat_corrs), len(diff_cat_corrs)))

    if same_cat_corrs and diff_cat_corrs:
        if np.mean(same_cat_corrs) > np.mean(diff_cat_corrs) * 1.5:
            print(">>> 结论: 同大类物资需求时间模式更一致，跨物资信息共享可行 <<<")
        else:
            print(">>> 结论: 大类维度不够，需要更细的特征拆分 <<<")

    # 打印几个具体的正相关例子
    all_pairs = []
    for i in range(n):
        for j in range(i + 1, n):
            vi = matrix[i]
            vj = matrix[j]
            mask = ~np.isnan(vi) & ~np.isnan(vj)
            if mask.sum() >= 6:
                corr = np.corrcoef(vi[mask], vj[mask])[0, 1]
                if not np.isnan(corr):
                    all_pairs.append((mat_names[i], mat_names[j], corr, mask.sum()))

    all_pairs.sort(key=lambda x: abs(x[2]), reverse=True)
    print("\nTOP5 最相关物资对:")
    for n1, n2, corr, cnt in all_pairs[:5]:
        print("  {} | {} : corr={:.3f} ({}个共同月份)".format(n1[:35], n2[:35], corr, cnt))


def validate_3_catboost_baseline():
    """
    验证3: 用CatBoost仅凭物资属性特征+时间特征做回归,
    检查在held-out时间上的预测能力。
    如果这个"不看历史序列"的模型就能拿到不错的R2,
    说明特征画像 → 需求量 的映射确实存在.
    """
    try:
        from catboost import CatBoostRegressor
    except ImportError:
        print("\n验证3: CatBoost未安装,跳过")
        return

    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()

    c.execute("""
        SELECT material_name, demand_month, demand_quantity
        FROM material_demand_total
        WHERE demand_quantity IS NOT NULL AND demand_quantity > 0
        ORDER BY material_name, demand_month
    """)
    rows = c.fetchall()
    conn.close()

    if len(rows) < 500:
        print("\n验证3: 样本不足,跳过")
        return

    # 特征工程
    X_list = []
    y_list = []
    for name, month, qty in rows:
        f = parse_bid_item_features(name)
        year = int(month[:4])
        m = int(month[4:])

        feat = [
            f.get("voltage", 0),
            f.get("cross_section", 0),
            f.get("cores", 0),
            1 if f.get("model") in ("YJV", "YJY") else (-1 if f.get("model") in ("YJLV", "YJLY") else 0),
            f.get("armor_22", 0),
            f.get("flame_retardant", 0),
            f.get("water_blocking", -1),
            year,
            m,
            np.sin(2 * np.pi * m / 12),
            np.cos(2 * np.pi * m / 12),
        ]
        X_list.append(feat)
        y_list.append(qty)

    X = np.array(X_list, dtype=np.float32)
    y = np.array(y_list, dtype=np.float32)

    # 时间切分: 2024年及以前训练, 2025年测试
    train_mask = np.array([row[7] <= 2024 for row in X_list])  # index 7 = year
    test_mask = np.array([row[7] >= 2025 for row in X_list])

    X_train, y_train = X[train_mask], y[train_mask]
    X_test, y_test = X[test_mask], y[test_mask]

    if len(X_test) < 20 or len(X_train) < 100:
        print("\n验证3: 训练/测试集太小,跳过")
        return

    # 基线: 用历史均值预测 (对于见过的物资)
    print("\n" + "=" * 60)
    print("验证3: CatBoost 特征模型 vs 历史均值基线")
    print("=" * 60)
    print("训练集: {} 条 (<=2024)".format(len(X_train)))
    print("测试集: {} 条 (>=2025)".format(len(X_test)))

    # 计算基线 MAPE (用训练集物资均值预测测试集)
    conn2 = sqlite3.connect(DB_PATH)
    c2 = conn2.cursor()
    baseline_preds = []
    baseline_actuals = []
    for i in range(len(X_test)):
        mat_name = rows[np.where(test_mask)[0][i]][0] if np.where(test_mask)[0][i] < len(rows) else None
    # 简化: 用全训练集均值作为朴素基线
    global_mean = np.mean(y_train)
    baseline_mape = np.mean(np.abs(y_test - global_mean) / (y_test + 1e-8)) * 100

    # CatBoost
    model = CatBoostRegressor(
        iterations=500, learning_rate=0.05, depth=6,
        verbose=False, random_seed=42,
    )
    model.fit(X_train, y_train)

    y_pred = model.predict(X_test)
    mape = np.mean(np.abs(y_test - y_pred) / (y_test + 1e-8)) * 100
    r2 = 1 - np.sum((y_test - y_pred) ** 2) / np.sum((y_test - np.mean(y_test)) ** 2)

    print("全局均值基线 MAPE: {:.1f}%".format(baseline_mape))
    print("CatBoost MAPE: {:.1f}%".format(mape))
    print("CatBoost R2: {:.3f}".format(r2))

    if mape < baseline_mape * 0.8:
        print(">>> 结论: 特征模型显著优于均值基线，特征→需求量的映射存在且可学习 <<<")
    elif mape < baseline_mape:
        print(">>> 结论: 特征模型略优于基线，需加强特征工程 <<<")
    else:
        print(">>> 结论: 纯特征模型无法击败均值基线，需要引入时序特征(VMD分量/历史值) <<<")

    # 特征重要性
    feat_names = [
        "电压", "截面", "芯数", "型号(铜/铝)", "铠装22",
        "阻燃", "阻水", "年份", "月份", "sin_m", "cos_m"
    ]
    importances = model.get_feature_importance()
    print("\n特征重要性:")
    for name, imp in sorted(zip(feat_names, importances), key=lambda x: x[1], reverse=True):
        print("  {}: {:.3f}".format(name, imp))


if __name__ == "__main__":
    print("物资特征画像 → 需求量相似性 验证脚本")
    print("=" * 60)
    validate_1_within_category_pattern()
    validate_2_correlation_within_category()
    validate_3_catboost_baseline()
    print("\n验证完成")
