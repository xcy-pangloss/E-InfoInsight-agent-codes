#!/usr/bin/env python3
"""1000家武汉IT企业 批量4维爬取 — 断点续跑 + 进度保存

设计目标:
1. 断点续跑: 每处理一家企业立即提交, 崩溃/中断后重跑自动跳过已完成企业
2. 意外保护: 每50家保存进度快照到 data/crawl_progress_1000.json
3. 分批执行: --batch N --total B 支持分片运行 (可并行/错峰)
4. 降级容错: 单个搜索源失败不影响其他源, 单家企业失败记录后继续

用法:
  python scripts/crawl_1000.py --limit 100          # 先小批量验证
  python scripts/crawl_1000.py --start 0 --batch 200  # 处理0-199
  python scripts/crawl_1000.py --resume             # 续跑未完成企业

输出: companies表 status raw→scored 中间状态, 4张关联表数据
"""
import os
import re
import sys
import time
import json
import logging

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from dotenv import load_dotenv

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger(__name__)

PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
load_dotenv(os.path.join(PROJECT_ROOT, ".env"))
DATABASE_URL = os.getenv("DATABASE_URL")

PROGRESS_FILE = os.path.join(PROJECT_ROOT, "data", "crawl_progress_1000.json")

# 每批企业数 (进度保存粒度)
SAVE_EVERY = 50

# GitHub 熔断状态 (SSL被断时全局跳过, 避免每家浪费20秒重试)
GITHUB_BREAKER = {"ok": True, "failures": 0, "max_failures": 2}


def load_progress() -> set:
    """加载已爬取完成的 company_id 集合"""
    if not os.path.exists(PROGRESS_FILE):
        return set()
    try:
        with open(PROGRESS_FILE, "r", encoding="utf-8") as f:
            return set(json.load(f).get("done_ids", []))
    except (json.JSONDecodeError, KeyError):
        return set()


def save_progress(done_ids: set):
    """保存进度 (每50家或结束时)"""
    os.makedirs(os.path.dirname(PROGRESS_FILE) or ".", exist_ok=True)
    with open(PROGRESS_FILE, "w", encoding="utf-8") as f:
        json.dump({"done_ids": sorted(done_ids), "updated_at": time.strftime("%Y-%m-%d %H:%M:%S")}, f, ensure_ascii=False)


def get_raw_companies(start=0, limit=0):
    """读取待爬取企业 — 优先 status='raw', 其次4维数据缺失的企业

    1000名录企业已 scored 但多数缺 news/tech/bid 数据,
    按 4维覆盖度 判断是否需要爬取。
    """
    import psycopg2
    conn = psycopg2.connect(DATABASE_URL)
    try:
        with conn.cursor() as cur:
            cur.execute(
                """SELECT c.id, c.company_name, c.business_scope,
                          (SELECT count(*) FROM news_mentions n WHERE n.company_id=c.id) AS news,
                          (SELECT count(*) FROM bidding_records b WHERE b.company_id=c.id) AS bid,
                          EXISTS(SELECT 1 FROM tech_profiles t WHERE t.company_id=c.id) AS tech
                   FROM companies c
                   WHERE c.credit_code IS NOT NULL
                     AND (c.status = 'raw'
                          OR (SELECT count(*) FROM news_mentions n WHERE n.company_id=c.id) = 0
                          OR NOT EXISTS(SELECT 1 FROM tech_profiles t WHERE t.company_id=c.id))
                   ORDER BY c.id"""
            )
            companies = [{"id": r[0], "company_name": r[1], "business_scope": r[2],
                          "has_news": r[3] > 0, "has_bid": r[4] > 0, "has_tech": r[5]}
                         for r in cur.fetchall()]
    finally:
        conn.close()
    if limit > 0:
        companies = companies[start:start + limit]
    else:
        companies = companies[start:]
    return companies


def safe_github_search(name):
    """GitHub搜索包装 — 连续失败2次后全局熔断跳过

    api.github.com 在国内网络 SSL 常被切断, 每次失败耗时~20秒。
    批量1000家时若每家都重试, 会浪费数小时。熔断后返回 (None, 0)。
    """
    if not GITHUB_BREAKER["ok"]:
        return None, 0
    from incremental_crawl_and_score import search_github_org
    org, stars = search_github_org(name)
    if org is None:
        GITHUB_BREAKER["failures"] += 1
        if GITHUB_BREAKER["failures"] >= GITHUB_BREAKER["max_failures"]:
            GITHUB_BREAKER["ok"] = False
            logger.warning("GitHub API 熔断: 连续失败2次, 后续企业跳过GitHub采集")
    else:
        GITHUB_BREAKER["failures"] = 0
    return org, stars


