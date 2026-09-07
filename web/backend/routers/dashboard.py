"""页面三：评级看板数据聚合"""

from fastapi import APIRouter, Query
from typing import Optional
from db import query, query_one, serialize

router = APIRouter(prefix="/api/dashboard", tags=["评级看板"])


@router.get("/summary")
def summary():
    """顶部 KPI 卡片"""
    companies = query_one("SELECT count(*) AS n FROM companies") or {}
    rated = query_one(
        "SELECT count(DISTINCT company_id) AS n FROM ratings WHERE rating_level IS NOT NULL"
    ) or {}
    sa_leads = query_one(
        "SELECT count(DISTINCT company_id) AS n FROM ratings WHERE rating_level IN ('S','A')"
    ) or {}
    pending = query_one(
        "SELECT count(*) AS n FROM companies WHERE status IN ('raw','scored')"
    ) or {}
    return {
        "total_companies": companies.get("n", 0),
        "rated": rated.get("n", 0),
        "sa_leads": sa_leads.get("n", 0),
        "pending": pending.get("n", 0),
    }


@router.get("/distribution")
def distribution():
    """等级分布 S/A/B/C/D"""
    rows = query("""
        SELECT rating_level, count(DISTINCT company_id) AS n
        FROM ratings
        WHERE rating_level IS NOT NULL
        GROUP BY rating_level
        ORDER BY rating_level
    """)
    return {"items": [serialize(r) for r in rows]}


@router.get("/dimension-radar")
def dimension_radar():
    """五维分数均值（按等级分组）"""
    rows = query("""
        SELECT rating_level,
               avg(tech_score) AS tech,
               avg(funding_score) AS funding,
               avg(intent_score) AS intent,
               avg(team_score) AS team,
               avg(industry_score) AS industry
        FROM ratings
        WHERE rating_level IS NOT NULL
        GROUP BY rating_level
        ORDER BY rating_level
    """)
    return {"items": [serialize(r) for r in rows]}


@router.get("/score-trend")
def score_trend():
    """评分变更趋势（读 rating_changelog，按天聚合）"""
    rows = query("""
        SELECT changed_at::date AS d, count(*) AS n
        FROM rating_changelog
        WHERE changed_at IS NOT NULL
        GROUP BY d
        ORDER BY d
    """)
    return {"items": [serialize(r) for r in rows]}


@router.get("/leads")
def leads(
    limit: int = Query(50, ge=1, le=500),
):
    """S/A 级线索榜"""
    rows = query("""
        SELECT DISTINCT ON (c.id)
            c.id, c.company_name, c.credit_code, c.industry_tags,
            r.total_score, r.rating_level, r.demand_tags,
            r.sales_pitch, r.reasoning, r.rated_by
        FROM ratings r
        JOIN companies c ON c.id = r.company_id
        WHERE r.rating_level IN ('S','A')
        ORDER BY c.id, r.total_score DESC
        LIMIT %s
    """, (limit,))
    return {"items": [serialize(r) for r in rows]}


@router.get("/data-quality")
def data_quality():
    """数据质量监控"""
    # 模板 scope 企业数
    template_scope = query_one("""
        SELECT count(*) AS n FROM companies
        WHERE business_scope LIKE '%人工智能应用软件开发、云计算技术服务、大数据分析、软件外包服务%'
    """) or {}
    real_scope = query_one("""
        SELECT count(*) AS n FROM companies
        WHERE business_scope IS NOT NULL AND business_scope != ''
          AND business_scope NOT LIKE '%人工智能应用软件开发、云计算技术服务、大数据分析、软件外包服务%'
    """) or {}
    # 4维覆盖
    coverage = query_one("""
        SELECT
            count(DISTINCT n.company_id) AS news,
            count(DISTINCT t.company_id) AS tech,
            count(DISTINCT r.company_id) AS recruit,
            count(DISTINCT b.company_id) AS bid
        FROM companies c
        LEFT JOIN news_mentions n ON n.company_id = c.id
        LEFT JOIN tech_profiles t ON t.company_id = c.id
        LEFT JOIN recruitments r ON r.company_id = c.id
        LEFT JOIN bidding_records b ON b.company_id = c.id
    """) or {}
    # 规则 vs DeepSeek 评分差异
    diff = query("""
        SELECT c.company_name, c.id,
               ru.total_score AS rules_score, ru.rating_level AS rules_level,
               ds.total_score AS ds_score, ds.rating_level AS ds_level
        FROM companies c
        JOIN ratings ru ON ru.company_id = c.id AND ru.rated_by = 'rules_engine'
        JOIN ratings ds ON ds.company_id = c.id AND ds.rated_by = 'deepseek'
        ORDER BY abs(ru.total_score - ds.total_score) DESC
        LIMIT 20
    """)
    return {
        "template_scope": template_scope.get("n", 0),
        "real_scope": real_scope.get("n", 0),
        "coverage": serialize(coverage),
        "score_diff": [serialize(r) for r in diff],
    }
