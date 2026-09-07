"""数据质量监控 — 模板scope、4维覆盖率、评分差异、采集进度、异常企业"""

from fastapi import APIRouter, Query
from typing import Optional
from db import query, query_one, serialize

router = APIRouter(prefix="/api/quality", tags=["数据质量监控"])


@router.get("/overview")
def overview():
    """质量总览"""
    template_scope = query_one("""
        SELECT count(*) AS n FROM companies
        WHERE business_scope LIKE '%人工智能应用软件开发、云计算技术服务、大数据分析、软件外包服务%'
    """) or {}
    real_scope = query_one("""
        SELECT count(*) AS n FROM companies
        WHERE business_scope IS NOT NULL AND business_scope != ''
          AND business_scope NOT LIKE '%人工智能应用软件开发、云计算技术服务、大数据分析、软件外包服务%'
    """) or {}
    empty_scope = query_one("""
        SELECT count(*) AS n FROM companies
        WHERE business_scope IS NULL OR business_scope = ''
    """) or {}
    return {
        "template_scope": template_scope.get("n", 0),
        "real_scope": real_scope.get("n", 0),
        "empty_scope": empty_scope.get("n", 0),
    }


@router.get("/coverage")
def coverage():
    """4维数据覆盖率明细"""
    rows = query("""
        SELECT
            count(*) AS total,
            count(DISTINCT n.company_id) AS news,
            count(DISTINCT t.company_id) AS tech,
            count(DISTINCT r.company_id) AS recruit,
            count(DISTINCT b.company_id) AS bid
        FROM companies c
        LEFT JOIN news_mentions n ON n.company_id = c.id
        LEFT JOIN tech_profiles t ON t.company_id = c.id
        LEFT JOIN recruitments r ON r.company_id = c.id
        LEFT JOIN bidding_records b ON b.company_id = c.id
    """)
    row = rows[0] if rows else {}
    total = row.get("total", 1) or 1
    return {
        "total": row.get("total", 0),
        "news": row.get("news", 0),
        "tech": row.get("tech", 0),
        "recruit": row.get("recruit", 0),
        "bid": row.get("bid", 0),
        "news_pct": round(row.get("news", 0) * 100 / total, 1),
        "tech_pct": round(row.get("tech", 0) * 100 / total, 1),
        "recruit_pct": round(row.get("recruit", 0) * 100 / total, 1),
        "bid_pct": round(row.get("bid", 0) * 100 / total, 1),
    }


@router.get("/template-companies")
def template_companies(
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=200),
):
    """模板scope企业列表（数据孤儿，待重采真实工商数据）"""
    total = query_one("""
        SELECT count(*) AS n FROM companies
        WHERE business_scope LIKE '%人工智能应用软件开发、云计算技术服务、大数据分析、软件外包服务%'
    """) or {}
    total_count = total.get("n", 0)
    offset = (page - 1) * page_size
    rows = query(f"""
        SELECT c.id, c.company_name, c.credit_code, c.status, c.industry_tags,
               c.registered_capital, c.created_at,
               r.total_score, r.rating_level
        FROM companies c
        LEFT JOIN ratings r ON r.company_id = c.id
        WHERE c.business_scope LIKE '%人工智能应用软件开发、云计算技术服务、大数据分析、软件外包服务%'
        ORDER BY c.id
        LIMIT %s OFFSET %s
    """, (page_size, offset))
    return {"total": total_count, "page": page, "page_size": page_size,
            "items": [serialize(r) for r in rows]}


@router.get("/score-diff")
def score_diff(limit: int = Query(50, ge=1, le=500)):
    """规则 vs DeepSeek 评分差异"""
    rows = query("""
        SELECT c.id, c.company_name,
               ru.total_score AS rules_score, ru.rating_level AS rules_level,
               ds.total_score AS ds_score, ds.rating_level AS ds_level,
               ru.reasoning AS rules_reasoning, ds.reasoning AS ds_reasoning
        FROM companies c
        JOIN ratings ru ON ru.company_id = c.id AND ru.rated_by = 'rules_engine'
        JOIN ratings ds ON ds.company_id = c.id AND ds.rated_by = 'deepseek'
        ORDER BY abs(ru.total_score - ds.total_score) DESC
        LIMIT %s
    """, (limit,))
    items = []
    for r in rows:
        d = serialize(r)
        d["score_diff"] = (r["rules_score"] or 0) - (r["ds_score"] or 0)
        d["level_diff"] = (r["rules_level"] or "") != (r["ds_level"] or "")
        items.append(d)
    return {"items": items}


@router.get("/anomalies")
def anomalies():
    """异常企业：评级为空、数据维度全空、等级与数据量不匹配"""
    # 评级为空
    no_rating = query_one("""
        SELECT count(*) AS n FROM companies c
        WHERE NOT EXISTS (SELECT 1 FROM ratings r WHERE r.company_id = c.id)
    """) or {}
    # 四维全空
    no_dim = query_one("""
        SELECT count(*) AS n FROM companies c
        WHERE NOT EXISTS (SELECT 1 FROM news_mentions n WHERE n.company_id = c.id)
          AND NOT EXISTS (SELECT 1 FROM recruitments r WHERE r.company_id = c.id)
          AND NOT EXISTS (SELECT 1 FROM bidding_records b WHERE b.company_id = c.id)
    """) or {}
    # 无credit_code
    no_credit = query_one("""
        SELECT count(*) AS n FROM companies WHERE credit_code IS NULL OR credit_code = ''
    """) or {}
    return {
        "no_rating": no_rating.get("n", 0),
        "no_dimension": no_dim.get("n", 0),
        "no_credit_code": no_credit.get("n", 0),
    }


@router.get("/crawl-progress")
def crawl_progress():
    """采集任务进度统计"""
    rows = query("""
        SELECT status, task_type, count(*) AS n
        FROM crawl_tasks
        GROUP BY status, task_type
        ORDER BY status, task_type
    """)
    return {"items": [serialize(r) for r in rows]}


@router.get("/low-data-rated")
def low_data_rated(limit: int = Query(30, ge=1, le=200)):
    """数据稀疏但评级偏高企业（数据量≤2 但评级≥B）— 呼应验证报告发现"""
    rows = query("""
        SELECT c.id, c.company_name, r.total_score, r.rating_level, r.rated_by,
               (SELECT count(*) FROM news_mentions n WHERE n.company_id = c.id) AS news_n,
               (SELECT count(*) FROM recruitments rc WHERE rc.company_id = c.id) AS recruit_n,
               (SELECT count(*) FROM bidding_records b WHERE b.company_id = c.id) AS bid_n
        FROM companies c
        JOIN ratings r ON r.company_id = c.id
        WHERE r.rating_level IN ('S','A','B')
        ORDER BY (news_n + recruit_n + bid_n) ASC, r.total_score DESC
        LIMIT %s
    """, (limit,))
    return {"items": [serialize(r) for r in rows]}