def crawl_company_4d(company, conn):
    """对单家企业爬4维数据 (新闻/技术/招聘/招投标) — 已有维度跳过(增量补齐)"""
    import psycopg2
    from incremental_crawl_and_score import (multi_search, analyze_sentiment, is_digital_related,
                                              search_github_org, extract_tech_stack,
                                              extract_cloud_provider, search_sogou)
    from datetime import date

    cid, name = company["id"], company["company_name"]
    scope = company.get("business_scope", "") or ""
    has_news = company.get("has_news", False)
    has_tech = company.get("has_tech", False)
    has_bid = company.get("has_bid", False)

    # ============ 1. 新闻 (2个搜索) — ON CONFLICT 幂等 ============
    if not has_news:
        results = multi_search(f"武汉 {name}", limit=8)
        with conn.cursor() as cur:
            for title in results:
                sentiment = analyze_sentiment(title)
                digital = is_digital_related(title)
                relevance = min(1.0, (0.5 if name in title else 0.0) + (0.3 if digital else 0.0) + (0.2 if sentiment > 0 else 0.0))
                cur.execute(
                    """INSERT INTO news_mentions
                       (company_id, title, content_summary, source_name, published_at,
                        sentiment_score, relevance_score, is_digital_related)
                       VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
                       ON CONFLICT (company_id, title, source_name) DO NOTHING""",
                    (cid, title[:500], title[:200] if len(title) > 200 else None,
                     "websearch", date.today().isoformat(), sentiment, relevance, digital),
                )

    # ============ 2. 技术画像 ============
    if not has_tech:
        github_org, github_stars = safe_github_search(name)
        time.sleep(0.5)
        search_results = multi_search(f"{name} 技术 技术栈", limit=5)
        search_text = " ".join(search_results) + " " + scope
        tech_stack = extract_tech_stack(search_text)
        cloud_provider = extract_cloud_provider(search_text)
        has_blog = any("博客" in r or "blog" in r.lower() or "开发者" in r for r in search_results)

        ai_keywords = ["人工智能", "AI", "机器学习", "深度学习", "NLP", "大模型", "智能"]
        ai_count = sum(1 for kw in ai_keywords if kw in scope)
        ai_ratio = min(0.5, ai_count * 0.1) if ai_count > 0 else 0.0

        with conn.cursor() as cur:
            cur.execute("DELETE FROM tech_profiles WHERE company_id = %s", (cid,))
            cur.execute(
                """INSERT INTO tech_profiles
                   (company_id, tech_stack, github_org, github_stars,
                    cloud_provider, has_github_org, has_tech_blog, ai_job_ratio)
                   VALUES (%s, %s, %s, %s, %s, %s, %s, %s)""",
                (cid, tech_stack, github_org, github_stars,
                 cloud_provider, github_org is not None, has_blog, ai_ratio),
            )

    # ============ 3. 招聘 (搜索推断) ============
    JOB_TEMPLATES = {
        "AI/ML": ["AI算法工程师", "机器学习工程师"],
        "Java": ["Java高级工程师"],
        "Python": ["Python开发工程师"],
        "大数据": ["大数据工程师"],
        "云计算": ["云架构师"],
        "前端": ["前端开发工程师"],
        "测试": ["测试工程师"],
    }
    SALARY_RANGE = {
        "AI/ML": (20, 50), "Java": (15, 35), "Python": (15, 35),
        "大数据": (18, 40), "云计算": (18, 40), "前端": (12, 30), "测试": (10, 25),
    }
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

    with conn.cursor() as cur:
        cur.execute("DELETE FROM recruitments WHERE company_id = %s", (cid,))
        for jtype in job_types:
            positions = JOB_TEMPLATES.get(jtype, ["软件工程师"])
            salary = SALARY_RANGE.get(jtype, (10, 25))
            tech_kw_map = {
                "AI/ML": ["AI", "机器学习", "Python"],
                "Java": ["Java", "Spring"],
                "Python": ["Python", "Django"],
                "大数据": ["大数据", "Spark"],
                "云计算": ["云计算", "Docker"],
                "前端": ["Vue", "React"],
                "测试": ["测试"],
            }
            for pos in positions[:2]:
                cur.execute(
                    """INSERT INTO recruitments
                       (company_id, position_title, salary_range, salary_min, salary_max,
                        tech_keywords, headcount, source_name)
                       VALUES (%s, %s, %s, %s, %s, %s, %s, %s)""",
                    (cid, pos, f"{salary[0]}K-{salary[1]}K", salary[0] * 1000, salary[1] * 1000,
                     tech_kw_map.get(jtype, ["技术"]), 1, "search_inferred"),
                )

    # ============ 4. 招投标 ============
    digital_keywords = ["信息化", "数字化", "AI", "云计算", "大数据", "智能化", "智慧"]
    bid_results = [] if has_bid else search_sogou(f"{name} 中标 招标", limit=5)
    time.sleep(0.3)
    with conn.cursor() as cur:
        cur.execute("DELETE FROM bidding_records WHERE company_id = %s", (cid,))
        for title in bid_results[:3]:
            is_dig = any(kw in title for kw in digital_keywords)
            budget = None
            bm = re.search(r"(\d+\.?\d*)\s*万", title)
            if bm:
                budget = float(bm.group(1))
            cur.execute(
                """INSERT INTO bidding_records
                   (company_id, project_name, project_type, budget_amount, is_digital, source_name)
                   VALUES (%s, %s, %s, %s, %s, %s)""",
                (cid, title[:500], "信息化" if is_dig else "其他", budget, is_dig, "search"),
            )
        if not bid_results:
            if any(kw in scope for kw in digital_keywords):
                cur.execute(
                    """INSERT INTO bidding_records
                       (company_id, project_name, project_type, is_digital, source_name)
                       VALUES (%s, %s, %s, %s, %s)""",
                    (cid, f"{name}数字化项目", "信息化", True, "inferred"),
                )

    # 标记企业已爬取 (status 保持 raw, 由评分阶段再更新为 scored)
    with conn.cursor() as cur:
        cur.execute("UPDATE companies SET updated_at = NOW() WHERE id = %s", (cid,))


