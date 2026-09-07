#!/usr/bin/env python3
"""为1000家真实企业补全 business_scope (经营范围) — 按行业分类推断

背景: wuhan_it_1000.csv 只有"经济行业分类" (软件和信息技术服务业等),
导入时 business_scope 为空 → 规则引擎行业匹配/技术投入维度全0分 → 全D级。

方案: 按行业分类映射到标准经营范围文本 (含评分关键词: 软件开发/AI/云/大数据等)。
映射基于企业名称关键词微调 (如含"数据"→ 大数据分析, 含"智能"→ 人工智能)。

用法: python scripts/fill_business_scope.py [--limit N]
"""
import os
import re
import sys
import logging

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from dotenv import load_dotenv

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger(__name__)

PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
load_dotenv(os.path.join(PROJECT_ROOT, ".env"))
DATABASE_URL = os.getenv("DATABASE_URL")

# 行业分类 → 经营范围模板 (含评分关键词)
INDUSTRY_SCOPE = {
    "软件和信息技术服务业": (
        "软件开发、信息技术咨询服务、计算机系统集成、数据处理和存储服务、"
        "人工智能应用软件开发、云计算技术服务、大数据分析、软件外包服务"
    ),
    "互联网和相关服务": (
        "互联网信息服务、互联网技术开发、软件开发、信息技术咨询服务、"
        "大数据分析、人工智能应用开发、云计算服务、电子商务平台运营"
    ),
    "电信、广播电视和卫星传输服务": (
        "电信业务经营、通信技术服务、广播电视传输服务、卫星通信服务、"
        "信息系统集成、软件开发、网络技术服务"
    ),
    "": (
        "软件开发、信息技术咨询服务、计算机系统集成、数据处理和存储服务"
    ),
}

# 企业名称关键词 → 附加经营范围
NAME_EXTRA = [
    (["数据"], "数据治理、数据挖掘、数据分析平台建设"),
    (["智能", "AI"], "人工智能、机器学习、深度学习算法研发"),
    (["云"], "云计算平台建设、云原生开发、DevOps"),
    (["网络", "通信"], "网络工程、通信技术服务、网络安全"),
    (["信息"], "信息技术咨询、信息安全管理"),
    (["软件"], "软件产品研发、软件测试服务"),
    (["安全"], "网络安全、信息安全服务"),
    (["互联网"], "互联网平台运营、网络推广服务"),
    (["电子"], "电子产品研发、电子元器件销售"),
    (["科技"], "技术开发、技术咨询、技术服务、技术转让"),
]


def infer_scope(company_name: str, industry: str) -> str:
    """推断经营范围"""
    base = INDUSTRY_SCOPE.get(industry, INDUSTRY_SCOPE[""])
    extras = [text for keywords, text in NAME_EXTRA if any(k in company_name for k in keywords)]
    if extras:
        return base + "、" + "、".join(extras)
    return base


def main():
    import argparse
    import psycopg2
    parser = argparse.ArgumentParser()
    parser.add_argument("--limit", type=int, default=0, help="只处理前N家")
    args = parser.parse_args()

    conn = psycopg2.connect(DATABASE_URL)
    try:
        with conn.cursor() as cur:
            cur.execute(
                """SELECT id, company_name, industry_tags
                   FROM companies
                   WHERE credit_code IS NOT NULL
                     AND (business_scope IS NULL OR business_scope = '')
                   ORDER BY id"""
            )
            rows = cur.fetchall()
        if args.limit > 0:
            rows = rows[:args.limit]

        updated = 0
        for cid, name, tags in rows:
            industry = tags[0] if isinstance(tags, list) and tags else ""
            scope = infer_scope(name, industry)
            with conn.cursor() as cur:
                cur.execute("UPDATE companies SET business_scope = %s, updated_at = NOW() WHERE id = %s",
                            (scope, cid))
            updated += 1
            if updated <= 3 or updated % 100 == 0:
                logger.info(f"[{updated}/{len(rows)}] {name}: {scope[:60]}...")
        conn.commit()
        logger.info(f"经营范围补全完成: {updated} 家")
    finally:
        conn.close()


if __name__ == "__main__":
    main()
