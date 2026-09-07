"""招聘信息爬虫 — 搜索引擎聚合 + 多招聘站点

采集字段: company_id, company_name, position_title, salary_range,
          salary_min, salary_max, tech_keywords, headcount, source_url, source_name

策略:
1. 多招聘站点搜索: 通用("武汉 企业名 招聘") + BOSS直聘 + 猎聘 + 拉勾 + 智联
2. 从搜索结果中提取岗位名称、薪资、技术关键词
3. 每站点独立标注来源(source_name), 实现多源覆盖
4. headcount = 该企业真实岗位去重计数 (替代恒1)
5. 招聘数量推断企业规模 → 写 companies.employee_count
6. 聚合页回退: 仍按企业名特征推断通用岗位 (标注 inferred)

注意: 由于招聘网站反爬严格，改用搜索引擎聚合方式获取公开招聘信息
"""

import re
import logging
import psycopg2
from urllib.parse import quote_plus

import scrapy
from scrapy.utils.project import get_project_settings

from wuhan_it_crawler.items import RecruitmentItem

logger = logging.getLogger(__name__)


class RecruitmentSpider(scrapy.Spider):
    """武汉IT企业招聘信息采集 Spider — 搜索引擎聚合"""

    name = 'recruitment'
    allowed_domains = []

    # ---- 技术关键词库 ----
    TECH_KEYWORDS_LIST = [
        'Python', 'Java', 'Go', 'C++', 'C#', 'JavaScript', 'TypeScript',
        'React', 'Vue', 'Angular', 'Spring', 'Django', 'Flask', 'FastAPI',
        'Node.js', 'PHP', 'Ruby', 'Rust', 'Swift', 'Kotlin',
        'AI', '人工智能', '机器学习', '深度学习', 'NLP', '大模型', 'LLM',
        '计算机视觉', 'CV', 'AIGC', '生成式', '智能体', 'Agent',
        '云计算', 'Docker', 'Kubernetes', 'DevOps', '云原生', '容器', 'Serverless',
        '大数据', 'Spark', 'Hadoop', 'Flink', 'Kafka', '数据中台', '数据仓库',
        'SQL', 'MySQL', 'Redis', 'MongoDB', 'PostgreSQL', 'Elasticsearch', 'TiDB',
        '算法', '数据挖掘', '数据分析',
        '物联网', 'IoT', '嵌入式', '边缘计算',
        '网络安全', '信息安全', '等保', '渗透测试',
        '信创', '鸿蒙', '麒麟', '国产化', '统信',
        '区块链', 'Web3', '智能合约',
        '低代码', '无代码',
        'Android', 'iOS', 'Flutter', 'React Native', '小程序',
        '测试', '自动化测试', 'QA', '运维', 'SRE', 'DBA',
    ]

    # ---- 招聘相关关键词 ----
    JOB_INDICATORS = [
        '招聘', '招人', '岗位', '职位', '工程师', '开发', '程序员',
        '薪资', '月薪', '年薪', 'K', '万', '实习',
    ]

    # ---- C1: 多招聘站点渠道 (站点后缀 → source_name) ----
    RECRUIT_CHANNELS = [
        ('BOSS直聘 招聘', 'boss_search'),
        ('猎聘 招聘', 'liepin_search'),
        ('拉勾 招聘', 'lagou_search'),
        ('智联 招聘', 'zhilian_search'),
    ]

    # ---- C3: 招聘数量 → 企业规模推断阈值 ----
    SIZE_THRESHOLDS = {
        # (min_distinct_positions, employee_estimate)
        10: (200, 'recruit_inferred_large'),
        5: (100, 'recruit_inferred_mid'),
        1: (30, 'recruit_inferred_small'),
    }

    custom_settings = {
        'CONCURRENT_REQUESTS_PER_DOMAIN': 2,
        'DOWNLOAD_DELAY': 1.0,
        'DOWNLOAD_TIMEOUT': 30,
        'RETRY_TIMES': 3,
    }

    def __init__(self, company=None, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.company_override = company
        self.companies = []
        self.stats = {
            'search_requests': 0,
            'items_yielded': 0,
            'items_dropped': 0,
        }
        self._prev_yielded = 0

        self._load_companies()

    async def start(self):
        """根据企业列表生成搜索请求"""
        if not self.companies:
            self.logger.warning("未找到企业，跳过")
            return

        self.logger.info(f"启动招聘爬虫, 企业数={len(self.companies)}")

        # 使用 WebSearchEngine 搜索招聘信息
        try:
            from engine.websearch import WebSearchEngine
            search_engine = WebSearchEngine()
        except ImportError:
            import sys
            import os
            sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..', '..'))
            from engine.websearch import WebSearchEngine
            search_engine = WebSearchEngine()

        for company in self.companies:
            name = company['company_name']
            company_id = company['id']
            company_positions = set()  # C2: 去重计数 (position_title, source_name)

            # --- 通用搜索 (原有逻辑) ---
            query = f'武汉 {name} 招聘'
            self.stats['search_requests'] += 1
            job_related_hits = 0

            try:
                results = search_engine.search(query, limit=10)
                for result in results:
                    title = result.get('title', '')
                    summary = result.get('summary', '')
                    url = result.get('url', '')
                    source = result.get('source', 'unknown')

                    # 从标题和摘要中提取岗位信息
                    text = f'{title} {summary}'
                    if not self._is_job_related(text):
                        continue
                    job_related_hits += 1

                    # 提取岗位名称
                    position_title = self._extract_position_title(text)
                    if not position_title:
                        continue

                    # 提取薪资
                    salary_text = self._extract_salary_text(text)
                    salary_min, salary_max = self._parse_salary(salary_text)

                    # 提取技术关键词
                    tech_keywords = self._extract_tech_keywords(text)

                    item = self._build_item(
                        company_id=company_id,
                        company_name=name,
                        position_title=position_title,
                        salary_range=salary_text,
                        salary_min=salary_min,
                        salary_max=salary_max,
                        tech_keywords=tech_keywords,
                        headcount=1,
                        source_url=url,
                        source_name=f'websearch_{source}',
                    )
                    if item:
                        company_positions.add((position_title, f'websearch_{source}'))
                        self.stats['items_yielded'] += 1
                        yield item

            except Exception as e:
                self.logger.error(f"搜索招聘失败: {name}, error={e}")

            # --- C1: 多招聘站点搜索 (BOSS/猎聘/拉勾/智联) ---
            for suffix, src_name in self.RECRUIT_CHANNELS:
                self.stats['search_requests'] += 1
                try:
                    site_results = search_engine.search(f'{name} {suffix}', limit=8)
                    for result in site_results:
                        text = f"{result.get('title', '')} {result.get('summary', '')}"
                        if not self._is_job_related(text):
                            continue
                        position_title = self._extract_position_title(text)
                        if not position_title:
                            continue
                        # 去重: 同一岗位+同一来源不重复
                        if (position_title, src_name) in company_positions:
                            continue
                        salary_text = self._extract_salary_text(text)
                        salary_min, salary_max = self._parse_salary(salary_text)
                        tech_keywords = self._extract_tech_keywords(text)

                        item = self._build_item(
                            company_id=company_id,
                            company_name=name,
                            position_title=position_title,
                            salary_range=salary_text,
                            salary_min=salary_min,
                            salary_max=salary_max,
                            tech_keywords=tech_keywords,
                            headcount=1,
                            source_url=result.get('url', ''),
                            source_name=src_name,
                        )
                        if item:
                            company_positions.add((position_title, src_name))
                            self.stats['items_yielded'] += 1
                            yield item
                except Exception as e:
                    self.logger.debug(f"招聘站点搜索失败: {name} {suffix}: {e}")

            # --- C2: headcount 更新 — 用真实岗位去重数替代恒1 ---
            distinct_positions = len(company_positions)
            if distinct_positions > 0:
                self._update_headcount(company_id, distinct_positions)

            # --- C3: 招聘数量推断企业规模 ---
            if distinct_positions > 0 and company.get('employee_count') is None:
                self._infer_employee_from_recruit(company_id, distinct_positions)

            # inferred 回退已删除: 无真实岗位时不再虚构占位数据
            # (之前 95.8% 的招聘数据是虚构的, 污染了分析)

        self.logger.info(f"招聘搜索完成: 产出={self.stats['items_yielded']}")

        # yield 一个 dummy request 让 spider 不报错
        yield scrapy.Request(
            url='data:,',
            callback=self.parse_dummy,
            dont_filter=True,
        )

    def parse_dummy(self, response):
        """Dummy callback"""
        pass

    def closed(self, reason):
        self.logger.info(
            f"招聘爬虫结束: 搜索={self.stats['search_requests']}, "
            f"产出={self.stats['items_yielded']}, "
            f"丢弃={self.stats['items_dropped']}"
        )

    # ================================================================
    # 岗位信息提取
    # ================================================================

    def _is_job_related(self, text):
        """判断文本是否与招聘相关"""
        return any(kw in text for kw in self.JOB_INDICATORS)

    def _get_db_conn(self):
        """获取数据库连接 (懒加载)"""
        if not hasattr(self, '_db_conn') or self._db_conn is None or self._db_conn.closed:
            from scrapy.utils.project import get_project_settings
            settings = get_project_settings()
            database_url = settings.get('DATABASE_URL')
            if not database_url:
                return None
            self._db_conn = psycopg2.connect(database_url)
        return self._db_conn

    def _update_headcount(self, company_id, distinct_positions):
        """C2: 用真实岗位去重数更新该企业招聘记录的 headcount。
        distinct_positions = 去重岗位数, 作为规模信号写入所有该企业真实招聘记录。"""
        conn = self._get_db_conn()
        if not conn:
            return
        try:
            with conn.cursor() as cur:
                cur.execute(
                    """UPDATE recruitments SET headcount = %s
                       WHERE company_id = %s AND source_name NOT LIKE '%%inferred%%'""",
                    (distinct_positions, company_id),
                )
            conn.commit()
        except Exception as e:
            conn.rollback()
            self.logger.debug(f"headcount更新失败 {company_id}: {e}")

    def _infer_employee_from_recruit(self, company_id, distinct_positions):
        """C3: 招聘数量推断企业规模 → 写 companies.employee_count。
        仅在 employee_count 为空时填充, 标注 recruit_inferred。"""
        conn = self._get_db_conn()
        if not conn:
            return
        # 阈值: ≥10岗位→200人, 5-9→100人, 1-4→30人
        if distinct_positions >= 10:
            emp, src = 200, 'recruit_inferred_large'
        elif distinct_positions >= 5:
            emp, src = 100, 'recruit_inferred_mid'
        else:
            emp, src = 30, 'recruit_inferred_small'
        try:
            with conn.cursor() as cur:
                cur.execute(
                    """UPDATE companies SET employee_count = %s, employee_count_source = %s
                       WHERE id = %s AND employee_count IS NULL""",
                    (emp, src, company_id),
                )
            conn.commit()
        except Exception as e:
            conn.rollback()
            self.logger.debug(f"规模推断失败 {company_id}: {e}")

    def _infer_jobs_from_name(self, name):
        """按企业名特征推断通用岗位 (聚合页回退, 区别于真实岗位)"""
        jobs = []
        if any(k in name for k in ['智能', 'AI', '机器人', '算法']):
            jobs.append('AI算法工程师')
        if any(k in name for k in ['数据', '信息', '软件']):
            jobs.append('软件工程师')
        if any(k in name for k in ['云', '网络', '通信', '互联网']):
            jobs.append('云计算工程师')
        if any(k in name for k in ['安全', '安防']):
            jobs.append('安全工程师')
        if any(k in name for k in ['集成', '系统', '自动化']):
            jobs.append('系统集成工程师')
        if not jobs:
            jobs = ['软件开发工程师', '运维工程师']
        return jobs[:3]

    def _extract_position_title(self, text):
        """从文本中提取岗位名称"""
        # 常见 IT 岗位模式
        job_patterns = [
            r'((?:高级|资深|初级|中级)?(?:前端|后端|全栈|Java|Python|Go|C\+\+|AI|算法|数据|运维|测试|产品|UI|交互|安全|架构|技术|开发|软件|系统|数据库|网络|云|大数据|人工智能|深度学习|机器学习|NLP|计算机)[^\s,，、|/]{0,20}(?:工程师|开发|专家|经理|主管|负责人|架构师|分析师|设计师|专员))',
            r'招聘[：:]\s*([^\s,，、|/]{2,30}(?:工程师|开发|专家|经理|主管|架构师|分析师|设计师|专员))',
            r'([^\s,，、|/]{2,20}(?:工程师|开发|专家|架构师|分析师|设计师|专员))\s*招聘',
            # 通用: 任意"XX工程师/开发/技术员/专员"组合 (聚合页标题回退)
            r'([^\s,，、|/]{2,12}(?:工程师|开发工程师|技术员|程序员|设计师|专员|架构师))',
        ]
        for pattern in job_patterns:
            m = re.search(pattern, text)
            if m:
                title = m.group(1).strip()
                # 清理标题
                title = re.sub(r'[【】\[\]()]', '', title)
                # 过滤明显非岗位的匹配 (如"招聘信息""招聘官网")
                if any(bad in title for bad in ['招聘信息', '招聘官网', '招聘职位', '企业招聘']):
                    continue
                if len(title) > 3 and len(title) < 30:
                    return title
        return None

    def _extract_salary_text(self, text):
        """从文本中提取薪资文本"""
        # K格式: "15-30K"
        m = re.search(r'(\d+[Kk]?[-~—到至]\d+[Kk])', text)
        if m:
            return m.group(1)
        # 万格式: "1.5-3万"
        m = re.search(r'(\d+\.?\d*[-~—到至]\d+\.?\d*万)', text)
        if m:
            return m.group(1)
        # 纯数字: "8000-15000"
        m = re.search(r'(\d{4,}[-~—到至]\d{4,})', text)
        if m:
            return m.group(1)
        return None

    # ================================================================
    # Item 构建
    # ================================================================

    def _build_item(self, company_id, company_name, position_title,
                    salary_range, salary_min, salary_max, tech_keywords,
                    headcount, source_url, source_name):
        """构建 RecruitmentItem"""
        position_title = position_title.strip()
        if not position_title:
            return None

        item = RecruitmentItem()
        item['company_id'] = company_id
        item['company_name'] = company_name
        item['position_title'] = position_title
        item['salary_range'] = salary_range.strip() if salary_range else None
        item['salary_min'] = salary_min
        item['salary_max'] = salary_max
        item['tech_keywords'] = tech_keywords
        item['headcount'] = headcount
        item['source_url'] = source_url or None
        item['source_name'] = source_name
        return item

    # ================================================================
    # 薪资解析
    # ================================================================

    def _parse_salary(self, text: str):
        """解析薪资范围文本"""
        if not text:
            return None, None

        text = text.strip()

        # K格式: "15-30K" / "8K-12K"
        k_match = re.search(r'(\d+)[Kk]?[-~—到至](\d+)[Kk]', text)
        if k_match:
            return int(k_match.group(1)) * 1000, int(k_match.group(2)) * 1000

        # 千格式: "5-8千"
        qian_match = re.search(r'(\d+)[-~—到至](\d+)千', text)
        if qian_match:
            return int(qian_match.group(1)) * 1000, int(qian_match.group(2)) * 1000

        # 纯数字: "5000-8000"
        num_match = re.search(r'(\d+)[-~—到至](\d+)', text)
        if num_match:
            low = int(num_match.group(1))
            high = int(num_match.group(2))
            if low > 100000:
                return round(low / 12), round(high / 12)
            return low, high

        # 万格式: "1.5-3万"
        wan_match = re.search(r'(\d+\.?\d*)[-~—到至](\d+\.?\d*)万', text)
        if wan_match:
            return int(float(wan_match.group(1)) * 10000), int(float(wan_match.group(2)) * 10000)

        return None, None

    # ================================================================
    # 技术关键词提取
    # ================================================================

    def _extract_tech_keywords(self, text: str) -> list:
        """从岗位标题和描述提取技术关键词"""
        found = []
        for kw in self.TECH_KEYWORDS_LIST:
            if kw.lower() in text.lower():
                found.append(kw)
        return found

    # ================================================================
    # 加载企业
    # ================================================================

    def _load_companies(self):
        """从数据库读取企业列表 — 仅wuhan_it_1000名录(credit_code非空) + 跳过已爬

        通过 scrapy 参数控制:
          -a company="企业名"   : 只处理指定企业
          -a skip_crawled=0    : 全量重爬 (默认1=跳过已有招聘)
        """
        settings = get_project_settings()
        database_url = settings.get('DATABASE_URL')
        if not database_url:
            return
        skip_crawled = getattr(self, 'skip_crawled', '1') != '0'
        try:
            conn = psycopg2.connect(database_url)
            with conn.cursor() as cur:
                if self.company_override:
                    cur.execute(
                        "SELECT id, company_name, employee_count FROM companies WHERE company_name = %s",
                        (self.company_override,),
                    )
                elif skip_crawled:
                    cur.execute(
                        "SELECT c.id, c.company_name, c.employee_count FROM companies c "
                        "WHERE c.credit_code IS NOT NULL "
                        "AND NOT EXISTS(SELECT 1 FROM recruitments r WHERE r.company_id = c.id LIMIT 1) "
                        "ORDER BY c.id"
                    )
                else:
                    cur.execute(
                        "SELECT c.id, c.company_name, c.employee_count FROM companies c "
                        "WHERE c.credit_code IS NOT NULL ORDER BY c.id"
                    )
                self.companies = [
                    {'id': r[0], 'company_name': r[1], 'employee_count': r[2]}
                    for r in cur.fetchall()
                ]
            conn.close()
            # -a limit=N: 限制企业数 (测试用)
            limit = int(getattr(self, 'limit', '0') or '0')
            if limit > 0:
                self.companies = self.companies[:limit]
            self.logger.info(f"加载企业 {len(self.companies)} 家 (skip_crawled={skip_crawled})")
        except Exception as e:
            self.logger.error(f"加载企业列表失败: {e}")

    def errback_request(self, failure):
        self.logger.error(f"请求失败: {failure.request.url}, error={failure.value}")
