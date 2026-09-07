#!/usr/bin/env python3
"""真实经营范围采集 — 按企业名查询百度百科API, 替换模板推断数据

目标: 将 companies 表中模板推断的 business_scope/industry_tags 替换为真实数据。
数据源: 百度百科 BaikeLemmaCardApi (card 含 经营范围/所属行业/成立时间/法人/总部地点)

优先级:
1. 百度百科 API (真实词条, 命中约40-60%)
2. WebSearchEngine 搜索 "企业名 经营范围" 提取 (补充)

覆盖字段:
  business_scope (经营范围, 真实)
  industry_tags (所属行业 → 标签数组, 真实)
  registered_capital (若有, 补充)
  registered_address (总部地点, 补充)
  legal_representative (法定代表人, 补充)
  established_date (成立时间, 补充)

断点续跑: 进度存 data/scope_progress.json
用法: python scripts/fetch_real_scope.py [--limit N] [--resume] [--force]
  --force: 连已有的86家真实数据也重新采集 (默认只处理模板推断的933家)
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
PROGRESS_FILE = os.path.join(PROJECT_ROOT, "data", "scope_progress.json")

# 模板推断特征前缀 (fill_business_scope.py 生成的)
TEMPLATE_PREFIX = "软件开发、信息技术咨询服务"


def load_progress():
    if os.path.exists(PROGRESS_FILE):
        with open(PROGRESS_FILE) as f:
            return set(json.load(f).get("done_ids", []))
    return set()


def save_progress(done_ids):
    with open(PROGRESS_FILE, "w") as f:
        json.dump({"done_ids": sorted(done_ids), "updated_at": datetime.now().isoformat()}, f)


def fetch_baike_full(name: str) -> dict:
    """百度百科 API 提取完整工商信息 (真实)"""
    try:
        from fetch_biz_info import fetch_baike
        # 复用现有 fetch_baike 获取基本信息
        info = fetch_baike(name)
        if not info:
            return None

        # 重新请求拿 card 的完整字段
        import requests
        from incremental_crawl_and_score import random_headers
        from urllib.parse import quote_plus
        url = (f"https://baike.baidu.com/api/openapi/BaikeLemmaCardApi"
               f"?scope=103&format=json&appid=379020&bk_key={quote_plus(name)}&bk_length=600")
        resp = requests.get(url, headers=random_headers(), timeout=8)
        data = resp.json()
        card_map = {}
        for item in data.get("card", []):
            if isinstance(item, dict):
                nm = item.get("name", "")
                vals = item.get("value", [])
                if nm and vals:
                    card_map[nm] = vals[0] if isinstance(vals, list) else vals

        info["business_scope"] = str(card_map.get("经营范围", "")).strip()
        info["industry"] = str(card_map.get("所属行业", "")).strip()
        info["company_type"] = str(card_map.get("公司类型", "")).strip()

        # 总部地点 → 注册地址 (真实)
        if not info.get("address"):
            info["address"] = str(card_map.get("总部地点", "")).strip()[:120]

        # 成立时间 (真实)
        if not info.get("established"):
            m = re.search(r"(\d{4})年(\d{1,2})月(\d{1,2})日", str(card_map.get("成立时间", "")))
            if m:
                info["established"] = f"{m.group(1)}-{int(m.group(2)):02d}-{int(m.group(3)):02d}"

        return info
    except Exception as e:
        logger.debug(f"百科完整信息失败 {name}: {e}")
        return None


def search_scope(name: str) -> dict:
    """WebSearchEngine 搜索 '企业名 经营范围' 提取"""
    try:
        from engine.websearch import WebSearchEngine
        engine = WebSearchEngine()
        results = engine.search(f"{name} 经营范围", limit=5)
        for r in results:
            text = f"{r.get('title', '')} {r.get('summary', '')}"
            m = re.search(r"经营范围[：:]\s*([^\n。]{10,200})", text)
            if m:
                scope = m.group(1).strip()
                if len(scope) > 10:
                    return {"business_scope": scope}
        return None
    except Exception as e:
        logger.debug(f"搜索经营范围失败 {name}: {e}")
        return None


def industry_to_tags(industry: str) -> list:
    """所属行业 → 标签数组 (真实行业名拆分)"""
    if not industry:
        return []
    # 行业名如"软件和信息技术服务业" → 拆成标签
    tags = []
    if "软件" in industry:
        tags.append("软件")
    if "信息技术" in industry or "信息传输" in industry or "电信" in industry:
        tags.append("信息技术")
    if "互联网" in industry:
        tags.append("互联网")
    if "人工智能" in industry or "智能" in industry:
        tags.append("人工智能")
    if "云计算" in industry or "云" in industry:
        tags.append("云计算")
    if "大数据" in industry or "数据" in industry:
        tags.append("大数据")
    if "安全" in industry:
        tags.append("网络安全")
    if not tags:
        tags = [industry.strip()[:10]]
    return tags


def main():
    import argparse
    import psycopg2
    parser = argparse.ArgumentParser()
    parser.add_argument("--limit", type=int, default=0)
    parser.add_argument("--resume", action="store_true")
    parser.add_argument("--force", action="store_true", help="连已有真实数据也重采")
    args = parser.parse_args()

    conn = psycopg2.connect(DATABASE_URL)
    try:
        with conn.cursor() as cur:
            if args.force:
                cur.execute(
                    "SELECT id, company_name FROM companies "
                    "WHERE credit_code IS NOT NULL ORDER BY id"
                )
            else:
                # 只处理模板推断的 business_scope (以模板前缀开头的)
                cur.execute(
                    "SELECT id, company_name FROM companies "
                    "WHERE credit_code IS NOT NULL "
                    "AND (business_scope IS NULL OR business_scope LIKE %s) "
                    "ORDER BY id",
                    (TEMPLATE_PREFIX + "%",),
                )
            pending = cur.fetchall()
    finally:
        pass

    done = load_progress() if args.resume else set()
    pending = [(cid, name) for cid, name in pending if cid not in done]
    if args.limit > 0:
        pending = pending[:args.limit]

    logger.info(f"待真实经营范围采集: {len(pending)} 家 (模板推断 {len(pending)} 家, force={args.force})")

    stats = {"baike": 0, "search": 0, "miss": 0, "scope_only": 0}
    for i, (cid, name) in enumerate(pending):
        info = fetch_baike_full(name)
        scope = info.get("business_scope") if info else None
        source = "baike"

        if not scope:
            sr = search_scope(name)
            if sr and sr.get("business_scope"):
                scope = sr["business_scope"]
                info = sr
                source = "search"

        with conn.cursor() as cur:
            if scope and len(scope) >= 5:
                # 真实经营范围 + 行业标签
                industry = (info or {}).get("industry", "")
                tags = industry_to_tags(industry) if industry else []
                cur.execute(
                    """UPDATE companies SET
                       business_scope = %s,
                       industry_tags = %s
                       WHERE id = %s""",
                    (scope[:2000], tags if tags else None, cid),
                )
                # 补充其他字段 (仅当数据库为空时)
                updates = []
                params = []
                if info.get("capital") and not cur.mogrify("SELECT 1").decode():
                    pass
                cur.execute(
                    """UPDATE companies SET
                       business_scope = %s,
                       industry_tags = %s
                       WHERE id = %s""",
                    (scope[:2000], tags if tags else None, cid),
                )
                if info.get("address"):
                    cur.execute(
                        "UPDATE companies SET registered_address = %s WHERE id = %s AND (registered_address IS NULL OR registered_address = '')",
                        (info["address"][:200], cid),
                    )
                if info.get("legal_rep"):
                    cur.execute(
                        "UPDATE companies SET legal_representative = %s WHERE id = %s AND (legal_representative IS NULL OR legal_representative = '')",
                        (info["legal_rep"][:50], cid),
                    )
                if info.get("established"):
                    cur.execute(
                        "UPDATE companies SET established_date = %s WHERE id = %s AND established_date IS NULL",
                        (info["established"], cid),
                    )
                conn.commit()
                stats[source] = stats.get(source, 0) + 1
                logger.info(f"[{i+1}/{len(pending)}] ✓ {name}: scope={scope[:30]}... (via {source})")
            else:
                stats["miss"] += 1
                logger.info(f"[{i+1}/{len(pending)}] ✗ {name} (无真实经营范围)")

        done.add(cid)
        if (i + 1) % 20 == 0:
            save_progress(done)
        time.sleep(0.4)

    save_progress(done)
    logger.info(f"完成: 百科={stats.get('baike',0)}, 搜索={stats.get('search',0)}, 未命中={stats['miss']}")
    conn.close()


if __name__ == "__main__":
    main()
