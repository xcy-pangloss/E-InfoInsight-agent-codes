#!/usr/bin/env python3
"""快速技术画像采集 — 替代卡死的 tech spider

问题: tech_spider 每家搜索官网+GitHub API (国内不可达, 30s×3重试=90s/家)
      1000家需25+小时, 实测卡死。

方案: WebSearchEngine 搜索 "企业名 技术 技术栈" 提取:
  - tech_stack (技术栈关键词)
  - cloud_provider (云服务商)
  - has_tech_blog (技术博客)
  - ai_job_ratio (从经营范围推断, 真实scope)
  每家约2秒, 1000家约35分钟。

写库: tech_profiles (幂等 UPSERT)
用法: python scripts/fetch_tech_fast.py [--limit N] [--resume]
"""
import os
import re
import sys
import json
import time
import logging
from datetime import datetime

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scripts"))

from dotenv import load_dotenv

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger(__name__)

PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
load_dotenv(os.path.join(PROJECT_ROOT, ".env"))
DATABASE_URL = os.getenv("DATABASE_URL")
PROGRESS_FILE = os.path.join(PROJECT_ROOT, "data", "tech_fast_progress.json")

TECH_KEYWORDS = ["Java", "Python", "Go", "C++", "React", "Vue", "Spring", "Django",
                 "Flask", "Kubernetes", "Docker", "Hadoop", "Spark", "TensorFlow",
                 "PyTorch", "Kafka", "Redis", "MySQL", "PostgreSQL", "MongoDB",
                 "微服务", "容器", "DevOps", "K8s", "大数据", "机器学习", "深度学习"]
# 经营范围 → 技术栈映射 (真实scope关键词)
SCOPE_TECH_MAP = [
    ("人工智能", ["TensorFlow", "PyTorch", "机器学习"]),
    ("机器学习", ["机器学习", "TensorFlow"]),
    ("深度学习", ["PyTorch", "深度学习"]),
    ("大数据", ["Hadoop", "Spark", "大数据"]),
    ("云计算", ["Kubernetes", "Docker", "微服务"]),
    ("云服务", ["Kubernetes", "Docker"]),
    ("数据库", ["PostgreSQL", "MySQL"]),
    ("数据治理", ["Kafka", "大数据"]),
    ("软件开发", ["Java", "Python"]),
    ("软件", ["Java", "Python"]),
    ("互联网", ["React", "Vue", "Node.js"]),
    ("网络", ["Go", "Kubernetes"]),
    ("系统集成", ["DevOps", "Docker"]),
    ("物联网", ["Go", "Python", "Kafka"]),
    ("区块链", ["Go", "Hyperledger"]),
    ("信息安全", ["安全测试", "渗透测试"]),
    ("网络安全", ["安全测试", "渗透测试"]),
]
CLOUD_KEYWORDS = ["阿里云", "腾讯云", "华为云", "AWS", "Azure", "百度云", "金山云", "天翼云"]
AI_KEYWORDS = ["人工智能", "AI", "机器学习", "深度学习", "NLP", "大模型", "智能"]


def scope_to_tech_stack(scope: str) -> list:
    """从真实经营范围映射技术栈"""
    if not scope:
        return []
    stack = []
    for kw, techs in SCOPE_TECH_MAP:
        if kw in scope:
            for t in techs:
                if t not in stack:
                    stack.append(t)
    return stack[:10]


def load_progress():
    if os.path.exists(PROGRESS_FILE):
        with open(PROGRESS_FILE) as f:
            return set(json.load(f).get("done_ids", []))
    return set()


def save_progress(done_ids):
    with open(PROGRESS_FILE, "w") as f:
        json.dump({"done_ids": sorted(done_ids), "updated_at": datetime.now().isoformat()}, f)


def fetch_tech(name: str, scope: str) -> dict:
    """搜索企业技术信息"""
    try:
        from engine.websearch import WebSearchEngine
        engine = WebSearchEngine()
        results = engine.search(f"{name} 技术 技术栈 云服务", limit=5)
        text = " ".join([f"{r.get('title','')} {r.get('summary','')}" for r in results]) + " " + (scope or "")

        # 技术栈: 优先真实经营范围映射, 搜索文本补充
        tech_stack = scope_to_tech_stack(scope or "")
        for kw in TECH_KEYWORDS:
            if kw in text and kw not in tech_stack:
                tech_stack.append(kw)
        tech_stack = tech_stack[:10]
        cloud = next((c for c in CLOUD_KEYWORDS if c in text), None)
        has_blog = any(k in text for k in ["博客", "blog", "技术博客", "开发者社区", "开源社区"])
        # AI占比: 从真实经营范围推断
        ai_count = sum(1 for kw in AI_KEYWORDS if kw in (scope or ""))
        ai_ratio = min(0.5, ai_count * 0.1) if ai_count > 0 else 0.0
        return {
            "tech_stack": tech_stack,
            "cloud_provider": cloud,
            "has_tech_blog": has_blog,
            "ai_job_ratio": ai_ratio,
        }
    except Exception as e:
        logger.debug(f"搜索技术失败: {e}")
        return {"tech_stack": scope_to_tech_stack(scope or ""), "cloud_provider": None,
                "has_tech_blog": False, "ai_job_ratio": 0.0}


def main():
    import argparse
    import psycopg2
    parser = argparse.ArgumentParser()
    parser.add_argument("--limit", type=int, default=0)
    parser.add_argument("--resume", action="store_true")
    args = parser.parse_args()

    conn = psycopg2.connect(DATABASE_URL)
    try:
        with conn.cursor() as cur:
            cur.execute(
                """SELECT c.id, c.company_name, c.business_scope
                   FROM companies c
                   WHERE c.credit_code IS NOT NULL
                   AND NOT EXISTS(SELECT 1 FROM tech_profiles t WHERE t.company_id = c.id)
                   ORDER BY c.id"""
            )
            pending = cur.fetchall()
    finally:
        pass

    done = load_progress() if args.resume else set()
    pending = [(cid, name, scope) for cid, name, scope in pending if cid not in done]
    if args.limit > 0:
        pending = pending[:args.limit]

    logger.info(f"待技术画像采集: {len(pending)} 家")

    stats = {"ok": 0, "empty": 0}
    for i, (cid, name, scope) in enumerate(pending):
        info = fetch_tech(name, scope or "")
        with conn.cursor() as cur:
            cur.execute(
                """INSERT INTO tech_profiles
                   (company_id, tech_stack, github_org, github_stars,
                    cloud_provider, has_github_org, has_tech_blog, ai_job_ratio)
                   VALUES (%s, %s, NULL, NULL, %s, FALSE, %s, %s)
                   ON CONFLICT (company_id) DO UPDATE SET
                   tech_stack = EXCLUDED.tech_stack,
                   cloud_provider = EXCLUDED.cloud_provider,
                   has_tech_blog = EXCLUDED.has_tech_blog,
                   ai_job_ratio = EXCLUDED.ai_job_ratio""",
                (cid, info["tech_stack"], info["cloud_provider"], info["has_tech_blog"], info["ai_job_ratio"]),
            )
        conn.commit()
        if info["tech_stack"] or info["cloud_provider"]:
            stats["ok"] += 1
        else:
            stats["empty"] += 1
        if (i + 1) % 20 == 0:
            save_progress(done)
            logger.info(f"[{i+1}/{len(pending)}] ok={stats['ok']}, empty={stats['empty']}")
        done.add(cid)
        time.sleep(0.3)

    save_progress(done)
    logger.info(f"完成: 有技术信息={stats['ok']}, 空={stats['empty']}")
    conn.close()


if __name__ == "__main__":
    main()
