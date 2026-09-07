#!/usr/bin/env python3
"""企业工商信息补全脚本 — 多渠道聚合补全注册资本/员工人数/招聘信息

替代 fill_capital_1000.py + fetch_biz_info.py 在 1000 名录场景的补全用途。
核心改进: 不再只依赖百度百科(小微企业命中率~0%), 而是通过 WebSearchEngine
聚合 天眼查/企查查/爱企查 的搜索摘要, 从摘要中正则提取:

  - 注册资本 (registered_capital + capital_amount)
  - 参保人数/人员规模 (employee_count — 新字段, 企业规模最佳代理指标)
  - 成立日期 (established_date)
  - 真实招聘岗位 (recruitments, source_name='boss_search' 等, 取代 inferred)

渠道优先级 (逐级 fallback, 已验证有效渠道):
  1. 百度百科 API (fetch_baike) — 已知企业命中率高 (资本/成立日期)
  2. 参保人数直接搜索 (social_insurance_search) — 大企业搜狗摘要偶现
  3. 天眼查搜索摘要 (tianyancha_search) — 偶尔命中

招聘信息渠道 (验证有效):
  1. BOSS直聘搜索摘要 (boss_search)
  2. 猎聘搜索摘要 (liepin_search)
  3. 拉勾搜索摘要 (lagou_search)

注: 企查查/爱企查/天眼查详情页需登录或JS渲染, 免费HTTP无法获取小微企业
注册资本/参保人数。完整补全需付费企业信息API (天眼查/企查查企业版)。
本脚本聚焦可免费获取的真实招聘岗位 + 大企业参保人数。

断点续跑: 进度存 data/enrich_progress.json, 已处理企业跳过。
写库: UPDATE companies (幂等) + INSERT recruitments (ON CONFLICT DO NOTHING)。

用法:
  python scripts/enrich_company_biz.py --limit 20   # 小批量验证命中率
  python scripts/enrich_company_biz.py --resume      # 续跑未完成
  python scripts/enrich_company_biz.py               # 全量
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

PROGRESS_FILE = os.path.join(PROJECT_ROOT, "data", "enrich_progress.json")
SAVE_EVERY = 50

# ============================================================
# 正则模式 — 从搜索摘要提取工商字段 (核心新能力)
# ============================================================

# 注册资本 (返回 float, 万元)
CAPITAL_PATTERNS = [
    (r"注册资本[：:为约是]?\s*([\d.]+)\s*亿[元人民币]?", "亿"),
    (r"注册资本[：:为约是]?\s*([\d.]+)\s*万[元人民币]?", "万"),
    (r"注册资金[：:为约是]?\s*([\d.]+)\s*万[元人民币]?", "万"),
    (r"([\d.]+)\s*万人民币", "万"),
]
# 员工人数 (返回 int)
EMPLOYEE_PATTERNS = [
    r"参保人数[：:为是]?\s*(\d+)\s*人?",
    r"社保参保人数[：:为是]?\s*(\d+)",
    r"社保人数[：:为是]?\s*(\d+)",
    r"人员规模[：:为是]?\s*(\d+)[\s~\-—到至]+(\d+)\s*人",   # 范围 → 取高
    r"员工[人数]?[：:为是]?\s*(\d+)\s*人",
    r"(\d+)[\s~\-—到至]+(\d+)\s*人",                        # "50-100人"
]
# 成立日期
ESTABLISHED_PATTERNS = [
    r"成立日期[：:为是]?\s*(\d{4})[-/年](\d{1,2})[-/月](\d{1,2})",
    r"成立于[：:为是]?\s*(\d{4})[-/年](\d{1,2})[-/月](\d{1,2})",
]

# ============================================================
# 招聘岗位提取 (复用 recruitment_spider 的正则, 此处独立定义避免依赖 Scrapy)
# ============================================================

JOB_PATTERNS = [
    r"((?:高级|资深|初级|中级)?(?:前端|后端|全栈|Java|Python|Go|C\+\+|AI|算法|数据|运维|测试|产品|UI|交互|安全|架构|技术|开发|软件|系统|数据库|网络|云|大数据|人工智能|深度学习|机器学习|NLP)[^\s,，、|/]{0,20}(?:工程师|开发|专家|经理|主管|负责人|架构师|分析师|设计师|专员))",
    r"招聘[：:]\s*([^\s,，、|/]{2,30}(?:工程师|开发|专家|经理|主管|架构师|分析师|设计师|专员))",
    r"([^\s,，、|/]{2,20}(?:工程师|开发|专家|架构师|分析师|设计师|专员))\s*招聘",
]

SALARY_K = r"(\d+)[Kk]?[-~—到至](\d+)[Kk]"
SALARY_WAN = r"(\d+\.?\d*)[-~—到至](\d+\.?\d*)万"
SALARY_NUM = r"(\d{4,})[-~—到至](\d{4,})"

TECH_KW_LIST = [
    "Python", "Java", "Go", "C++", "JavaScript", "TypeScript", "Vue", "React",
    "Spring", "Django", "Flask", "FastAPI", "Node", "AI", "机器学习", "深度学习",
    "NLP", "大模型", "计算机视觉", "算法", "大数据", "Hadoop", "Spark", "Flink",
    "云计算", "Docker", "Kubernetes", "微服务", "MySQL", "PostgreSQL", "Redis",
    "MongoDB", "网络安全", "信创", "区块链", "物联网", "IoT", "5G", "DevOps",
    "Linux", "自动化测试", "前端", "后端", "全栈", "数据仓库", "数据中台",
]


def extract_position(text):
    for pat in JOB_PATTERNS:
        m = re.search(pat, text)
        if m:
            title = re.sub(r"[【】\[\]()]", "", m.group(1)).strip()
            if any(b in title for b in ["招聘信息", "招聘官网", "招聘职位", "企业招聘"]):
                continue
            if 4 <= len(title) <= 30:
                return title
    return None


def parse_salary(text):
    if not text:
        return None, None, None
    m = re.search(SALARY_K, text)
    if m:
        s = f"{m.group(1)}K-{m.group(2)}K"
        return s, int(m.group(1)) * 1000, int(m.group(2)) * 1000
    m = re.search(SALARY_WAN, text)
    if m:
        s = f"{m.group(1)}-{m.group(2)}万"
        return s, int(float(m.group(1)) * 10000), int(float(m.group(2)) * 10000)
    m = re.search(SALARY_NUM, text)
    if m:
        lo, hi = int(m.group(1)), int(m.group(2))
        if lo > 100000:
            return f"{m.group(1)}-{m.group(2)}", round(lo / 12), round(hi / 12)
        return f"{m.group(1)}-{m.group(2)}", lo, hi
    return None, None, None


def extract_tech(text):
    tl = text.lower()
    return [kw for kw in TECH_KW_LIST if kw.lower() in tl][:5]


# ============================================================
# 提取工具
# ============================================================

def extract_capital(text):
    """从文本提取注册资本 (float 万元)。亿→×10000。"""
    if not text:
        return None
    for pat, unit in CAPITAL_PATTERNS:
        m = re.search(pat, text)
        if m:
            try:
                val = float(m.group(1))
                if unit == "亿":
                    val *= 10000
                # 合理性校验: 0.1万 ~ 1000亿
                if 0.1 <= val <= 10_000_000:
                    return val
            except ValueError:
                continue
    return None


def extract_employee(text):
    """从文本提取员工人数 (int)。范围取上限。"""
    if not text:
        return None
    for pat in EMPLOYEE_PATTERNS:
        m = re.search(pat, text)
        if m:
            groups = m.groups()
            # 范围模式 (2组) 取高; 单值模式取该值
            val = int(groups[-1]) if len(groups) > 1 and groups[-1] else int(groups[0])
            if 1 <= val <= 500_000:
                return val
    return None


def extract_established(text):
    """从文本提取成立日期 'YYYY-MM-DD'。"""
    if not text:
        return None
    for pat in ESTABLISHED_PATTERNS:
        m = re.search(pat, text)
        if m:
            try:
                return f"{int(m.group(1)):04d}-{int(m.group(2)):02d}-{int(m.group(3)):02d}"
            except (ValueError, IndexError):
                continue
    return None


def search_snippets(engine, query, limit=8):
    """WebSearchEngine.search → 拼接 title+summary 文本列表"""
    try:
        results = engine.search(query, limit=limit)
        return [f"{r.get('title', '')} {r.get('summary', '')}" for r in results]
    except Exception as e:
        logger.debug(f"搜索失败 [{query[:30]}]: {e}")
        return []


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

def ensure_columns(conn):
    """幂等添加 employee_count 列 (兼容已存在的库)"""
    with conn.cursor() as cur:
        cur.execute("ALTER TABLE companies ADD COLUMN IF NOT EXISTS employee_count INTEGER")
        cur.execute("ALTER TABLE companies ADD COLUMN IF NOT EXISTS employee_count_source VARCHAR(50)")
    conn.commit()


def get_pending_companies():
    """读取待补全企业: 缺 注册资本/员工人数/真实招聘 任一项"""
    import psycopg2
    conn = psycopg2.connect(DATABASE_URL)
    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT c.id, c.company_name, c.business_scope
                FROM companies c
                WHERE c.credit_code IS NOT NULL
                  AND (c.registered_capital IS NULL
                       OR c.capital_amount IS NULL
                       OR c.employee_count IS NULL
                       OR NOT EXISTS (SELECT 1 FROM recruitments r
                                      WHERE r.company_id = c.id
                                        AND r.source_name NOT LIKE '%inferred%'))
                ORDER BY c.id
                """
            )
            return [(r[0], r[1], r[2]) for r in cur.fetchall()]
    finally:
        conn.close()


