#!/usr/bin/env python3
"""企业工商信息补全 — Playwright 浏览器会话方案

低效率但有效的替代方案: 用持久化浏览器会话模拟真人访问天眼查/企查查,
从渲染后的DOM提取结构化工商数据 (注册资本/参保人数/成立日期/法人/地址)。

优势 (相比免费HTTP搜索):
- 天眼查详情页含完整工商字段 (含参保人数, 需登录)
- JS渲染后数据完整, 不依赖搜索摘要
- 持久化会话: 首次扫码登录后保存cookies, 后续免登录

工作流:
1. 首次运行: 启动有头浏览器, 弹出天眼查登录二维码, 用户扫码登录
   → cookies保存到 .browser-session/tianyancha.json
2. 后续运行: headless模式复用cookies, 直接访问详情页提取数据
3. cookies过期时自动检测, 提示重新登录

数据源优先级:
1. 天眼查详情页 (注册资本/参保人数/成立日期/法人/地址/经营范围)
2. (备) 企查查 — 如天眼查未收录

用法:
  python scripts/enrich_via_browser.py --login        # 首次: 扫码登录天眼查
  python scripts/enrich_via_browser.py --limit 10     # 用已登录会话补全10家
  python scripts/enrich_via_browser.py --resume       # 续跑
"""
import os
import re
import sys
import json
import time
import logging
import argparse
from urllib.parse import quote_plus

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from dotenv import load_dotenv

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger(__name__)

PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
load_dotenv(os.path.join(PROJECT_ROOT, ".env"))
DATABASE_URL = os.getenv("DATABASE_URL")

SESSION_DIR = os.path.join(PROJECT_ROOT, ".browser-session")
PROGRESS_FILE = os.path.join(PROJECT_ROOT, "data", "browser_enrich_progress.json")
SAVE_EVERY = 10


# ============================================================
# 正则提取 (从渲染后的页面文本提取结构化字段)
# ============================================================

def extract_fields(text):
    """从天眼查/企查查页面文本提取工商字段。
    返回 dict {registered_capital, capital_amount, employee_count,
               established_date, legal_rep, address}。"""
    result = {}

    # 注册资本 (万元)
    for pat in [
        r"注册资本[：:\s]*([\d.]+)\s*亿[元人民币]?",
        r"注册资本[：:\s]*([\d.]+)\s*万[元人民币]?",
    ]:
        m = re.search(pat, text)
        if m:
            val = float(m.group(1))
            if "亿" in m.group(0):
                val *= 10000
            if 0.1 <= val <= 500_000:
                result["registered_capital"] = f"{val:.0f}万元"
                result["capital_amount"] = val
            break

    # 参保人数 (0也为有效值: 小微企业可能无社保)
    # 企查查详情页格式 "参保人数\t20 (2025年报)" — \s 覆盖制表符
    m = re.search(r"参保人数[：:\s]*([\d,]+)", text)
    if m:
        emp = int(m.group(1).replace(",", ""))
        if 0 <= emp <= 500_000:
            result["employee_count"] = emp

    # 成立日期
    m = re.search(r"成立日期[：:\s]*(\d{4})[-/年](\d{1,2})[-/月](\d{1,2})", text)
    if m:
        result["established_date"] = f"{int(m.group(1)):04d}-{int(m.group(2)):02d}-{int(m.group(3)):02d}"

    # 法定代表人 (仅中文字符, 排除尾随数字如"15复制")
    m = re.search(r"法定代表人[：:\s]*([一-龥·]{2,10})", text)
    if m:
        result["legal_rep"] = m.group(1).strip()

    # 注册地址 (详情页"注册地址"; 搜索页"地址：" 退一步匹配)
    m = re.search(r"注册地址[：:\s]*([^\s]{5,80})", text)
    if not m:
        m = re.search(r"地\s*址[：:\s]*([^\s]{5,80})", text)
    if m:
        result["address"] = m.group(1).strip()[:120]

    return result


# ============================================================
# 天眼查浏览器会话
# ============================================================

