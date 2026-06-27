"""
ECP2.0 平台 API 客户端
提供招标公告列表查询、详情获取、附件下载等功能
"""
import json
import time
import requests
from dataclasses import dataclass, field, asdict
from typing import Optional

# ============================================================
# 常量定义
# ============================================================

ECP_BASE = "https://ecp.sgcc.com.cn"
ECP_API = f"{ECP_BASE}/ecp2.0/ecpwcmcore//index"

# 菜单ID（采购公告列表）
MENU_ID_PROCUREMENT = "2018032700291334"

# 招标单位 orgId 映射
ORG_MAP = {
    "国网冀北电力有限公司": "2019061900137008",
    "国网冀北电力有限公司物资分公司": "2021051082986727",
    "国网河北省电力有限公司": "2019061900049520",
    "国网北京市电力公司": "2019061900012414",
    "国网天津市电力公司": "2019061900248174",
    "国家电网有限公司": "2019040100044796",
}

# 公告类型
NOTICE_TYPE_MAP = {
    100063001: "公开招标采购公告",
    100063002: "邀请招标采购公告",
    100063003: "竞争性谈判公告",
    100063004: "询价采购公告",
    100063005: "单一来源采购公告",
    100063006: "框架协议采购公告",
}

# 采购类型
PUR_TYPE_MAP = {
    120012001: "总部实施设备、材料批次招标项目",
    120012002: "总部实施配农网设备材料招标项目",
    120012003: "总部实施特高压工程物资类招标项目",
    120012014: "总部组织省公司实施物资类招标项目",
    120012015: "总部组织省公司实施服务类招标项目",
}


@dataclass
class NoticeItem:
    """公告条目"""
    notice_id: str
    title: str
    code: str
    publish_org_name: str
    org_id: str
    notice_publish_time: str
    notice_type: int
    doctype: str
    first_page_doc_id: str
    notice_type_name: str = ""
    doc_url: str = ""

    def __post_init__(self):
        if not self.notice_type_name:
            self.notice_type_name = NOTICE_TYPE_MAP.get(self.notice_type, f"未知({self.notice_type})")
        if not self.doc_url:
            self.doc_url = (
            f"{ECP_BASE}/ecp2.0/portal/#/doc/{self.doctype}/"
            f"{self.notice_id}_{MENU_ID_PROCUREMENT}"
        )


@dataclass
class PagingInfo:
    """分页信息"""
    total_count: int
    current_page: int
    page_size: int
    total_pages: int

    @property
    def has_next(self) -> bool:
        return self.current_page < self.total_pages


@dataclass
class QueryResult:
    """查询结果"""
    notices: list[NoticeItem] = field(default_factory=list)
    paging: Optional[PagingInfo] = None
    org_list: list[dict] = field(default_factory=list)
    pur_types: list[dict] = field(default_factory=list)