def enrich_company(engine, cid, name, scope, conn):
    """对单家企业执行多渠道补全。返回各字段命中来源 dict。"""
    result = {"capital": None, "capital_src": None, "employee": None, "emp_src": None,
              "established": None, "jobs": 0}

    # ---------- 1. 百度百科 ----------
    try:
        from fetch_biz_info import fetch_baike
        info = fetch_baike(name)
        if info:
            if info.get("capital"):
                cap = extract_capital(info.get("capital", ""))
                if cap:
                    result["capital"] = cap
                    result["capital_src"] = "baike"
            if not result["established"] and info.get("established"):
                est = extract_established(info.get("established", ""))
                if est:
                    result["established"] = est
    except Exception as e:
        logger.debug(f"baike失败 {name}: {e}")

    # ---------- 2. 参保人数直接搜索 (验证有效, 前置) ----------
    if not result["employee"]:
        snippets = search_snippets(engine, f"{name} 参保人数 社保", limit=8)
        combined = " ".join(snippets)
        emp = extract_employee(combined)
        if emp:
            result["employee"] = emp
            result["emp_src"] = "social_insurance_search"
        if not result["capital"]:
            cap = extract_capital(combined)
            if cap:
                result["capital"] = cap
                result["capital_src"] = "social_insurance_search"
        if not result["established"]:
            est = extract_established(combined)
            if est:
                result["established"] = est
        time.sleep(0.3)

    # ---------- 3. 天眼查搜索 (偶尔命中) ----------
    if not result["capital"] or not result["employee"]:
        snippets = search_snippets(engine, f"{name} 天眼查", limit=6)
        combined = " ".join(snippets)
        if not result["capital"]:
            cap = extract_capital(combined)
            if cap:
                result["capital"] = cap
                result["capital_src"] = "tianyancha_search"
        if not result["employee"]:
            emp = extract_employee(combined)
            if emp:
                result["employee"] = emp
                result["emp_src"] = "tianyancha_search"
        time.sleep(0.3)

    # ---------- 6. 招聘信息: BOSS/猎聘/拉勾 ----------
    recruit_channels = [
        ("boss_search", "BOSS直聘 招聘"),
        ("liepin_search", "猎聘 招聘"),
        ("lagou_search", "拉勾 招聘"),
    ]
    real_jobs = []  # (position, salary_range, salary_min, salary_max, tech, url, src)
    for src, suffix in recruit_channels:
        results = engine.search(f"{name} {suffix}", limit=8)
        for r in results:
            text = f"{r.get('title', '')} {r.get('summary', '')}"
            pos = extract_position(text)
            if pos:
                sal_range, sal_min, sal_max = parse_salary(text)
                tech = extract_tech(text)
                real_jobs.append((pos, sal_range, sal_min, sal_max, tech,
                                  r.get("url", ""), src))
        time.sleep(0.3)
    result["jobs"] = len(real_jobs)

    # ---------- 写库 ----------
    with conn.cursor() as cur:
        # UPDATE companies
        cur.execute(
            """UPDATE companies SET
                   registered_capital = COALESCE(registered_capital, %s),
                   capital_amount = COALESCE(capital_amount, %s),
                   employee_count = COALESCE(employee_count, %s),
                   employee_count_source = COALESCE(employee_count_source, %s),
                   established_date = COALESCE(established_date, %s),
                   updated_at = NOW()
               WHERE id = %s""",
            (f"{result['capital']:.0f}万元" if result["capital"] else None,
             result["capital"], result["employee"], result["emp_src"],
             result["established"], cid),
        )
        # INSERT recruitments (去重 by position+source)
        seen_pos = set()
        for pos, sal_range, sal_min, sal_max, tech, url, src in real_jobs:
            key = (pos, src)
            if key in seen_pos:
                continue
            seen_pos.add(key)
            cur.execute(
                """INSERT INTO recruitments
                   (company_id, position_title, salary_range, salary_min, salary_max,
                    tech_keywords, headcount, source_url, source_name)
                   VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
                   ON CONFLICT (company_id, position_title, source_name) DO NOTHING""",
                (cid, pos, sal_range, sal_min, sal_max, tech, 1, url or None, src),
            )
    conn.commit()
    return result


