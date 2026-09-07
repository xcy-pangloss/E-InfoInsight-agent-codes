#!/usr/bin/env python3
"""wuhan_it_1000 多爬虫真实数据采集编排

按用户要求:
1. 仅参考源文件中公司名称 + 统一社会信用代码, 其他数据无视
2. 已爬取的企业不再重复爬取 (skip_crawled 增量模式)
3. 多爬虫真实采集: news(百度/搜狗/必应/360) + tech(官网/GitHub) +
   recruitment(BOSS/拉勾/猎聘) + bidding(政采网) + websearch(聚合)
4. 实现企业差异化呈现 (真实数据替代模板推断)

执行顺序 (串行, 避免互相干扰):
  1. websearch — 聚合搜索 (最快, 通用信息)
  2. news — 新闻舆情 (4源)
  3. tech — 技术画像
  4. recruitment — 招聘
  5. bidding — 招投标

用法:
  python scripts/crawl_multi_spiders.py [--spider news] [--limit N]
                                        [--full] [--no-proxy]

注意: 运行前先清空推断数据 (search_inferred/inferred/websearch来源),
      让真实爬虫数据占主导 (由 --purge-inferred 控制)。
"""
import os
import sys
import time
import logging
import subprocess

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger(__name__)

PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
CRAWLER_DIR = os.path.join(PROJECT_ROOT, "crawler")
VENV_PYTHON = os.path.join(PROJECT_ROOT, ".venv", "bin", "python")

# spider 执行顺序和说明
SPIDERS = [
    ("websearch", "聚合搜索(百度/搜狗/必应)"),
    ("news", "新闻舆情(百度新闻/搜狗新闻/必应新闻/360新闻)"),
    ("tech", "技术画像(官网+GitHub+云服务商)"),
    ("recruitment", "招聘(BOSS/拉勾/猎聘)"),
    ("bidding", "招投标(中国政府采购网)"),
]

# 推断数据源 (爬虫开始前清理, 让真实数据占主导)
INFERRED_SOURCES = {
    "news_mentions": ["websearch"],
    "recruitments": ["search_inferred"],
    "bidding_records": ["inferred", "search"],
}


def purge_inferred():
    """清空推断/搜索标题类数据, 保留真实爬虫数据"""
    import psycopg2
    from dotenv import load_dotenv
    load_dotenv(os.path.join(PROJECT_ROOT, ".env"))
    conn = psycopg2.connect(os.getenv("DATABASE_URL"))
    try:
        for table, sources in INFERRED_SOURCES.items():
            with conn.cursor() as cur:
                for src in sources:
                    cur.execute(f"DELETE FROM {table} WHERE source_name = %s", (src,))
                    logger.info(f"清理 {table}.source_name={src}: {cur.rowcount} 条")
        conn.commit()
    finally:
        conn.close()


def run_spider(spider, company=None, skip_crawled=True, extra_args=None):
    """运行单个 scrapy spider"""
    cmd = [
        VENV_PYTHON, "-m", "scrapy", "crawl", spider,
        "-s", "LOG_LEVEL=INFO",
        "-s", "DUPEFILTER_CLASS=scrapy.dupefilters.BaseDupeFilter",
        "-s", "ROBOTSTXT_OBEY=False",
    ]
    if company:
        cmd += ["-a", f"company={company}"]
    if skip_crawled:
        cmd += ["-a", "skip_crawled=1"]
    else:
        cmd += ["-a", "skip_crawled=0"]
    if extra_args:
        cmd += extra_args

    logger.info(f"=== 运行 spider: {spider} ===")
    start = time.time()
    result = subprocess.run(cmd, cwd=CRAWLER_DIR, capture_output=True, text=True, timeout=3600)
    elapsed = time.time() - start

    # 提取统计
    output = result.stdout + result.stderr
    for line in output.splitlines():
        if any(k in line for k in ["爬虫结束", "结束:", "产出=", "加载企业", "Traceback", "ERROR"]):
            logger.info(f"  {line.strip()}")

    if result.returncode != 0 and "Traceback" in output:
        logger.error(f"spider {spider} 异常退出 (rc={result.returncode}), 耗时{elapsed:.0f}s")
        return False
    logger.info(f"spider {spider} 完成, 耗时 {elapsed:.0f}s")
    return True


def main():
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument("--spider", choices=[s[0] for s in SPIDERS], default=None,
                        help="只运行指定spider (默认全部)")
    parser.add_argument("--company", default=None, help="只处理指定企业(调试)")
    parser.add_argument("--full", action="store_true", help="全量重爬(默认增量跳过已爬)")
    parser.add_argument("--purge-inferred", action="store_true",
                        help="运行前清空推断数据(search_inferred/inferred/websearch)")
    parser.add_argument("--limit", type=int, default=0, help="限制企业数(通过CLOSESPIDER控制, 暂未实现)")
    args = parser.parse_args()

    logger.info("=" * 60)
    logger.info("wuhan_it_1000 多爬虫真实数据采集")
    logger.info(f"模式: {'全量' if args.full else '增量'}, 企业: {args.company or '全部名录'}")
    logger.info("=" * 60)

    if args.purge_inferred:
        logger.info("--- 清理推断数据 (让真实爬虫数据占主导) ---")
        purge_inferred()

    spiders = [s for s in SPIDERS if args.spider is None or s[0] == args.spider]

    for spider, desc in spiders:
        logger.info(f"\n{'='*60}\n[{spider}] {desc}\n{'='*60}")
        ok = run_spider(spider, company=args.company, skip_crawled=not args.full)
        if not ok:
            logger.warning(f"[{spider}] 运行异常, 继续下一个")
        # spider 间间隔, 避免触发反爬
        time.sleep(3)

    logger.info("\n全部爬虫执行完毕")


if __name__ == "__main__":
    main()