class TianYanChaBrowser:
    """天眼查浏览器会话 — 持久化登录态"""

    SEARCH_URL = "https://www.tianyancha.com/search?key={}"

    def __init__(self, headless=True):
        from playwright.sync_api import sync_playwright
        self._pw = sync_playwright().start()
        self.headless = headless
        self.browser = self._pw.chromium.launch(
            headless=headless,
            args=['--disable-blink-features=AutomationControlled',
                  '--no-sandbox']
        )
        os.makedirs(SESSION_DIR, exist_ok=True)
        self.session_file = os.path.join(SESSION_DIR, "tianyancha_state.json")
        self.context = self.browser.new_context(
            user_agent='Mozilla/5.0 (Windows NT 10.0; Win64; x64) '
                       'AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0 Safari/537.36',
            viewport={'width': 1280, 'height': 800},
            storage_state=self.session_file if os.path.exists(self.session_file) else None,
        )
        self.page = self.context.new_page()

    def login(self):
        """有头模式扫码登录, 保存会话"""
        logger.info("打开天眼查登录页, 请扫码登录(60秒内完成)...")
        self.page.goto("https://www.tianyancha.com/login", wait_until="domcontentloaded", timeout=30000)
        # 等待登录成功 (URL变化或出现用户头像)
        try:
            self.page.wait_for_url("**/user/**", timeout=60000)
        except Exception:
            # 备选: 等待任意非login页
            time.sleep(60)
        self.context.storage_state(path=self.session_file)
        logger.info(f"登录会话已保存: {self.session_file}")

    def is_logged_in(self):
        """检测登录态是否有效"""
        try:
            self.page.goto("https://www.tianyancha.com/", wait_until="domcontentloaded", timeout=20000)
            time.sleep(2)
            content = self.page.content()
            # 登录态: 页面有"退出"或用户菜单, 无"扫码登录"
            return "扫码登录" not in content[:3000] and "login" not in self.page.url
        except Exception:
            return False

    def search_company(self, name):
        """搜索企业, 返回第一个匹配的详情页URL + 搜索结果文本块。
        返回 (detail_url, text_chunk) 或 (None, None)。"""
        try:
            self.page.goto(
                self.SEARCH_URL.format(quote_plus(name)),
                wait_until="networkidle", timeout=30000
            )
            time.sleep(3)
            body = self.page.inner_text("body")
            # 找公司名附近文本 (天眼查可能截断显示, 用名字前6字匹配)
            short = name[:6] if len(name) >= 6 else name
            idx = body.find(short)
            if idx < 0:
                # 试试去掉"有限公司"
                idx = body.find(name.replace("有限公司", "")[:8])
            if idx < 0:
                return None, None
            chunk = body[idx:idx + 600]
            # 详情页链接
            detail_url = None
            links = self.page.eval_on_selector_all(
                f'a:has-text("{short}")', 'els => els.map(e => e.href).find(h => h.includes("/company/"))'
            )
            if links:
                detail_url = links
            else:
                detail_links = self.page.eval_on_selector_all(
                    'a[href*="/company/"]', 'els => els.map(e => e.href)'
                )
                if detail_links:
                    detail_url = detail_links[0]
            return detail_url, chunk
        except Exception as e:
            logger.debug(f"搜索失败 {name}: {e}")
            return None, None

    def get_detail(self, detail_url):
        """访问详情页, 返回页面文本 (含完整工商字段)"""
        try:
            self.page.goto(detail_url, wait_until="networkidle", timeout=30000)
            time.sleep(3)
            return self.page.inner_text("body")
        except Exception as e:
            logger.debug(f"详情页失败 {detail_url}: {e}")
            return None

    def close(self):
        self.context.close()
        self.browser.close()
        self._pw.stop()


