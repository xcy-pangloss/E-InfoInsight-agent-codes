#!/usr/bin/env python3
"""补充1000名录企业注册资本 (registered_capital + capital_amount)

复用现有能力, 按优先级:
1. 百度百科 API (fetch_biz_info.fetch_baike) — 命中率约40%
2. 东方财富 F10 (fetch_biz_info.fetch_eastmoney) — 上市公司
3. WebSearchEngine 搜索 "企业名 注册资本" — 从摘要提取

断点续跑: 进度存 data/capital_progress.json, 已处理企业跳过。
写库: companies.registered_capital + capital_amount (幂等 UPDATE)。

用法: python scripts/fill_capital_1000.py [--limit N] [--resume]
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

PROGRESS_FILE = os.path.join(PROJECT_ROOT, "data", "capital_progress.json")


def load_progress():
    if os.path.exists(PROGRESS_FILE):
        with open(PROGRESS_FILE) as f:
            return set(json.load(f).get("done_ids", []))
    return set()


def save_progress(done_ids):
    with open(PROGRESS_FILE, "w") as f:
        json.dump({"done_ids": sorted(done_ids), "updated_at": datetime.now().isoformat()}, f)


def extract_capital_from_text(text: str):
    """从文本提取注册资本 (万元)"""
    if not text:
        return None
    # 万元
    m = re.search(r"注册资本[^\d]{0,10}(\d+(?:\.\d+)?)\s*万元", text)
    if m:
        return float(m.group(1))
    m = re.search(r"注册资本[^\d]{0,10}(\d+(?:\.\d+)?)\s*万(?!元)", text)
    if m:
        return float(m.group(1))
    # 亿元
    m = re.search(r"注册资本[^\d]{0,10}(\d+(?:\.\d+)?)\s*亿元", text)
    if m:
        return float(m.group(1)) * 10000
    return None


def search_capital(name: str):
    """WebSearchEngine 搜索 '企业名 注册资本' 提取"""
    try:
        from engine.websearch import WebSearchEngine
        engine = WebSearchEngine()
        results = engine.search(f"{name} 注册资本 万元", limit=5)
        for r in results:
            text = f"{r.get('title', '')} {r.get('summary', '')}"
            cap = extract_capital_from_text(text)
            if cap:
                return cap
        return None
    except Exception as e:
        logger.debug(f"搜索失败 {name}: {e}")
        return None


def main():
    import argparse
    import psycopg2
    parser = argparse.ArgumentParser()
    parser.add_argument("--limit", type=int, default=0, help="处理数量限制")
    parser.add_argument("--resume", action="store_true", help="断点续跑(跳过已处理)")
    args = parser.parse_args()

    conn = psycopg2.connect(DATABASE_URL)
    try:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT id, company_name FROM companies "
                "WHERE credit_code IS NOT NULL AND registered_capital IS NULL "
                "ORDER BY id"
            )
            pending = cur.fetchall()
    finally:
        pass

    done = load_progress() if args.resume else set()
    pending = [(cid, name) for cid, name in pending if cid not in done]
    if args.limit > 0:
        pending = pending[:args.limit]

    logger.info(f"待补充注册资本: {len(pending)} 家 (已跳过 {len(done)} 家)")

    stats = {"baike": 0, "search": 0, "miss": 0}
    for i, (cid, name) in enumerate(pending):
        capital = None
        source = None

        # 1. 百度百科
        try:
            from fetch_biz_info import fetch_baike
            info = fetch_baike(name)
            if info and info.get("capital"):
                m = re.search(r"(\d+(?:\.\d+)?)", info["capital"])
                if m:
                    capital = float(m.group(1))
                    source = "baike"
        except Exception:
            pass

        # 2. 搜索补充
        if not capital:
            capital = search_capital(name)
            if capital:
                source = "search"

        # 写库
        if capital:
            with conn.cursor() as cur:
                cur.execute(
                    "UPDATE companies SET registered_capital = %s, capital_amount = %s WHERE id = %s",
                    (f"{capital:.2f}万元" if capital == int(capital) else f"{capital}万元",
                     capital, cid),
                )
            conn.commit()
            stats[source] = stats.get(source, 0) + 1
            logger.info(f"[{i+1}/{len(pending)}] ✓ {name}: {capital}万元 (via {source})")
        else:
            stats["miss"] += 1
            logger.info(f"[{i+1}/{len(pending)}] ✗ {name}")

        done.add(cid)
        if (i + 1) % 20 == 0:
            save_progress(done)

        time.sleep(0.5)  # 防反爬

    save_progress(done)
    logger.info(f"完成: 百科={stats.get('baike',0)}, 搜索={stats.get('search',0)}, 未命中={stats['miss']}")
    conn.close()


if __name__ == "__main__":
    main()
