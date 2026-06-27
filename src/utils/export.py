"""
数据导出工具：JSON → CSV / Excel / SQLite
"""
import csv
import json
import os
from typing import Optional


def json_to_csv(json_path: str, csv_path: Optional[str] = None):
    """将ECP导出的JSON文件转换为CSV"""
    if csv_path is None:
        csv_path = json_path.replace(".json", ".csv")

    with open(json_path, "r", encoding="utf-8") as f:
        records = json.load(f)

    if not records:
        print("空数据，跳过")
        return

    fieldnames = list(records[0].keys())
    with open(csv_path, "w", encoding="utf-8-sig", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(records)

    print(f"CSV导出完成: {csv_path} ({len(records)} 条, {os.path.getsize(csv_path):,} bytes)")


def filter_by_keyword(records: list[dict], keywords: list[str]) -> list[dict]:
    """按标题关键词筛选"""
    result = []
    for r in records:
        title = r.get("title", "")
        if any(kw in title for kw in keywords):
            result.append(r)
    return result


MATERIAL_KEYWORDS = [
    "物资", "设备", "材料", "电缆", "变压器", "开关柜",
    "组合电器", "GIS", "断路器", "互感器", "避雷器",
    "绝缘子", "电容器", "电抗器", "导线", "光缆",
]

SERVICE_KEYWORDS = [
    "服务", "工程", "设计", "施工", "监理", "运维",
    "修理", "咨询", "劳务",
]