# ============================================================
# 主入口
# ============================================================

def baike_enrich_one(cid, name, conn):
    """baike-only: 仅百度百科补全单家企业。返回命中字段数。"""
    try:
        from fetch_biz_info import fetch_baike
        info = fetch_baike(name)
        if not info:
            return 0
        cap_text = info.get("capital", "") or ""
        capital_amount = None
        registered_capital = None
        if cap_text:
            m = re.search(r"([\d.]+)\s*亿", cap_text)
            if m:
                capital_amount = float(m.group(1)) * 10000
            else:
                m = re.search(r"([\d.]+)\s*万", cap_text)
                if m:
                    capital_amount = float(m.group(1))
            if capital_amount and 0.1 <= capital_amount <= 10_000_000:
                registered_capital = f"{capital_amount:.0f}万元"
        established = info.get("established", "") or ""
        legal_rep = info.get("legal_rep", "") or ""
        address = info.get("address", "") or ""
        employee = info.get("employee_count")

        with conn.cursor() as cur:
            cur.execute(
                """UPDATE companies SET
                       registered_capital = COALESCE(registered_capital, %s),
                       capital_amount = COALESCE(capital_amount, %s),
                       established_date = COALESCE(established_date, %s),
                       legal_representative = COALESCE(legal_representative, %s),
                       registered_address = COALESCE(registered_address, %s),
                       employee_count = COALESCE(employee_count, %s),
                       employee_count_source = COALESCE(employee_count_source, %s),
                       updated_at = NOW()
                   WHERE id = %s""",
                (registered_capital, capital_amount, established or None,
                 legal_rep or None, address or None, employee,
                 'baike' if employee else None, cid),
            )
        conn.commit()
        hits = sum(1 for v in (registered_capital, established, legal_rep, address, employee) if v)
        return hits
    except Exception as e:
        conn.rollback()
        logger.debug(f"baike补全失败 {name}: {e}")
        return 0


