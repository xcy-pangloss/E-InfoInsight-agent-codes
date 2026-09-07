#!/usr/bin/env python3
"""筛选300家信息丰富企业作为展示数据

标准 (按优先级):
1. 真实经营范围 + 注册资本 双齐全 (162家, 全部入选)
2. 有真实经营范围 或 注册资本 (其一) + 其他维度丰富
   (新闻数/招聘数/技术栈/评分 加权排序)

输出: data/potential_companies/wuhan_it_1000_demo_300.csv
"""
import os
import sys
import csv
import logging
from collections import Counter

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from dotenv import load_dotenv

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger(__name__)

PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
load_dotenv(os.path.join(PROJECT_ROOT, ".env"))
DATABASE_URL = os.getenv("DATABASE_URL")

OUTPUT = os.path.join(PROJECT_ROOT, "data", "potential_companies", "wuhan_it_1000_demo_300.csv")
TARGET = 300


def main():
    import psycopg2
    conn = psycopg2.connect(DATABASE_URL)
    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT c.id, c.company_name, COALESCE(c.credit_code, '') AS credit_code,
                       COALESCE(c.registered_capital, '') AS registered_capital,
                       COALESCE(c.business_scope, '') AS business_scope,
                       COALESCE(c.registered_address, '') AS registered_address,
                       COALESCE(c.legal_representative, '') AS legal_representative,
                       COALESCE(c.established_date::text, '') AS established_date,
                       r.total_score, r.rating_level,
                       (SELECT count(*) FROM news_mentions n
                        WHERE n.company_id = c.id AND n.source_name NOT LIKE 'websearch%') AS news_cnt,
                       (SELECT count(*) FROM recruitments rc WHERE rc.company_id = c.id) AS recruit_cnt,
                       COALESCE((SELECT array_length(tp.tech_stack, 1) FROM tech_profiles tp
                                 WHERE tp.company_id = c.id), 0) AS tech_cnt
                FROM companies c
                LEFT JOIN ratings r ON r.company_id = c.id AND r.rated_by = 'rules_engine'
                WHERE c.credit_code IS NOT NULL
                ORDER BY c.id
                """
            )
            rows = cur.fetchall()
    finally:
        conn.close()

    companies = []
    for r in rows:
        cid, name, code, capital, scope, addr, legal, est, score, level, news, recruit, tech = r
        has_real_scope = scope and not scope.startswith("软件开发、信息技术咨询服务")
        has_capital = bool(capital)
        companies.append({
            "id": cid, "company_name": name, "credit_code": code,
            "registered_capital": capital, "business_scope": scope,
            "registered_address": addr, "legal_representative": legal,
            "established_date": est, "total_score": score, "rating_level": level,
            "news_cnt": news or 0, "recruit_cnt": recruit or 0, "tech_cnt": tech or 0,
            "has_real_scope": has_real_scope, "has_capital": has_capital,
        })

    # Tier1: 双齐全
    tier1 = [c for c in companies if c["has_real_scope"] and c["has_capital"]]
    logger.info(f"Tier1 双齐全: {len(tier1)} 家")

    # Tier2: 有一项 + 其他维度丰富 (按信息丰富度加权排序)
    tier2 = [c for c in companies if (c["has_real_scope"] or c["has_capital"]) and c not in tier1]
    for c in tier2:
        c["richness"] = (c["news_cnt"] * 3 + c["recruit_cnt"] * 2 + c["tech_cnt"] * 2
                         + (1 if c["has_real_scope"] else 0) * 2 + (1 if c["has_capital"] else 0) * 2)
    tier2.sort(key=lambda x: (-x["richness"], -(x["total_score"] or 0)))

    # 组合
    selected = list(tier1)
    need = TARGET - len(selected)
    if need > 0:
        selected += tier2[:need]
    logger.info(f"选中 {len(selected)} 家 (Tier1={len(tier1)}, Tier2补充={len(selected)-len(tier1)})")

    # 统计
    levels = Counter(c["rating_level"] for c in selected)
    logger.info(f"等级分布: {dict(levels)}")
    logger.info(f"含真实经营范围: {sum(1 for c in selected if c['has_real_scope'])}")
    logger.info(f"含注册资本: {sum(1 for c in selected if c['has_capital'])}")
    logger.info(f"含地址: {sum(1 for c in selected if c['registered_address'])}")
    logger.info(f"含法人: {sum(1 for c in selected if c['legal_representative'])}")
    logger.info(f"含成立日期: {sum(1 for c in selected if c['established_date'])}")

    # 导出
    os.makedirs(os.path.dirname(OUTPUT), exist_ok=True)
    columns = ["company_name", "credit_code", "registered_capital", "business_scope",
               "registered_address", "legal_representative", "established_date",
               "total_score", "rating_level"]
    with open(OUTPUT, "w", newline="", encoding="utf-8-sig") as f:
        writer = csv.DictWriter(f, fieldnames=columns, extrasaction="ignore")
        writer.writeheader()
        for c in sorted(selected, key=lambda x: -(x["total_score"] or 0)):
            writer.writerow(c)

    logger.info(f"已导出 {len(selected)} 家 → {OUTPUT}")


if __name__ == "__main__":
    main()
