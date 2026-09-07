"""招投标爬虫 — 搜索引擎聚合

采集字段: company_id, company_name, project_name, project_type,
          budget_amount, is_digital, bid_date, source_url, source_name

策略:
1. 使用 WebSearchEngine 搜索 "武汉 信息化 招标/采购" 等关键词
2. 从搜索结果中提取招标项目信息
3. 识别数字化项目
4. 匹配中标企业与 companies 表
5. yield BiddingItem

注意: ccgp.gov.cn 反爬严格，改用搜索引擎聚合获取招标信息
"""

import re
import logging
import psycopg2
from urllib.parse import quote_plus
from datetime import datetime, timedelta

import scrapy
from scrapy.utils.project import get_project_settings

from wuhan_it_crawler.items import BiddingItem

logger = logging.getLogger(__name__)


class BiddingSpider(scrapy.Spider):
    """武汉政府采购招投标采集 Spider — 搜索引擎聚合"""

    name = 'bidding'
    allowed_domains = []

    CCGP_SEARCH_URL = 'http://search.ccgp.gov.cn/bxsearch'

    SEARCH_KEYWORDS = [
        # 不再用泛搜索 "武汉 信息化 招标" — 这种搜索永远匹配不到具体中标企业,
        # 产生的结果全是采购网首页/通用新闻, company_id 全为 NULL.
        # 改为在 start_requests() 中按企业名搜 "公司名 中标"
    ]

    # ---- 中标噪声过滤 ----
    BID_NOISE_TITLES = ('官网入口', '欢迎', '下载', '登录', '注册', '招聘', '人才')

    # 武汉本地招投标平台域名 — 用于 source_url 标注与高亮
    LOCAL_PLATFORM_DOMAINS = [
        'ggzy.hubei.gov.cn',      # 湖北省公共资源交易中心
        'ccgp-hubei.gov.cn',      # 湖北政府采购网
        'wuhan.gov.cn',           # 武汉市政府采购
        'whggzy.com',             # 武汉公共资源交易
        'ccgp.gov.cn',            # 中国政府采购网
    ]

    DIGITAL_KEYWORDS = [
        '数字化', '信息化', '智能', 'AI', '人工智能', '云计算',
        '大数据', '智慧城市', '软件', '数据中台', '上云',
        '电子政务', '网络安全', '信息技术', '系统集成',
    ]

    # 公司名后缀 — 用于模糊匹配时剥离
    COMPANY_SUFFIXES = ['股份有限公司', '有限责任公司', '有限公司', '集团']

    custom_settings = {
        'CONCURRENT_REQUESTS': 4,
        'DOWNLOAD_DELAY': 1.0,
        'DOWNLOAD_TIMEOUT': 30,
        'RETRY_TIMES': 3,
    }

    def __init__(self, keyword=None, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.keyword_override = keyword
        self.companies_map = {}
        self.stats = {
            'search_requests': 0, 'detail_requests': 0,
            'items_yielded': 0, 'digital_count': 0, 'items_dropped': 0,
        }

        self._load_companies()

    async def start(self):
        # 按企业名搜索 "公司名 中标" — 每条结果天然关联该企业,
        # company_id 100% 匹配 (不像泛搜索 "武汉 信息化 招标" 全部 company_id=NULL)
        self.logger.info(f"启动招投标爬虫, 企业数={len(self.companies_map)}")

        try:
            import sys, os
            sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..', '..'))
            from engine.websearch import WebSearchEngine
            search_engine = WebSearchEngine()
        except ImportError:
            from engine.websearch import WebSearchEngine
            search_engine = WebSearchEngine()

        for company_name, company_id in self.companies_map.items():
            self.stats['search_requests'] += 1
            try:
                results = search_engine.search(f'{company_name} 中标', limit=8)
                for result in results:
                    title = result.get('title', '')
                    summary = result.get('summary', '')
                    url = result.get('url', '')
                    source = result.get('source', 'unknown')

                    text = f'{title} {summary}'

                    # 噪声过滤
                    if any(noise in title for noise in self.BID_NOISE_TITLES):
                        self.stats['items_dropped'] += 1
                        continue

                    # 标题/摘要须含"中标"或"成交"
                    if not any(kw in text for kw in ('中标', '成交', '预成交')):
                        self.stats['items_dropped'] += 1
                        continue

                    # 从文本中提取项目名
                    project_name = self._extract_project_name(text)
                    if not project_name:
                        project_name = title[:100]

                    # 预算提取
                    budget = self._extract_budget(text)

                    # 数字化判断
                    is_digital = self._is_digital_project(text)
                    if is_digital:
                        self.stats['digital_count'] += 1

                    # 构建并 yield Item (company_id 已知, 100% 有效)
                    item = BiddingItem()
                    item['company_id'] = company_id
                    item['project_name'] = project_name
                    item['project_type'] = '信息化' if is_digital else '其他'
                    item['budget_amount'] = budget
                    item['is_digital'] = is_digital
                    item['source_url'] = url or None
                    item['source_name'] = f'bidding_{source}'

                    self.stats['items_yielded'] += 1
                    yield item

            except Exception as e:
                self.logger.error(f"搜索失败 {company_name}: {str(e)[:80]}")

        self.logger.info(
            f"招投标爬虫结束: 搜索={self.stats['search_requests']}, "
            f"产出={self.stats['items_yielded']}, "
            f"数字化={self.stats['digital_count']}, "
            f"丢弃={self.stats['items_dropped']}"
        )

    def closed(self, reason):
        self.logger.info(
            f"招投标爬虫结束: 搜索={self.stats['search_requests']}, "
            f"产出={self.stats['items_yielded']}, "
            f"数字化={self.stats['digital_count']}, "
            f"丢弃={self.stats['items_dropped']}"
        )

    # ================================================================
    # 信息提取
    # ================================================================

    def _extract_project_name(self, text):
        """从文本中提取招标项目名称"""
        # 常见招标项目模式
        patterns = [
            r'([^\s,，。、|/<>"]{5,80}(?:采购|招标|竞争|磋商|询价|谈判)[^\s,，。、|/<>"]{0,40})',
            r'(项目名称[：:]\s*[^\s,，。]{5,80})',
        ]
        for pattern in patterns:
            m = re.search(pattern, text)
            if m:
                name = m.group(1).strip()
                # 清理
                name = re.sub(r'^[：:】\]]', '', name)
                name = re.sub(r'[【\[]', '', name)
                if 5 < len(name) < 100:
                    return name
        # 如果没匹配到模式，返回标题本身（截断）
        if len(text) > 5:
            # 截取到第一个标点
            m = re.match(r'([^\s,，。！？、]{5,80})', text)
            if m:
                return m.group(1)
        return None

    def _is_bidding_related(self, text):
        """判断文本是否与招标采购相关"""
        bidding_words = ['采购', '招标', '竞争', '磋商', '询价', '谈判',
                         '中标', '成交', '公告', '公示', '预算']
        return any(kw in text for kw in bidding_words)

    def _match_company(self, text):
        """匹配文本中提到的企业 — 精确 + 标准化名模糊 + 去前缀子串"""
        # 1) 精确全名子串匹配（最高优先）
        for name, cid in self.companies_map.items():
            if name in text:
                return cid, name
        # 2) 标准化名子串匹配（去后缀后，最短 4 字符避免误匹配）
        for name, cid in self.companies_map.items():
            short = self._normalize_name(name)
            if len(short) >= 4 and short in text:
                return cid, name
        # 3) 去地域前缀子串匹配（"武汉XX有限公司" → "XX有限公司"）
        for name, cid in self.companies_map.items():
            bare = name
            for prefix in ('武汉', '武汉市', '湖北', '湖北省'):
                if bare.startswith(prefix):
                    bare = bare[len(prefix):]
                    break
            if len(bare) >= 6 and bare in text:
                return cid, name
        return None, None

    @classmethod
    def _normalize_name(cls, name):
        """剥离公司名后缀，用于模糊匹配"""
        for suffix in cls.COMPANY_SUFFIXES:
            if name.endswith(suffix):
                return name[:-len(suffix)]
        return name

    def _check_digital(self, text):
        return any(kw in text for kw in self.DIGITAL_KEYWORDS)

    def _parse_budget(self, text):
        if not text:
            return None
        m = re.search(r'(\d+\.?\d*)\s*万', text)
        if m:
            return float(m.group(1))
        m = re.search(r'(\d+\.?\d*)\s*亿', text)
        if m:
            return float(m.group(1)) * 10000
        m = re.search(r'(\d+\.?\d*)\s*元', text)
        if m:
            return float(m.group(1)) / 10000
        return None

    def _extract_project_type(self, text):
        for kw, pt in [('竞争性谈判', '竞争性谈判'), ('单一来源', '单一来源采购'),
                       ('磋商', '竞争性磋商'), ('询价', '询价采购'),
                       ('招标', '公开招标'), ('采购', '政府采购')]:
            if kw in text:
                return pt
        return '其他'

    def _extract_date(self, text):
        if not text:
            return None
        for p in [r'(\d{4}[-/]\d{1,2}[-/]\d{1,2})', r'(\d{4}年\d{1,2}月\d{1,2}日)']:
            m = re.search(p, text)
            if m:
                return m.group(1).replace('年', '-').replace('月', '-').replace('日', '').replace('/', '-')
        return None

    def _load_companies(self):
        """从数据库读取企业列表 — 仅wuhan_it_1000名录(credit_code非空)

        通过 scrapy 参数控制:
          -a skip_crawled=0    : 全量重爬 (默认1=跳过已有招投标)
        """
        settings = get_project_settings()
        database_url = settings.get('DATABASE_URL')
        if not database_url:
            return
        skip_crawled = getattr(self, 'skip_crawled', '1') != '0'
        try:
            conn = psycopg2.connect(database_url)
            with conn.cursor() as cur:
                if skip_crawled:
                    cur.execute(
                        "SELECT c.id, c.company_name FROM companies c "
                        "WHERE c.credit_code IS NOT NULL "
                        "AND NOT EXISTS(SELECT 1 FROM bidding_records b WHERE b.company_id = c.id LIMIT 1) "
                        "ORDER BY c.id"
                    )
                else:
                    cur.execute(
                        "SELECT c.id, c.company_name FROM companies c "
                        "WHERE c.credit_code IS NOT NULL ORDER BY c.id"
                    )
                for row in cur.fetchall():
                    self.companies_map[row[1]] = row[0]
            conn.close()
            # -a limit=N: 限制企业数 (测试用)
            limit = int(getattr(self, 'limit', '0') or '0')
            if limit > 0:
                items = list(self.companies_map.items())[:limit]
                self.companies_map = dict(items)
            self.logger.info(f"加载企业映射 {len(self.companies_map)} 家 (skip_crawled={skip_crawled})")
        except Exception as e:
            self.logger.error(f"加载企业列表失败: {e}")

    def errback_request(self, failure):
        self.logger.error(f"请求失败: {failure.request.url}, error={failure.value}")
