"""
从ECP数据库提取真实采购数据，生成 vmd-catboost 模型输入文件。

输出: vmd-catboost/inputs/data/data.xlsx
- 3个Sheet: 交流避雷器 / 电容式电压互感器 / 交流支柱绝缘子
- 列: 日期, 需求量, 项目数量, 铜期货月均价, 铝期货月均价,
       第二产业用电量, 工业用电同比增速, 农历月_sin, 农历月_cos
"""
import os, sqlite3, numpy as np
import lunardate

# ---- 配置 ----
BIDDING_ROOT = os.path.dirname(os.path.abspath(__file__))
DB_PATH = os.path.join(BIDDING_ROOT, "data", "ecp_data.db")
VMD_DATA_DIR = r"C:\Users\董文涛\PycharmProjects\vmd-catboost\inputs\data"
OUTPUT = os.path.join(VMD_DATA_DIR, "data.xlsx")

TARGET_MATERIALS = ["交流避雷器", "电容式电压互感器", "交流支柱绝缘子"]

START = "201911"
END = "202607"


def all_months(start, end):
    months = []
    y, m = int(start[:4]), int(start[4:])
    y_e, m_e = int(end[:4]), int(end[4:])
    while (y < y_e) or (y == y_e and m <= m_e):
        months.append("{:04d}{:02d}".format(y, m))
        m += 1
        if m > 12:
            m = 1; y += 1
    return months


def get_lunar_month(year, month):
    """取每月15日对应的农历月份"""
    import datetime
    d = datetime.date(year, month, 15)
    try:
        lunar = lunardate.LunarDate.fromSolarDate(d.year, d.month, d.day)
        return lunar.month  # 1-12
    except Exception:
        # fallback: 春节通常在1-2月，近似估计
        if month == 1:
            return 12  # 约在腊月
        elif month == 2:
            return 1   # 约在正月
        else:
            return month - 1  # 粗略偏移


def main():
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()

    # 1. 读取 material_demand_total
    c.execute("""
        SELECT material_name, demand_month,
               SUM(demand_quantity) as total_qty,
               MAX(notice_count) as notice_cnt
        FROM material_demand_total
        WHERE material_name IN ({})
        GROUP BY material_name, demand_month
        ORDER BY material_name, demand_month
    """.format(",".join(["?"]*len(TARGET_MATERIALS))), TARGET_MATERIALS)

    rows = c.fetchall()

    # 2. 读取 external_factors
    c.execute("SELECT * FROM external_factors ORDER BY month")
    ext_cols = [d[0] for d in c.description]
    ext_rows = c.fetchall()
    conn.close()

    ext = {}
    for row in ext_rows:
        d = dict(zip(ext_cols, row))
        ext[d["month"]] = d

    # 3. 构建完整月份列表
    months = all_months(START, END)
    print(f"数据范围: {START} ~ {END}, 共 {len(months)} 个月")

    # 4. 按物资组织数据
    mat_data = {mat: {} for mat in TARGET_MATERIALS}
    for (name, dm, qty, ncnt) in rows:
        if name in mat_data:
            mat_data[name][dm] = (qty or 0, ncnt or 0)

    # 5. 生成 Excel
    import openpyxl
    wb = openpyxl.Workbook()
    # 删除默认Sheet (重新编排顺序)
    wb.remove(wb.active)

    for mat in TARGET_MATERIALS:
        ws = wb.create_sheet(mat)
        headers = [
            "日期", "需求量", "项目数量",
            "铜期货月均价(元/吨)", "铝期货月均价(元/吨)",
            "第二产业用电量(亿kWh)", "工业用电同比增速(%)",
            "农历月_sin", "农历月_cos",
        ]
        for ci, h in enumerate(headers, 1):
            ws.cell(row=1, column=ci, value=h)

        zero_months = 0
        total_months = len(months)
        for ri, dm in enumerate(months, 2):
            qty, ncnt = mat_data[mat].get(dm, (0, 0))
            if qty == 0:
                zero_months += 1

            ef = ext.get(dm, {})
            y, m = int(dm[:4]), int(dm[4:])

            # 农历月份编码
            lm = get_lunar_month(y, m)
            lm_sin = round(np.sin(2 * np.pi * lm / 12), 6)
            lm_cos = round(np.cos(2 * np.pi * lm / 12), 6)

            ws.cell(row=ri, column=1, value=f"{y}-{m:02d}-01")
            ws.cell(row=ri, column=2, value=qty)
            ws.cell(row=ri, column=3, value=ncnt)
            ws.cell(row=ri, column=4, value=ef.get("copper_price"))
            ws.cell(row=ri, column=5, value=ef.get("aluminum_price"))
            ws.cell(row=ri, column=6, value=ef.get("industrial_elec"))
            ws.cell(row=ri, column=7, value=ef.get("industrial_elec_yoy"))
            ws.cell(row=ri, column=8, value=lm_sin)
            ws.cell(row=ri, column=9, value=lm_cos)

        print(f"  [{mat}] {total_months}月, 零值月={zero_months}, "
              f"非零月={total_months - zero_months}")

    os.makedirs(VMD_DATA_DIR, exist_ok=True)
    wb.save(OUTPUT)
    print(f"\n输出: {OUTPUT}")


if __name__ == "__main__":
    main()
