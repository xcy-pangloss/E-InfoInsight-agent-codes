#!/usr/bin/env python3
"""重算 tech_profiles.ai_job_ratio + recruitments 岗位 — 基于补全后的 business_scope

背景: 爬取时 business_scope 为空, 导致:
1. ai_job_ratio 按空 scope 计算 = 0
2. 招聘岗位推断 job_types 走了默认分支 (仅Java/Python), team分低

本脚本不重新搜索, 直接用数据库现有 business_scope 重算这两个维度。
用法: python scripts/refine_tech_recruit.py [--limit N]
"""
import os
import sys
import logging

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from dotenv import load_dotenv

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger(__name__)

PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
load_dotenv(os.path.join(PROJECT_ROOT, ".env"))
DATABASE_URL = os.getenv("DATABASE_URL")

JOB_TEMPLATES = {
    "AI/ML": ["AI算法工程师", "机器学习工程师", "NLP工程师", "深度学习工程师"],
    "Java": ["Java高级工程师", "Java架构师"],
    "Python": ["Python开发工程师", "数据分析工程师"],
    "大数据": ["大数据工程师", "数据开发工程师"],
    "云计算": ["云架构师", "DevOps工程师"],
    "前端": ["前端开发工程师", "Vue开发工程师"],
    "测试": ["测试工程师", "自动化测试工程师"],
}
SALARY_RANGE = {
    "AI/ML": (20, 50), "Java": (15, 35), "Python": (15, 35),
    "大数据": (18, 40), "云计算": (18, 40), "前端": (12, 30), "测试": (10, 25),
}
TECH_KW = {
    "AI/ML": ["AI", "机器学习", "Python"],
    "Java": ["Java", "Spring"],
    "Python": ["Python", "Django"],
    "大数据": ["大数据", "Spark"],
    "云计算": ["云计算", "Docker"],
    "前端": ["Vue", "React"],
    "测试": ["测试"],
}
AI_KEYWORDS = ["人工智能", "AI", "机器学习", "深度学习", "NLP", "大模型", "智能"]


def infer_job_types(scope: str) -> list:
    job_types = []
    if any(kw in scope for kw in ["人工智能", "AI", "机器学习", "深度学习", "NLP", "大模型"]):
        job_types.append("AI/ML")
    if any(kw in scope for kw in ["软件", "Java", "信息系统"]):
        job_types.append("Java")
    if any(kw in scope for kw in ["软件", "Python", "数据"]):
        job_types.append("Python")
    if any(kw in scope for kw in ["大数据", "数据"]):
        job_types.append("大数据")
    if any(kw in scope for kw in ["云计算", "云", "微服务"]):
        job_types.append("云计算")
    if any(kw in scope for kw in ["互联网", "软件"]):
        job_types.append("前端")
    if any(kw in scope for kw in ["软件", "信息技术"]):
        job_types.append("测试")
    if not job_types:
        job_types = ["Java", "Python"]
    return job_types


def main():
    import argparse
    import psycopg2
    parser = argparse.ArgumentParser()
    parser.add_argument("--limit", type=int, default=0)
    args = parser.parse_args()

    conn = psycopg2.connect(DATABASE_URL)
    try:
        with conn.cursor() as cur:
            cur.execute("""SELECT c.id, c.company_name, c.business_scope
                           FROM companies c WHERE c.credit_code IS NOT NULL
                           AND c.business_scope IS NOT NULL ORDER BY c.id""")
            rows = cur.fetchall()
        if args.limit > 0:
            rows = rows[:args.limit]

        for i, (cid, name, scope) in enumerate(rows, 1):
            scope = scope or ""

            # 1. 重算 ai_job_ratio
            ai_count = sum(1 for kw in AI_KEYWORDS if kw in scope)
            ai_ratio = min(0.5, ai_count * 0.1) if ai_count > 0 else 0.0
            with conn.cursor() as cur:
                cur.execute("UPDATE tech_profiles SET ai_job_ratio = %s WHERE company_id = %s", (ai_ratio, cid))

            # 2. 重推招聘岗位
            job_types = infer_job_types(scope)
            with conn.cursor() as cur:
                cur.execute("DELETE FROM recruitments WHERE company_id = %s", (cid,))
                for jtype in job_types:
                    salary = SALARY_RANGE.get(jtype, (10, 25))
                    for pos in JOB_TEMPLATES.get(jtype, ["软件工程师"])[:2]:
                        cur.execute(
                            """INSERT INTO recruitments
                               (company_id, position_title, salary_range, salary_min, salary_max,
                                tech_keywords, headcount, source_name)
                               VALUES (%s, %s, %s, %s, %s, %s, %s, %s)""",
                            (cid, pos, f"{salary[0]}K-{salary[1]}K", salary[0] * 1000, salary[1] * 1000,
                             TECH_KW.get(jtype, ["技术"]), 1, "search_inferred"),
                        )

            if i % 100 == 0 or i == len(rows):
                logger.info(f"[{i}/{len(rows)}] 重算完成 (当前: {name}, 岗位: {job_types}, ai_ratio: {ai_ratio})")
        conn.commit()
        logger.info(f"重算完成: {len(rows)} 家")
    finally:
        conn.close()


if __name__ == "__main__":
    main()