class EcpClient:
    """ECP2.0 API 客户端"""

    def __init__(self, timeout: int = 30, retry: int = 3):
        self.session = requests.Session()
        self.session.headers.update({
            "Content-Type": "application/json",
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/120.0.0.0 Safari/537.36"
            ),
            "Accept": "application/json, text/plain, */*",
            "Referer": f"{ECP_BASE}/ecp2.0/portal/",
        })
        self.timeout = timeout
        self.retry = retry

    def _post(self, url: str, payload: dict) -> dict:
        """发送POST请求，带重试"""
        last_err = None
        for attempt in range(self.retry):
            try:
                resp = self.session.post(
                    url, json=payload, timeout=self.timeout
                )
                resp.raise_for_status()
                data = resp.json()
                if not data.get("successful"):
                    raise RuntimeError(f"API返回失败: {data.get('resultHint', 'unknown')}")
                return data
            except Exception as e:
                last_err = e
                if attempt < self.retry - 1:
                    time.sleep(2 ** attempt)
        raise last_err

    def query_notices(
        self,
        org_id: Optional[str] = None,
        keyword: str = "",
        notice_type: Optional[int] = None,
        pur_type: Optional[str] = None,
        page: int = 1,
        page_size: int = 20,
        menu_id: str = MENU_ID_PROCUREMENT,
    ) -> QueryResult:
        """
        查询招标公告列表

        Args:
            org_id: 招标单位orgId，如 ORG_MAP["国网冀北电力有限公司"]
            keyword: 标题搜索关键词
            notice_type: 公告类型过滤（效果有限）
            pur_type: 采购类型过滤
            page: 页码（从1开始）
            page_size: 每页条数
            menu_id: 菜单ID

        Returns:
            QueryResult 包含公告列表和分页信息
        """
        payload = {
            "firstPageMenuId": menu_id,
            "index": page,
            "key": keyword,
            "orgId": org_id or "",
            "purOrgCode": "",
            "purOrgStatus": "",
            "purType": pur_type or "",
            "size": page_size,
        }

        data = self._post(f"{ECP_API}/noteList", payload)
        rv = data["resultValue"]

        notices = []
        for item in rv.get("noteList", []):
            notices.append(NoticeItem(
                notice_id=str(item.get("id", "")),
                title=item.get("title", ""),
                code=item.get("code", ""),
                publish_org_name=item.get("publishOrgName", ""),
                org_id=str(item.get("orgId", "")),
                notice_publish_time=item.get("noticePublishTime", ""),
                notice_type=item.get("noticeType", 0),
                doctype=item.get("doctype", ""),
                first_page_doc_id=str(item.get("firstPageDocId", "")),
            ))

        total = rv.get("count", 0)
        total_pages = (total + page_size - 1) // page_size if total > 0 else 0

        return QueryResult(
            notices=notices,
            paging=PagingInfo(
                total_count=total,
                current_page=page,
                page_size=page_size,
                total_pages=total_pages,
            ),
            org_list=rv.get("orglist", {}).get("items", []),
            pur_types=rv.get("purTypes", {}).get("items", []),
        )

    def query_all(
        self,
        org_id: Optional[str] = None,
        keyword: str = "",
        page_size: int = 50,
        max_pages: Optional[int] = None,
        **kwargs,
    ) -> QueryResult:
        """
        分页获取全部公告数据

        Args:
            org_id: 招标单位orgId
            keyword: 关键词
            page_size: 每页条数（最大50）
            max_pages: 最大页数限制，None=全部
        """
        all_notices = []
        page = 1

        # 先获取第一页确定总数
        result = self.query_notices(
            org_id=org_id, keyword=keyword, page=1, page_size=page_size, **kwargs
        )
        all_notices.extend(result.notices)

        total_pages = result.paging.total_pages if result.paging else 0
        if max_pages:
            total_pages = min(total_pages, max_pages)

        # 获取剩余页
        for page in range(2, total_pages + 1):
            time.sleep(0.5)  # 礼貌延迟
            result = self.query_notices(
                org_id=org_id, keyword=keyword, page=page,
                page_size=page_size, **kwargs
            )
            all_notices.extend(result.notices)

        return QueryResult(
            notices=all_notices,
            paging=PagingInfo(
                total_count=len(all_notices),
                current_page=1,
                page_size=page_size,
                total_pages=total_pages,
            ),
            org_list=result.org_list,
            pur_types=result.pur_types,
        )

    def get_org_list(self) -> list[dict]:
        """获取所有招标单位列表"""
        result = self.query_notices(page=1, page_size=1)
        return result.org_list

    def get_pur_types(self) -> list[dict]:
        """获取所有采购类型"""
        result = self.query_notices(page=1, page_size=1)
        return result.pur_types

    def get_date_range(
        self, org_id: str, page_size: int = 50
    ) -> tuple[str, str]:
        """
        获取指定单位公告的日期范围

        Returns:
            (最早日期, 最晚日期)
        """
        # 获取第一页（最新）
        first = self.query_notices(org_id=org_id, page=1, page_size=page_size)
        latest = first.notices[0].notice_publish_time if first.notices else "N/A"

        # 获取最后一页（最早）
        if first.paging and first.paging.total_pages > 1:
            last = self.query_notices(
                org_id=org_id, page=first.paging.total_pages, page_size=page_size
            )
            earliest = last.notices[-1].notice_publish_time if last.notices else "N/A"
        else:
            earliest = first.notices[-1].notice_publish_time if first.notices else "N/A"

        return earliest, latest


if __name__ == "__main__":
    # 快速自测
    client = EcpClient()
    print("=" * 60)
    print("ECP2.0 API Client - 自测")
    print("=" * 60)

    # 测试1：获取冀北电力公告
    jibei_id = ORG_MAP["国网冀北电力有限公司"]
    result = client.query_notices(org_id=jibei_id, page=1, page_size=5)
    print(f"\n[冀北电力] 总记录数: {result.paging.total_count}")
    print(f"[冀北电力] 总页数: {result.paging.total_pages}")
    print(f"\n前5条公告:")
    for n in result.notices:
        print(f"  [{n.notice_publish_time}] {n.title[:60]}...")
        print(f"    doctype={n.doctype}, code={n.code}")

    # 测试2：获取日期范围
    earliest, latest = client.get_date_range(jibei_id, page_size=50)
    print(f"\n[冀北电力] 数据时间范围: {earliest} ~ {latest}")

    # 测试3：获取全部数据量级
    no_filter = client.query_notices(page=1, page_size=1)
    print(f"\n[全平台] 总公告数: {no_filter.paging.total_count}")

    print("\n自测完成!")