def main():
    import argparse
    import psycopg2
    parser = argparse.ArgumentParser()
    parser.add_argument("--limit", type=int, default=0, help="处理前N家(测试)")
    parser.add_argument("--resume", action="store_true", help="断点续跑")
    parser.add_argument("--sleep", type=float, default=0.5, help="企业间延迟")
    parser.add_argument("--baike-only", action="store_true",
                        help="快速模式: 仅百度百科API, 不搜招聘/参保(约0.2s/家)")
    args = parser.parse_args()

    from engine.websearch import WebSearchEngine
    engine = WebSearchEngine()

    conn = psycopg2.connect(DATABASE_URL)
    ensure_columns(conn)
    conn.close()

    pending = get_pending_companies()
    if args.limit > 0:
        pending = pending[:args.limit]

    done = load_progress() if args.resume else set()
    pending = [(cid, n, s) for cid, n, s in pending if cid not in done]
    logger.info(f"待补全企业: {len(pending)} 家 (已跳过 {len(done)} 家)")

    # ---- baike-only 快速模式: 仅百度百科, 不启动 WebSearchEngine ----
    if args.baike_only:
        import psycopg2
        conn = psycopg2.connect(DATABASE_URL)
        stats = {"cap": 0, "emp": 0, "any_hit": 0}
        try:
            for i, (cid, name, scope) in enumerate(pending, 1):
                hits = baike_enrich_one(cid, name, conn)
                done.add(cid)
                if hits:
                    stats["any_hit"] += 1
                    tag = "✓"
                else:
                    tag = " "
                logger.info(f"[{i}/{len(pending)}] {tag} {name[:20]}: baike命中{hits}字段")
                if i % 100 == 0:
                    save_progress(done)
                    logger.info(f"进度: {len(done)}家, 命中{stats['any_hit']}")
                time.sleep(0.15)  # 百度百科API限速
        finally:
            save_progress(done)
            conn.close()
        logger.info(f"百度百科补全完成: 命中={stats['any_hit']}/{len(pending)}")
        return

    conn = psycopg2.connect(DATABASE_URL)
    stats = {"capital_hit": 0, "employee_hit": 0, "jobs_total": 0, "miss": 0}
    try:
        for i, (cid, name, scope) in enumerate(pending, 1):
            try:
                r = enrich_company(engine, cid, name, scope, conn)
                done.add(cid)
                cap_str = f"{r['capital']:.0f}万({r['capital_src']})" if r["capital"] else "—"
                emp_str = f"{r['employee']}({r['emp_src']})" if r["employee"] else "—"
                logger.info(f"[{i}/{len(pending)}] {name[:18]}: 资本={cap_str} 员工={emp_str} 招聘={r['jobs']}岗")
                if r["capital"]:
                    stats["capital_hit"] += 1
                if r["employee"]:
                    stats["employee_hit"] += 1
                stats["jobs_total"] += r["jobs"]
                if not r["capital"] and not r["employee"] and r["jobs"] == 0:
                    stats["miss"] += 1
            except Exception as e:
                conn.rollback()
                logger.error(f"企业失败 {name}: {str(e)[:100]}")
                stats["miss"] += 1

            if i % SAVE_EVERY == 0:
                save_progress(done)
                logger.info(f"进度保存: {len(done)} 家 | 命中: 资本{stats['capital_hit']} 员工{stats['employee_hit']} 招聘{stats['jobs_total']}岗")
            time.sleep(args.sleep)
    finally:
        save_progress(done)
        conn.close()

    logger.info(f"补全完成: 资本命中={stats['capital_hit']}, 员工命中={stats['employee_hit']}, "
                f"招聘岗位={stats['jobs_total']}, 全空={stats['miss']} / 总{len(pending)}")


if __name__ == "__main__":
    main()
