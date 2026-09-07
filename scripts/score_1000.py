#!/usr/bin/env python3
"""1000家武汉IT企业 批量评分 + 分级DeepSeek评级

设计 (省token策略):
1. 规则引擎评分全部企业 (零token) → ratings.rated_by='rules_engine'
2. 仅对规则评分 >= 阈值 的企业调用 DeepSeek 深度评级 (省token)
   - pass_threshold 默认40 (B级以上=潜在客户)
3. 断点续跑: 评分按 company_id 幂等 (ON CONFLICT), DeepSeek 用进度文件
4. 分级调用: --top-ratio 可选只评最高分的前N% (更省token)

用法:
  python scripts/score_1000.py                 # 全部评分
  python scripts/score_1000.py --threshold 40  # 自定义达标阈值
  python scripts/score_1000.py --no-deepseek   # 只评分不调API
"""
import os
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

DEEPSEEK_PROGRESS = os.path.join(PROJECT_ROOT, "data", "deepseek_progress_1000.json")


def load_ds_progress() -> set:
    if not os.path.exists(DEEPSEEK_PROGRESS):
        return set()
    try:
        with open(DEEPSEEK_PROGRESS, "r", encoding="utf-8") as f:
            return set(json.load(f).get("done_ids", []))
    except (json.JSONDecodeError, KeyError):
        return set()


def save_ds_progress(done: set):
    os.makedirs(os.path.dirname(DEEPSEEK_PROGRESS) or ".", exist_ok=True)
    with open(DEEPSEEK_PROGRESS, "w", encoding="utf-8") as f:
        json.dump({"done_ids": sorted(done), "updated_at": time.strftime("%Y-%m-%d %H:%M:%S")}, f, ensure_ascii=False)


def score_all_rules():
    """规则引擎批量评分 (幂等, 零token)"""
    sys.path.insert(0, PROJECT_ROOT)
    from engine.rules_engine import RatingRulesEngine

    engine = RatingRulesEngine()
    stats = engine.batch_score(DATABASE_URL, mode="incremental")
    logger.info(f"规则评分完成: 总计{stats['total']}家, 通过{stats['passed']}家, 未通过{stats['failed']}家")
    return stats


def rate_top_with_deepseek(threshold=40, top_ratio=0.0, limit=0):
    """对达标企业 DeepSeek 评级 (省token)

    Args:
        threshold: 规则分达标线 (默认40 = B级以上)
        top_ratio: 只评前N%高分企业 (0=全部达标企业)
        limit: 最多评N家 (0=不限)
    """
    sys.path.insert(0, PROJECT_ROOT)
    import psycopg2
    from engine.llm_client import LLMRatingClient

    api_key = os.getenv("DEEPSEEK_API_KEY", "")
    if not api_key or api_key.startswith("your"):
        logger.error("缺少 DEEPSEEK_API_KEY, 跳过DeepSeek评级")
        return []

    conn = psycopg2.connect(DATABASE_URL)
    try:
        with conn.cursor() as cur:
            cur.execute(
                """SELECT c.id AS company_id, c.company_name, c.capital_amount, c.business_scope,
                          c.industry_tags, c.funding_stage,
                          r.total_score, r.tech_score, r.funding_score,
                          r.intent_score, r.team_score, r.industry_score,
                          tp.ai_job_ratio, tp.cloud_provider,
                          tp.has_github_org, tp.has_tech_blog
                   FROM companies c
                   JOIN ratings r ON r.company_id = c.id AND r.rated_by = 'rules_engine'
                   LEFT JOIN tech_profiles tp ON tp.company_id = c.id
                   WHERE c.status IN ('scored','raw') AND r.total_score >= %s
                   ORDER BY r.total_score DESC
                """, (threshold,))
            cols = [d[0] for d in cur.description]
            rows = [dict(zip(cols, r)) for r in cur.fetchall()]
    finally:
        conn.close()

    # top_ratio 截断: 只保留前N%高分企业
    if top_ratio > 0:
        keep = max(1, int(len(rows) * top_ratio))
        rows = rows[:keep]
        logger.info(f"top_ratio={top_ratio}: 只评前{keep}家")

    if limit > 0:
        rows = rows[:limit]

    logger.info(f"DeepSeek待评级: {len(rows)} 家 (规则分>={threshold})")

    # 断点续跑
    done = load_ds_progress()
    if done:
        before = len(rows)
        done_ids = done
        rows = [r for r in rows if r.get("company_id") not in done_ids]
        logger.info(f"断点续跑: 跳过{len(done)}家已完成, 剩余{len(rows)}家")

    if not rows:
        logger.info("无待评级企业")
        return []

    client = LLMRatingClient(api_key=api_key, max_tokens=8000, batch_size=3)
    results = client.batch_rate(rows, mode="wuhan1000")
    if results:
        client.update_database(results, DATABASE_URL)
        logger.info(f"DeepSeek评级完成: {len(results)} 家")
    return results


def report_distribution():
    """输出最终等级分布"""
    import psycopg2
    from collections import Counter
    conn = psycopg2.connect(DATABASE_URL)
    try:
        with conn.cursor() as cur:
            cur.execute(
                """SELECT COALESCE(lr.rating_level, rr.rating_level)
                   FROM companies c
                   LEFT JOIN ratings rr ON rr.company_id=c.id AND rr.rated_by='rules_engine'
                   LEFT JOIN ratings lr ON lr.company_id=c.id AND lr.rated_by='deepseek'
                   WHERE c.credit_code IS NOT NULL"""
            )
            levels = Counter(r[0] for r in cur.fetchall() if r[0])
            cur.execute(
                """SELECT COALESCE(lr.rating_level, rr.rating_level), count(*)
                   FROM companies c
                   LEFT JOIN ratings rr ON rr.company_id=c.id AND rr.rated_by='rules_engine'
                   LEFT JOIN ratings lr ON lr.company_id=c.id AND lr.rated_by='deepseek'
                   WHERE c.credit_code IS NOT NULL
                   GROUP BY 1 ORDER BY 1"""
            )
            detail = dict(cur.fetchall())
    finally:
        conn.close()
    logger.info(f"等级分布: {dict(levels)}")
    return detail


def main():
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument("--threshold", type=int, default=40, help="DeepSeek评级达标线(默认40=B级)")
    parser.add_argument("--top-ratio", type=float, default=0.0, help="只评前N%高分企业(0=全部达标)")
    parser.add_argument("--limit", type=int, default=0, help="DeepSeek最多评N家(0=不限)")
    parser.add_argument("--no-deepseek", action="store_true", help="只规则评分, 不调DeepSeek")
    args = parser.parse_args()

    logger.info("=" * 60)
    logger.info("1000家批量评分 + 分级DeepSeek评级")
    logger.info(f"阈值={args.threshold}, top_ratio={args.top_ratio}, limit={args.limit}")
    logger.info("=" * 60)

    # Step 1: 规则评分全部 (零token)
    score_all_rules()

    # Step 2: 达标企业 DeepSeek 评级 (省token)
    if not args.no_deepseek:
        rate_top_with_deepseek(threshold=args.threshold, top_ratio=args.top_ratio, limit=args.limit)

    # Step 3: 分布报告
    report_distribution()
    logger.info("全部完成")


if __name__ == "__main__":
    main()
