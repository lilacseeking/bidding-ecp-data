# bidding-ecp-data

国网新一代电子商务平台 (ECP2.0) 招标数据采集与分析工具。

## 项目结构

```
bidding-ecp-data/
├── src/
│   ├── crawler/
│   │   ├── __init__.py
│   │   ├── ecp_client.py          # ECP2.0 API 客户端
│   │   └── verify_sampling.py     # 数据抽样验证脚本
│   └── utils/
│       ├── __init__.py
│       └── export.py              # 数据导出工具 (JSON→CSV)
├── data/
│   ├── samples/                   # 抽样数据
│   └── raw/                       # 原始数据
├── reports/
│   ├── research_report.md         # 调研报告
│   └── verification_report.md     # SOP验证报告
└── README.md
```

## 快速开始

```bash
# 安装依赖
pip install requests

# 运行API自测
python src/crawler/ecp_client.py

# 运行完整验证
python src/crawler/verify_sampling.py
```

## 核心API

```python
from src.crawler.ecp_client import EcpClient, ORG_MAP

client = EcpClient()

# 查询冀北电力所有公告
jibei_id = ORG_MAP["国网冀北电力有限公司"]
result = client.query_notices(org_id=jibei_id, page=1, page_size=20)
print(f"冀北共{result.paging.total_count}条公告")

# 获取全部公告（自动分页）
all_data = client.query_all(org_id=jibei_id, page_size=50)

# 获取所有招标单位
orgs = client.get_org_list()
```

## 验证结果摘要

| 验证项 | 结果 |
|--------|------|
| API可访问性 | ✅ 无需登录 |
| orgId过滤 | ✅ 100%准确 |
| 分页一致性 | ✅ 无重复 |
| 时间跨度(冀北) | ✅ 6.2年 |
| 物资公告占比 | ✅ ~27% |
| 物资名称字段 | ⚠️ 需从详情页/附件获取 |
| 需求数量字段 | ⚠️ 需从货物清单附件获取 |

详见 `reports/verification_report.md`

## 已知限制

1. **物资名称和需求数量不在列表API中**——需进入详情页或下载附件获取
2. **中标公告菜单ID未确定**——需进一步逆向ECP SPA路由
3. **平台历史数据约6年**——2020年之前的数据不可用