class QCChaBrowser:
    """企查查浏览器会话 — 持久化登录态"""

    def __init__(self, headless=True):
        from playwright.sync_api import sync_playwright
        self._pw = sync_playwright().start()
        self.browser = self._pw.chromium.launch(
            headless=headless,
            args=['--disable-blink-features=AutomationControlled', '--no-sandbox']
        )
        os.makedirs(SESSION_DIR, exist_ok=True)
        self.session_file = os.path.join(SESSION_DIR, "qcc_state.json")
        self.context = self.browser.new_context(
            user_agent='Mozilla/5.0 (Windows NT 10.0; Win64; x64) '
                       'AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0 Safari/537.36',
            viewport={'width': 1280, 'height': 800},
            storage_state=self.session_file if os.path.exists(self.session_file) else None,
        )
        self.page = self.context.new_page()

    def login(self):
        logger.info("打开企查查登录页, 请登录(120秒内完成)...")
        self.page.goto("https://www.qcc.com/user/login", wait_until="domcontentloaded", timeout=30000)
        time.sleep(120)
        self.context.storage_state(path=self.session_file)
        logger.info(f"企查查会话已保存: {self.session_file}")

    def search_and_extract(self, name, visit_detail=True):
        """搜索企业并提取字段。

        搜索结果页可直接获取: 注册资本/法定代表人/成立日期/地址/信用代码
        详情页额外提供: 参保人数 (搜索页无此字段)

        匹配校验: 企查查无结果时仅在头部回显查询词, 附近不含"注册资本"
        等工商字段; 真实命中卡片必含结构化字段, 据此区分避免误提。
        返回 dict。
        """
        try:
            self.page.goto(
                f'https://www.qcc.com/web/search?key={quote_plus(name)}',
                wait_until="networkidle", timeout=30000
            )
            time.sleep(4)
            body = self.page.inner_text("body")
            current_url = self.page.url

            # 风控检测: 访问限制页(inner_text为空/iframe verify页) 或 频率限制关键词
            if (not body.strip()
                    or "verify.qcc.com" in current_url
                    or "limits" in current_url):
                logger.warning(f"企查查风控(访问限制): {name}")
                return {"_blocked": True}
            top = body[:2000]
            if any(kw in top for kw in ("验证码", "滑块", "访问过于频繁", "请求过于频繁", "操作过于频繁")):
                logger.warning(f"企查查风控: {name}")
                return {"_blocked": True}

            # 定位公司名 (先全名前6字, 再退去除"有限公司"前6字)
            short = name[:6] if len(name) >= 6 else name
            idx = body.find(short)
            if idx < 0:
                idx = body.find(name.replace("有限公司", "")[:6])
            if idx < 0:
                # 名称未出现在搜索结果 → 合法"未收录", 非风控
                return {}

            chunk = body[idx:idx + 500]
            # 真实匹配信号: 卡片必须含工商字段, 否则只是头部回显查询词
            if not any(k in chunk for k in ("注册资本", "法定代表人", "成立日期", "统一社会信用代码")):
                return {}

            fields = extract_fields(chunk)

            # 详情页: 补参保人数 (搜索页无此字段)
            if visit_detail:
                try:
                    detail_links = self.page.eval_on_selector_all(
                        'a[href*="/firm/"]', 'els => els.map(e => e.href)'
                    )
                    if detail_links:
                        self.page.goto(detail_links[0], wait_until="networkidle", timeout=30000)
                        # 参保人数区块异步渲染且位置靠后, 专门等待其出现
                        # (新企业无年报时该字段不存在, 超时即放弃, 不报错)
                        try:
                            self.page.wait_for_function(
                                "() => document.body.innerText.includes('参保人数')",
                                timeout=8000,
                            )
                        except Exception:
                            pass
                        time.sleep(1)
                        detail_body = self.page.inner_text("body")
                        detail_fields = extract_fields(detail_body)
                        # 合并: 详情页找到的字段覆盖搜索页 (注意 employee_count=0
                        # 是有效值, 不能用 if v 真值判断, 否则0会被丢弃)
                        for k, v in detail_fields.items():
                            if v is not None:
                                fields[k] = v
                except Exception as e:
                    logger.debug(f"企查查详情页失败: {e}")

            return fields
        except Exception as e:
            logger.debug(f"企查查搜索失败 {name}: {e}")
            return {}

    def close(self):
        self.context.close()
        self.browser.close()
        self._pw.stop()


