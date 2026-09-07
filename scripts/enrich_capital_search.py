#!/usr/bin/env python3
"""注册资本补全 — 搜索引擎摘要方案

用 WebSearchEngine (必应/百度/360/头条) 搜 "公司名 注册资本",
从搜索引擎缓存摘要中正则提取注册资本。无需直连企查查/天眼查,
无 IP 风控风险, 约 0.3~0.5s/家。

渠道:
  1. "公司名 注册资本" — 最直接, 摘要常含百度百科/天眼查/企查查的资本数据
  2. "公司名 工商信息" — 备选, 摘要偶含资本/法人/成立日期

仅补 registered_capital + capital_amount, COALESCE 不覆盖已有数据。

用法:
  python scripts/enrich_capital_search.py --limit 20   # 小批量测试
  python scripts/enrich_capital_search.py --resume      # 断点续跑
  python scripts/enrich_capital_search.py               # 全量
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
# 搜狗频繁403, 降为ERROR避免淹没主流程日志
logging.getLogger("engine.websearch").setLevel(logging.ERROR)

PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
load_dotenv(os.path.join(PROJECT_ROOT, ".env"))
DATABASE_URL = os.getenv("DATABASE_URL")

PROGRESS_FILE = os.path.join(PROJECT_ROOT, "data", "capital_search_progress.json")
SAVE_EVERY = 50

# ============================================================
# 正则模式 — 从搜索摘要提取注册资本
# ============================================================

CAPITAL_PATTERNS = [
    (r"注册资本[：:为约是]?\s*([\d.]+)\s*亿[元人民币]?", "亿"),
    (r"注册资本[：:为约是]?\s*([\d.]+)\s*万[元人民币]?", "万"),
    (r"注册资金[：:为约是]?\s*([\d.]+)\s*万[元人民币]?", "万"),
    (r"([\d.]+)\s*万人民币", "万"),
]


def extract_capital(text):
    """从文本提取注册资本 (float 万元)。亿→×10000。

    合理性上界 500000万(50亿): 超此多为新闻/公告中"X亿"误匹配, 非真实资本。
    武汉IT企业最大(烽火通信)仅12.9亿, 故50亿上界保留所有真实值。
    """
    if not text:
        return None
    for pat, unit in CAPITAL_PATTERNS:
        m = re.search(pat, text)
        if m:
            try:
                val = float(m.group(1))
                if unit == "亿":
                    val *= 10000
                if 0.1 <= val <= 500_000:
                    return val
            except ValueError:
                continue
    return None


# ============================================================
# 进度管理
# ============================================================

def load_progress():
    if os.path.exists(PROGRESS_FILE):
        try:
            with open(PROGRESS_FILE, encoding="utf-8") as f:
                return set(json.load(f).get("done_ids", []))
        except (json.JSONDecodeError, KeyError):
            return set()
    return set()


def save_progress(done_ids):
    os.makedirs(os.path.dirname(PROGRESS_FILE) or ".", exist_ok=True)
    with open(PROGRESS_FILE, "w", encoding="utf-8") as f:
        json.dump({"done_ids": sorted(done_ids),
                   "updated_at": datetime.now().isoformat()}, f, ensure_ascii=False)


# ============================================================
# 数据库
# ============================================================

def get_pending():
    """缺注册资本的企业列表"""
    import psycopg2
    conn = psycopg2.connect(DATABASE_URL)
    try:
        with conn.cursor() as cur:
            cur.execute("""
                SELECT id, company_name FROM companies
                WHERE credit_code IS NOT NULL
                  AND (registered_capital IS NULL OR capital_amount IS NULL)
                ORDER BY id
            """)
            return cur.fetchall()
    finally:
        conn.close()


def write_capital(cid, capital_amount, source, conn):
    """幂等UPDATE补注册资本"""
    registered_capital = f"{capital_amount:.0f}万元"
    try:
        with conn.cursor() as cur:
            cur.execute("""
                UPDATE companies SET
                    registered_capital = COALESCE(registered_capital, %s),
                    capital_amount = COALESCE(capital_amount, %s),
                    updated_at = NOW()
                WHERE id = %s
            """, (registered_capital, capital_amount, cid))
        conn.commit()
        return True
    except Exception as e:
        conn.rollback()
        logger.debug(f"写库失败 {cid}: {e}")
        return False


# ============================================================
# 主入口
# ============================================================

def main():
    import argparse
    import psycopg2
    parser = argparse.ArgumentParser()
    parser.add_argument("--limit", type=int, default=0, help="处理前N家(测试)")
    parser.add_argument("--resume", action="store_true", help="断点续跑")
    parser.add_argument("--sleep", type=float, default=0.3, help="企业间延迟(秒)")
    args = parser.parse_args()

    from engine.websearch import WebSearchEngine
    engine = WebSearchEngine()

    pending = get_pending()
    if args.limit > 0:
        pending = pending[:args.limit]

    done = load_progress() if args.resume else set()
    pending = [p for p in pending if p[0] not in done]
    logger.info(f"缺注册资本: {len(pending)} 家 (已跳过 {len(done)} 家)")

    conn = psycopg2.connect(DATABASE_URL)
    stats = {"hit": 0, "miss": 0, "written": 0}
    try:
        for i, (cid, name) in enumerate(pending, 1):
            try:
                found_capital = None
                found_source = None

                # ---- 渠道1: "公司名 注册资本" (最直接) ----
                results = engine.search(f"{name} 注册资本", limit=8)
                for r in results:
                    text = f"{r.get('title', '')} {r.get('summary', '')}"
                    cap = extract_capital(text)
                    if cap:
                        found_capital = cap
                        found_source = f"capital_search_{r.get('source', 'web')}"
                        break

                # ---- 渠道2: "公司名 工商信息" (备选) ----
                if not found_capital:
                    results2 = engine.search(f"{name} 工商信息", limit=6)
                    for r in results2:
                        text = f"{r.get('title', '')} {r.get('summary', '')}"
                        cap = extract_capital(text)
                        if cap:
                            found_capital = cap
                            found_source = f"bizinfo_search_{r.get('source', 'web')}"
                            break

                # ---- 写库 ----
                if found_capital:
                    written = write_capital(cid, found_capital, found_source, conn)
                    if written:
                        stats["written"] += 1
                    stats["hit"] += 1
                    logger.info(f"[{i}/{len(pending)}] ✓ {name[:20]}: 资本={found_capital:.0f}万 ({found_source})")
                else:
                    stats["miss"] += 1
                    logger.info(f"[{i}/{len(pending)}] ✗ {name[:20]}: 未提取到资本")

                done.add(cid)

                if i % SAVE_EVERY == 0:
                    save_progress(done)
                    logger.info(f"进度: {len(done)}家 | 命中{stats['hit']} 写入{stats['written']}")

            except KeyboardInterrupt:
                raise
            except Exception as e:
                conn.rollback()
                logger.error(f"企业失败 {name}: {str(e)[:80]}")
                stats["miss"] += 1

            time.sleep(args.sleep)
    finally:
        save_progress(done)
        conn.close()

    logger.info(f"注册资本补全完成: 命中={stats['hit']}, 写入={stats['written']}, "
                f"未命中={stats['miss']} / 总{len(pending)}")


if __name__ == "__main__":
    main()