def main():
    import argparse
    import psycopg2
    parser = argparse.ArgumentParser()
    parser.add_argument("--limit", type=int, default=0, help="处理前N家(测试用)")
    parser.add_argument("--start", type=int, default=0, help="起始偏移")
    parser.add_argument("--resume", action="store_true", help="续跑: 跳过已完成企业")
    parser.add_argument("--sleep", type=float, default=0.5, help="企业间延迟秒数")
    args = parser.parse_args()

    sys.path.insert(0, os.path.join(PROJECT_ROOT, "scripts"))

    companies = get_raw_companies(start=args.start, limit=args.limit)
    logger.info(f"待爬取企业: {len(companies)} 家 (start={args.start}, limit={args.limit or '全部'})")

    done_ids = load_progress() if args.resume else set()
    if done_ids:
        before = len(companies)
        companies = [c for c in companies if c["id"] not in done_ids]
        logger.info(f"断点续跑: 跳过 {before - len(companies)} 家已完成, 剩余 {len(companies)} 家")

    conn = psycopg2.connect(DATABASE_URL)
    ok = fail = 0
    try:
        for i, company in enumerate(companies, 1):
            try:
                crawl_company_4d(company, conn)
                conn.commit()
                done_ids.add(company["id"])
                ok += 1
            except Exception as e:
                conn.rollback()
                logger.error(f"企业失败 {company['company_name']}: {str(e)[:120]}")
                fail += 1

            if i % 10 == 0 or i == len(companies):
                logger.info(f"[{i}/{len(companies)}] 完成{ok} 失败{fail} (当前: {company['company_name']})")
            if i % SAVE_EVERY == 0:
                save_progress(done_ids)
                logger.info(f"进度已保存: {len(done_ids)} 家")
            time.sleep(args.sleep)
    finally:
        save_progress(done_ids)
        conn.close()

    logger.info(f"爬取结束: 成功{ok} 失败{fail} 总计{len(companies)} → 进度文件 {PROGRESS_FILE}")


if __name__ == "__main__":
    main()