# ============================================================
# 数据库
# ============================================================

def get_pending():
    """缺注册资本或员工人数的企业列表 (其他字段已100%覆盖)"""
    import psycopg2
    conn = psycopg2.connect(DATABASE_URL)
    try:
        with conn.cursor() as cur:
            cur.execute("""
                SELECT id, company_name FROM companies
                WHERE credit_code IS NOT NULL
                  AND (registered_capital IS NULL
                       OR capital_amount IS NULL
                       OR employee_count IS NULL)
                ORDER BY id
            """)
            return cur.fetchall()
    finally:
        conn.close()


def write_to_db(cid, fields, conn, source=None):
    """幂等UPDATE补全字段。source标注 employee_count_source (仅QCC/天眼查浏览器数据)。"""
    if not fields:
        return 0
    try:
        with conn.cursor() as cur:
            cur.execute("""
                UPDATE companies SET
                    registered_capital = COALESCE(registered_capital, %s),
                    capital_amount = COALESCE(capital_amount, %s),
                    employee_count = COALESCE(employee_count, %s),
                    employee_count_source = COALESCE(employee_count_source, %s),
                    established_date = COALESCE(established_date, %s),
                    legal_representative = COALESCE(legal_representative, %s),
                    registered_address = COALESCE(registered_address, %s),
                    updated_at = NOW()
                WHERE id = %s
            """, (
                fields.get("registered_capital"),
                fields.get("capital_amount"),
                fields.get("employee_count"),
                source if "employee_count" in fields else None,
                fields.get("established_date"),
                fields.get("legal_rep"),
                fields.get("address"),
                cid,
            ))
        conn.commit()
        return len(fields)
    except Exception as e:
        conn.rollback()
        logger.debug(f"写库失败 {cid}: {e}")
        return 0


# ============================================================
# 进度
# ============================================================

def load_progress():
    if os.path.exists(PROGRESS_FILE):
        try:
            return set(json.load(open(PROGRESS_FILE, encoding="utf-8")).get("done_ids", []))
        except Exception:
            return set()
    return set()


def save_progress(done):
    os.makedirs(os.path.dirname(PROGRESS_FILE) or ".", exist_ok=True)
    json.dump({"done_ids": sorted(done)}, open(PROGRESS_FILE, "w", encoding="utf-8"),
              ensure_ascii=False)


