"""页面二：历史爬取记录与数据库数据检索"""

from fastapi import APIRouter, Query
from typing import Optional, List
from db import query, query_one, serialize

router = APIRouter(prefix="/api", tags=["数据检索"])


# ------------------------------------------------------------------
# 企业列表（支持搜索、分类、范围筛选、分页）
# ------------------------------------------------------------------
@router.get("/companies")
def list_companies(
    keyword: Optional[str] = Query(None, description="企业名模糊搜索"),
    credit_code: Optional[str] = Query(None, description="信用代码精确搜索"),
    status: Optional[str] = Query(None, description="状态分类 raw/scored/rated/filtered"),
    rating_level: Optional[str] = Query(None, description="评级等级 S/A/B/C/D"),
    industry_tag: Optional[str] = Query(None, description="行业标签多值筛选"),
    funding_stage: Optional[str] = Query(None, description="融资阶段"),
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=200),
):
    where = []
    params: list = []

    if keyword:
        where.append("c.company_name ILIKE %s")
        params.append(f"%{keyword}%")
    if credit_code:
        where.append("c.credit_code = %s")
        params.append(credit_code)
    if status:
        where.append("c.status = %s")
        params.append(status)
    if industry_tag:
        where.append("%s = ANY(c.industry_tags)")
        params.append(industry_tag)
    if funding_stage:
        where.append("c.funding_stage = %s")
        params.append(funding_stage)
    if rating_level:
        where.append("r.rating_level = %s")
        params.append(rating_level)

    where_sql = ("WHERE " + " AND ".join(where)) if where else ""

    # 总数
    count_sql = f"""
        SELECT count(DISTINCT c.id) FROM companies c
        LEFT JOIN ratings r ON r.company_id = c.id
        {where_sql}
    """
    total = query_one(count_sql, tuple(params)) or {}
    total_count = list(total.values())[0] if total else 0

    # 分页数据（每个企业取最高分评级）
    offset = (page - 1) * page_size
    list_sql = f"""
        SELECT DISTINCT ON (c.id)
            c.id, c.company_name, c.credit_code, c.registered_capital,
            c.capital_amount, c.established_date, c.legal_representative,
            c.status, c.industry_tags, c.funding_stage, c.registered_address,
            r.total_score, r.rating_level, r.rated_by, r.rated_at
        FROM companies c
        LEFT JOIN ratings r ON r.company_id = c.id
        {where_sql}
        ORDER BY c.id, r.total_score DESC NULLS LAST
        LIMIT %s OFFSET %s
    """
    rows = query(list_sql, tuple(params + [page_size, offset]))
    return {"total": total_count, "page": page, "page_size": page_size,
            "items": [serialize(r) for r in rows]}


# ------------------------------------------------------------------
# 企业详情（聚合四维数据 + 评级）
# ------------------------------------------------------------------
@router.get("/companies/{company_id}")
def company_detail(company_id: int):
    company = query_one("""
        SELECT * FROM companies WHERE id = %s
    """, (company_id,))
    if not company:
        return {"error": "企业不存在"}

    ratings = query("""
        SELECT * FROM ratings WHERE company_id = %s
        ORDER BY total_score DESC
    """, (company_id,))
    tech = query_one("SELECT * FROM tech_profiles WHERE company_id = %s", (company_id,))
    news = query("""
        SELECT id, title, content_summary, source_name, published_at,
               sentiment_score, is_digital_related
        FROM news_mentions WHERE company_id = %s
        ORDER BY published_at DESC NULLS LAST LIMIT 50
    """, (company_id,))
    recruitments = query("""
        SELECT id, position_title, salary_range, salary_min, salary_max,
               tech_keywords, source_name
        FROM recruitments WHERE company_id = %s LIMIT 50
    """, (company_id,))
    biddings = query("""
        SELECT id, project_name, project_type, budget_amount, is_digital,
               bid_date, source_name
        FROM bidding_records WHERE company_id = %s LIMIT 50
    """, (company_id,))

    return {
        "company": serialize(company),
        "ratings": [serialize(r) for r in ratings],
        "tech_profile": serialize(tech) if tech else None,
        "news": [serialize(r) for r in news],
        "recruitments": [serialize(r) for r in recruitments],
        "biddings": [serialize(r) for r in biddings],
    }


