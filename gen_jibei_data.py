"""Generate Jibei company data.xlsx for vmd-catboost."""
import sqlite3, os, sys, openpyxl
from collections import defaultdict

DB = r'C:\Users\董文涛\PycharmProjects\bidding-ecp-data\data_jibei\ecp_data.db'
OUT = r'C:\Users\董文涛\PycharmProjects\vmd-catboost\inputs\data_jibei.xlsx'
JIBEI = '2019061900137008'

def all_months(start, end):
    months = []
    y, m = int(start[:4]), int(start[4:])
    ye, me = int(end[:4]), int(end[4:])
    while (y < ye) or (y == ye and m <= me):
        months.append(f'{y:04d}{m:02d}')
        m += 1
        if m > 12:
            m = 1
            y += 1
    return months

conn = sqlite3.connect(DB)
c = conn.cursor()

c.execute("""SELECT material_name, COUNT(DISTINCT demand_month) as m, SUM(demand_quantity) as q
    FROM material_demand_total WHERE org_id=? GROUP BY 1 ORDER BY m DESC, q DESC LIMIT 5""", (JIBEI,))
top5 = [r[0] for r in c.fetchall()]
print(f'Jibei Top5: {top5}')

ph = ','.join(['?']*5)
c.execute(f'SELECT material_name, demand_month, SUM(demand_quantity), MAX(notice_count) FROM material_demand_total WHERE org_id=? AND material_name IN ({ph}) GROUP BY 1,2', [JIBEI]+top5)
rows = c.fetchall()

c.execute('SELECT notice_id, title, notice_publish_time FROM bid_notices')
bids = c.fetchall()
conn.close()

months = all_months('201911', '202607')
print(f'Months: {len(months)}')

monthly = defaultdict(lambda: {'trans': 0, 'uhv': 0, 'dig': 0, 'tot': 0})
for nid, t, pt in bids:
    ym = pt[:4] + pt[5:7]
    monthly[ym]['tot'] += 1
    if any(k in t for k in ['输变电', '变电设备']):
        monthly[ym]['trans'] += 1
    if '特高压' in t:
        monthly[ym]['uhv'] += 1
    if '数字化' in t:
        monthly[ym]['dig'] += 1

mat_data = {m: {} for m in top5}
for n, dm, q, nc in rows:
    if n in mat_data:
        mat_data[n][dm] = (q or 0, nc or 0)

wb = openpyxl.Workbook()
wb.remove(wb.active)
hdrs = ['日期', '需求量', '项目数量', 'transformer_bids', 'monthly_bid_count',
        'uhv_bids', 'has_batch', 'digital_bids']

for mat in top5:
    ws = wb.create_sheet(mat)
    for ci, h in enumerate(hdrs, 1):
        ws.cell(row=1, column=ci, value=h)
    for ri, dm in enumerate(months, 2):
        q, nc = mat_data[mat].get(dm, (0, 0))
        bf = monthly.get(dm, defaultdict(int))
        y, m = int(dm[:4]), int(dm[4:])
        ws.cell(row=ri, column=1, value=f'{y}-{m:02d}-01')
        ws.cell(row=ri, column=2, value=q)
        ws.cell(row=ri, column=3, value=nc)
        ws.cell(row=ri, column=4, value=bf.get('trans', 0))
        ws.cell(row=ri, column=5, value=bf.get('tot', 0))
        ws.cell(row=ri, column=6, value=bf.get('uhv', 0))
        ws.cell(row=ri, column=7, value=1 if bf.get('tot', 0) > 0 else 0)
        ws.cell(row=ri, column=8, value=bf.get('dig', 0))
    zeros = sum(1 for dm in months if mat_data[mat].get(dm, (0, 0))[0] == 0)
    print(f'  {mat}: {len(months)}月, zeros={zeros}')

os.makedirs(os.path.dirname(OUT), exist_ok=True)
wb.save(OUT)
print(f'Done: {OUT}')