# ============================================================
# 主流程
# ============================================================

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--login", action="store_true", help="首次扫码登录(默认天眼查)")
    parser.add_argument("--source", choices=["tianyancha", "qcc"], default="qcc",
                        help="数据源: tianyancha(天眼查) / qcc(企查查, 默认)")
    parser.add_argument("--limit", type=int, default=0)
    parser.add_argument("--resume", action="store_true")
    parser.add_argument("--sleep-min", type=float, default=8.0, help="随机延迟下限(秒)")
    parser.add_argument("--sleep-max", type=float, default=20.0, help="随机延迟上限(秒)")
    parser.add_argument("--detail", action="store_true", help="访问详情页(更全但更慢)")
    parser.add_argument("--no-detail", action="store_true", help="跳过详情页(仅搜索页,降风控)")
    args = parser.parse_args()

    import psycopg2
    import random

    # ---- 登录模式 ----
    if args.login:
        logger.info(f"启动有头浏览器登录 {args.source}...")
        if args.source == "qcc":
            browser = QCChaBrowser(headless=False)
            browser.login()
            browser.close()
        else:
            browser = TianYanChaBrowser(headless=False)
            browser.login()
            browser.close()
        logger.info("登录完成")
        return

    # ---- 补全模式 ----
    headless = True
    if args.source == "qcc":
        session_file = os.path.join(SESSION_DIR, "qcc_state.json")
        if not os.path.exists(session_file):
            logger.error("企查查未登录, 先运行: python scripts/enrich_via_browser.py --login --source qcc")
            return
        logger.info("使用企查查(登录态)")
    else:
        session_file = os.path.join(SESSION_DIR, "tianyancha_state.json")
        if not os.path.exists(session_file):
            logger.error("天眼查未登录, 先运行: python scripts/enrich_via_browser.py --login --source tianyancha")
            return
        logger.info("使用天眼查(登录态)")

    # 启动浏览器
    if args.source == "qcc":
        browser = QCChaBrowser(headless=True)
    else:
        browser = TianYanChaBrowser(headless=True)
        if browser.is_logged_in():
            logger.info("天眼查登录态有效")
        else:
            logger.warning("天眼查登录态过期, 重新登录")

    pending = get_pending()
    if args.limit > 0:
        pending = pending[:args.limit]
    done = load_progress() if args.resume else set()
    pending = [p for p in pending if p[0] not in done]
    visit_detail = not args.no_detail
    logger.info(f"待补全: {len(pending)} 家 (随机延迟 {args.sleep_min}-{args.sleep_max}秒, 详情页={'开' if visit_detail else '关'})")

    conn = psycopg2.connect(DATABASE_URL)
    stats = {"hit": 0, "miss": 0, "cap": 0, "emp": 0}
    consecutive_block = 0  # 连续风控计数 (仅真实风控, 不含"未收录")
    try:
        for i, (cid, name) in enumerate(pending, 1):
            try:
                # ---- 提取字段 ----
                fields = {}
                if args.source == "qcc":
                    fields = browser.search_and_extract(name, visit_detail=visit_detail)
                else:
                    detail_url, chunk = browser.search_company(name)
                    text = chunk or ""
                    if args.detail and detail_url:
                        detail_text = browser.get_detail(detail_url)
                        if detail_text:
                            text = detail_text[:3000]
                    if text:
                        fields = extract_fields(text)

                # ---- 风控检测 (特殊标记) ----
                if isinstance(fields, dict) and fields.get("_blocked"):
                    consecutive_block += 1
                    stats["miss"] += 1
                    logger.info(f"[{i}/{len(pending)}] ⚠ {name[:18]}: 风控拦截")
                    if consecutive_block >= 5:
                        pause = 600
                        logger.warning(f"连续{consecutive_block}次风控, 暂停{pause}秒...")
                        time.sleep(pause)
                        consecutive_block = 0
                    time.sleep(random.uniform(30, 60))
                    continue

                # ---- 写库 ----
                hits = write_to_db(cid, fields, conn, source=args.source)
                done.add(cid)

                if fields:
                    consecutive_block = 0
                    stats["hit"] += 1
                    if "capital_amount" in fields: stats["cap"] += 1
                    if "employee_count" in fields: stats["emp"] += 1
                    cap = fields.get("registered_capital", "—")
                    emp = fields.get("employee_count", "—")
                    logger.info(f"[{i}/{len(pending)}] ✓ {name[:18]}: 资本={cap} 参保={emp}")
                else:
                    stats["miss"] += 1
                    logger.info(f"[{i}/{len(pending)}] ✗ {name[:18]}: 未收录(跳过)")

                if i % SAVE_EVERY == 0:
                    save_progress(done)
                    logger.info(f"进度: {len(done)}家 | 命中{stats['hit']} 资本{stats['cap']} 参保{stats['emp']}")

                # ---- 随机延迟 (策略3: 模拟真人节奏) ----
                delay = random.uniform(args.sleep_min, args.sleep_max)
                logger.debug(f"等待 {delay:.1f}s")
                time.sleep(delay)

            except KeyboardInterrupt:
                raise
            except Exception as e:
                conn.rollback()
                logger.error(f"企业失败 {name}: {str(e)[:80]}")
                stats["miss"] += 1
                time.sleep(random.uniform(30, 60))
    finally:
        save_progress(done)
        conn.close()
        browser.close()

    logger.info(f"完成: 命中={stats['hit']} 资本={stats['cap']} 参保={stats['emp']} "
                f"未命中={stats['miss']} / 总{len(pending)}")


if __name__ == "__main__":
    main()