# ------------------------------------------------------------------
# 历史爬取记录
# ------------------------------------------------------------------
@router.get("/crawl-records")
def crawl_records(
    task_type: Optional[str] = Query(None),
    status: Optional[str] = Query(None),
    source_name: Optional[str] = Query(None),
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=200),
):
    where = []
    params: list = []
    if task_type:
        where.append("task_type = %s"); params.append(task_type)
    if status:
        where.append("status = %s"); params.append(status)
    if source_name:
        where.append("source_name ILIKE %s"); params.append(f"%{source_name}%")
    where_sql = ("WHERE " + " AND ".join(where)) if where else ""

    total = query_one(f"SELECT count(*) FROM crawl_tasks {where_sql}", tuple(params))
    total_count = list(total.values())[0] if total else 0

    offset = (page - 1) * page_size
    rows = query(f"""
        SELECT * FROM crawl_tasks {where_sql}
        ORDER BY created_at DESC NULLS LAST
        LIMIT %s OFFSET %s
    """, tuple(params + [page_size, offset]))
    return {"total": total_count, "page": page, "page_size": page_size,
            "items": [serialize(r) for r in rows]}


# ------------------------------------------------------------------
# 新闻舆情
# ------------------------------------------------------------------
@router.get("/news")
def list_news(
    company_id: Optional[int] = Query(None),
    keyword: Optional[str] = Query(None),
    source_name: Optional[str] = Query(None),
    is_digital: Optional[bool] = Query(None),
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=200),
):
    where = []
    params: list = []
    if company_id:
        where.append("n.company_id = %s"); params.append(company_id)
    if keyword:
        where.append("(n.title ILIKE %s OR n.content_summary ILIKE %s)")
        params += [f"%{keyword}%", f"%{keyword}%"]
    if source_name:
        where.append("n.source_name ILIKE %s"); params.append(f"%{source_name}%")
    if is_digital is not None:
        where.append("n.is_digital_related = %s"); params.append(is_digital)
    where_sql = ("WHERE " + " AND ".join(where)) if where else ""

    total = query_one(f"SELECT count(*) FROM news_mentions n {where_sql}", tuple(params))
    total_count = list(total.values())[0] if total else 0
    offset = (page - 1) * page_size
    rows = query(f"""
        SELECT n.*, c.company_name FROM news_mentions n
        LEFT JOIN companies c ON c.id = n.company_id
        {where_sql}
        ORDER BY n.published_at DESC NULLS LAST
        LIMIT %s OFFSET %s
    """, tuple(params + [page_size, offset]))
    return {"total": total_count, "page": page, "page_size": page_size,
            "items": [serialize(r) for r in rows]}


# ------------------------------------------------------------------
# 招聘
# ------------------------------------------------------------------
@router.get("/recruitments")
def list_recruitments(
    company_id: Optional[int] = Query(None),
    keyword: Optional[str] = Query(None),
    tech_keyword: Optional[str] = Query(None),
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=200),
):
    where = []
    params: list = []
    if company_id:
        where.append("r.company_id = %s"); params.append(company_id)
    if keyword:
        where.append("r.position_title ILIKE %s"); params.append(f"%{keyword}%")
    if tech_keyword:
        where.append("%s = ANY(r.tech_keywords)"); params.append(tech_keyword)
    where_sql = ("WHERE " + " AND ".join(where)) if where else ""

    total = query_one(f"SELECT count(*) FROM recruitments r {where_sql}", tuple(params))
    total_count = list(total.values())[0] if total else 0
    offset = (page - 1) * page_size
    rows = query(f"""
        SELECT r.*, c.company_name FROM recruitments r
        LEFT JOIN companies c ON c.id = r.company_id
        {where_sql}
        ORDER BY r.crawled_at DESC NULLS LAST
        LIMIT %s OFFSET %s
    """, tuple(params + [page_size, offset]))
    return {"total": total_count, "page": page, "page_size": page_size,
            "items": [serialize(r) for r in rows]}


# ------------------------------------------------------------------
# 招投标
# ------------------------------------------------------------------
@router.get("/biddings")
def list_biddings(
    company_id: Optional[int] = Query(None),
    keyword: Optional[str] = Query(None),
    project_type: Optional[str] = Query(None),
    is_digital: Optional[bool] = Query(None),
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=200),
):
    where = []
    params: list = []
    if company_id:
        where.append("b.company_id = %s"); params.append(company_id)
    if keyword:
        where.append("b.project_name ILIKE %s"); params.append(f"%{keyword}%")
    if project_type:
        where.append("b.project_type = %s"); params.append(project_type)
    if is_digital is not None:
        where.append("b.is_digital = %s"); params.append(is_digital)
    where_sql = ("WHERE " + " AND ".join(where)) if where else ""

    total = query_one(f"SELECT count(*) FROM bidding_records b {where_sql}", tuple(params))
    total_count = list(total.values())[0] if total else 0
    offset = (page - 1) * page_size
    rows = query(f"""
        SELECT b.*, c.company_name FROM bidding_records b
        LEFT JOIN companies c ON c.id = b.company_id
        {where_sql}
        ORDER BY b.bid_date DESC NULLS LAST
        LIMIT %s OFFSET %s
    """, tuple(params + [page_size, offset]))
    return {"total": total_count, "page": page, "page_size": page_size,
            "items": [serialize(r) for r in rows]}
