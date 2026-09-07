#!/usr/bin/env python3
"""导出1000家企业最终评级结果 → CSV (18列, 与all_rated_companies同格式)

数据源: PostgreSQL rating_system
- 每家企业评级优先级: kscc > deepseek > rules_engine
- 包含工商字段: credit_code/注册资本/经营范围/地址/融资阶段

用法: python scripts/export_1000_results.py [--output PATH]
"""
import os
import csv
import sys
import logging
from datetime import datetime

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from dotenv import load_dotenv

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger(__name__)

PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
load_dotenv(os.path.join(PROJECT_ROOT, ".env"))
DATABASE_URL = os.getenv("DATABASE_URL")

DEFAULT_OUT = os.path.join(PROJECT_ROOT, "data", "potential_companies", "wuhan_it_1000_scored.csv")

# 18列 (与 all_rated_companies.csv / 5_smart_manufacturing_scored.csv 同格式)
OUT_COLUMNS = ["company_name", "credit_code", "registered_capital", "business_scope",
               "industry_tags", "registered_address", "funding_stage",
               "total_score", "rating_level", "tech_score", "funding_score",
               "intent_score", "team_score", "industry_score",
               "demand_tags", "sales_pitch", "reasoning", "crawl_time"]


def main():
    import argparse
    import psycopg2
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", default=DEFAULT_OUT, help="输出CSV路径")
    args = parser.parse_args()

    conn = psycopg2.connect(DATABASE_URL)
    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT
                    c.company_name,
                    COALESCE(c.credit_code, '') AS credit_code,
                    COALESCE(c.registered_capital, '') AS registered_capital,
                    COALESCE(c.business_scope, '') AS business_scope,
                    c.industry_tags,
                    COALESCE(c.registered_address, '') AS registered_address,
                    COALESCE(c.funding_stage, '') AS funding_stage,
                    COALESCE(lk.total_score, ld.total_score, rr.total_score) AS total_score,
                    COALESCE(lk.rating_level, ld.rating_level, rr.rating_level) AS rating_level,
                    COALESCE(lk.tech_score, ld.tech_score, rr.tech_score, 0) AS tech_score,
                    COALESCE(lk.funding_score, ld.funding_score, rr.funding_score, 0) AS funding_score,
                    COALESCE(lk.intent_score, ld.intent_score, rr.intent_score, 0) AS intent_score,
                    COALESCE(lk.team_score, ld.team_score, rr.team_score, 0) AS team_score,
                    COALESCE(lk.industry_score, ld.industry_score, rr.industry_score, 0) AS industry_score,
                    COALESCE(lk.demand_tags, ld.demand_tags, rr.demand_tags) AS demand_tags,
                    COALESCE(lk.sales_pitch, ld.sales_pitch, rr.sales_pitch) AS sales_pitch,
                    COALESCE(lk.reasoning, ld.reasoning, rr.reasoning) AS reasoning,
                    COALESCE(lk.rated_at, ld.rated_at, rr.rated_at) AS crawl_time
                FROM companies c
                LEFT JOIN ratings lk ON lk.company_id = c.id AND lk.rated_by = 'kscc'
                LEFT JOIN ratings ld ON ld.company_id = c.id AND ld.rated_by = 'deepseek'
                LEFT JOIN ratings rr ON rr.company_id = c.id AND rr.rated_by = 'rules_engine'
                WHERE c.credit_code IS NOT NULL
                ORDER BY COALESCE(lk.total_score, ld.total_score, rr.total_score) DESC NULLS LAST, c.id
                """
            )
            cols = [d[0] for d in cur.description]
            rows = cur.fetchall()
    finally:
        conn.close()

    os.makedirs(os.path.dirname(args.output) or ".", exist_ok=True)
    with open(args.output, "w", newline="", encoding="utf-8-sig") as f:
        writer = csv.writer(f)
        writer.writerow(OUT_COLUMNS)
        for row in rows:
            d = dict(zip(cols, row))
            tags = ", ".join(d["industry_tags"]) if isinstance(d["industry_tags"], list) else (d["industry_tags"] or "—")
            demand = ", ".join(d["demand_tags"]) if isinstance(d["demand_tags"], list) else (d["demand_tags"] or "—")
            writer.writerow([
                d["company_name"], d["credit_code"] or "—", d["registered_capital"] or "—",
                d["business_scope"] or "—", tags or "—", d["registered_address"] or "—",
                d["funding_stage"] or "—",
                d["total_score"] if d["total_score"] is not None else "—",
                d["rating_level"] if d["rating_level"] else "—",
                d["tech_score"] or 0, d["funding_score"] or 0, d["intent_score"] or 0,
                d["team_score"] or 0, d["industry_score"] or 0,
                demand, d["sales_pitch"] or "—", d["reasoning"] or "—",
                d["crawl_time"].strftime("%Y-%m-%d %H:%M:%S") if d["crawl_time"] else "—",
            ])

    logger.info(f"已导出 {len(rows)} 家 → {args.output}")

    # 等级分布统计
    from collections import Counter
    levels = Counter(r[8] for r in rows if r[8])
    logger.info(f"等级分布: {dict(levels)}")


if __name__ == "__main__":
    main()
