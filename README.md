# 武汉IT企业智能评级系统 — 系统说明文档

> 版本: 2026-08-27 | 分支策略: master(程序代码) / test(代码+运行时数据)

---

## 一、系统架构

### 1.1 目录结构

```
E-InfoInsight-agent-codes/
├── config/                    # 配置文件
│   ├── config.yaml            # 主配置 (数据库/DeepSeek/爬虫/评分/调度/报告)
│   ├── scoring_rules.yaml     # 5维度评分规则与阈值
│   └── industry_keywords.yaml # IT行业关键词库 (强/中/弱信号词)
├── db/                        # 数据库
│   ├── init.sql               # DDL (8表) + 唯一约束
│   ├── seed.sql               # 种子数据 (10家示例)
│   └── seed_real.sql          # 真实企业名录 (1000家, 含统一社会信用代码)
├── crawler/                   # Scrapy 爬虫框架
│   └── wuhan_it_crawler/
│       ├── spiders/           # 6个Spider
│       │   ├── business_spider.py     # 工商信息 (搜索聚合)
│       │   ├── tech_spider.py         # 技术画像 (GitHub+搜索)
│       │   ├── recruitment_spider.py  # 招聘信息 (搜索+推断)
│       │   ├── news_spider.py         # 新闻舆情 (百度/搜狗/必应)
│       │   ├── bidding_spider.py      # 招投标 (搜索推断)
│       │   └── websearch_spider.py    # 多引擎聚合搜索
│       ├── pipelines/         # 5级管道 (优先级100→500)
│       │   ├── dedup_pipeline.py      # 100: 去重 (credit_code)
│       │   ├── filter_pipeline.py     # 200: 行业过滤
│       │   ├── clean_pipeline.py      # 300: 字段清洗
│       │   ├── validate_pipeline.py   # 400: 完整性校验
│       │   └── standardize_pipeline.py# 500: 格式标准化
│       ├── items.py           # Item定义 (Company/Recruitment/TechProfile/News/Bidding)
│       ├── middlewares.py     # 中间件
│       └── utils/             # 工具 (反检测/验证码/代理池/UA轮换)
├── engine/                    # 核心引擎
│   ├── rules_engine.py       # 规则引擎 (5维度评分)
│   ├── llm_client.py         # DeepSeek LLM客户端 (批量评级+429退避)
│   ├── report.py             # 报告生成 (日报/线索/通知)
│   ├── data_pipeline.py      # 数据管道框架
│   ├── websearch.py          # 多引擎搜索聚合 (百度/搜狗/必应/360/头条)
│   └── prompts/
│       ├── analysis_prompt.md       # DeepSeek评级Prompt模板
│       └── few_shot_examples.json   # Few-shot示例 (S/A/B/C/D)
├── scripts/                   # 运维脚本 (32个)
│   ├── integrated_crawl.py   # 集成爬取 (新闻→技术→招聘→招投标+内含评分)
│   ├── enrich_company_biz.py # 工商信息补全 (天眼查/企查查/爱企查/BOSS/猎聘/拉勾聚合)
│   ├── rate_by_kscc.py       # kscc确定性评级 (不调DeepSeek)
│   ├── run_deepseek_rating.py# DeepSeek批量评级驱动
│   ├── supplement_tech.py    # 技术画像补强 (KNOWN_TECH覆盖)
│   ├── export_1000_results.py# 导出1000家评级CSV
│   ├── export_individual.py  # 导出individual.csv
│   ├── verify_results.py     # 评级结果验证
│   ├── daily_update.sh       # 每日增量更新
│   ├── full_scan.sh          # 全量扫描
│   └── hot_track.sh          # 热点追踪
├── skills/                    # Hermes Skills (5个)
│   ├── rating-pipeline.skill.md  # 全链路编排 (主运行手册)
│   ├── rating-crawl.skill.md     # 爬虫编排
│   ├── rating-score.skill.md     # 规则引擎评分
│   ├── rating-analyze.skill.md   # DeepSeek评级
│   └── rating-report.skill.md    # 报告导出
├── dashboard/                 # 可视化看板
│   ├── dashboard.py          # Python HTTP服务 (端口8642)
│   └── index.html            # 前端页面
├── tests/                     # 测试套件
├── data/                      # 运行时数据 (仅test分支)
│   └── reports/
│       └── individual.csv    # 全链路最终产出 (唯一跟踪的数据文件)
├── logs/                      # 运行日志 (仅test分支)
└── docs/                      # 文档
```

### 1.2 数据流与状态机

```
companies.status: raw ──(爬虫入库)──→ raw ──(规则引擎)──→ scored ──(DeepSeek)──→ rated ──(报告)──→ filtered
```

每一步对应数据库更新：
- **爬虫**: INSERT companies/关联表, status='raw'
- **规则引擎**: INSERT ratings(rated_by='rules_engine'), status='scored'
- **DeepSeek评级**: INSERT ratings(rated_by='deepseek'), status='rated'
- **报告导出**: 导出S/A级线索, status='filtered'

### 1.3 数据库Schema (8表)

| 表 | 核心字段 | 唯一约束 |
|---|---|---|
| companies | company_name, credit_code, registered_capital, capital_amount, employee_count, business_scope, registered_address, industry_tags, funding_stage, status | credit_code |
| tech_profiles | company_id, tech_stack, github_org, github_stars, cloud_provider, ai_job_ratio | company_id |
| recruitments | company_id, position_title, salary_range, salary_min, salary_max, tech_keywords, headcount, source_name | (company_id, position_title, source_name) |
| news_mentions | company_id, title, sentiment_score, relevance_score, is_digital_related | (company_id, title, source_name) |
| bidding_records | company_id, project_name, project_type, budget_amount, is_digital | (company_id, project_name, source_name) |
| ratings | company_id, total_score, rating_level, tech_score, funding_score, intent_score, team_score, industry_score, demand_tags, sales_pitch, reasoning, rated_by | (company_id, rated_by) |
| crawl_tasks | spider_name, status, items_count, started_at, finished_at | — |
| rating_changelog | company_id, old_level, new_level, changed_by | — |

### 1.4 评分体系

**5维度加权评分** (实际权重, 来源: `config/scoring_rules.yaml`):

| 维度 | 权重 | 主要信号 |
|------|------|---------|
| tech_investment | 30 | AI岗位占比, GitHub Stars, 技术博客, 云服务商, 经营范围技术词 |
| funding | 25 | 融资轮次(B/C+15分), 注册资本(≥1000万+5分), 资本规模分档 |
| transformation_intent | 30 | 新闻数字化关键词(+5/每个,上限20), 数字化招投标(+10), 跨维度加成 |
| team_size | 15 | 招聘数量分档(≥10个+10分), 资本/经营范围推断兜底 |
| industry_match | 10 | 高匹配行业+2/每个, 低匹配-1/每个 |

> **注意**: 权重合计110, `rules_engine.py` 通过 `min(total, 100)` 截断到100分。

**等级阈值** (规则引擎实际使用, 来源: `scoring_rules.yaml`):

| 等级 | 分数区间 |
|------|---------|
| S | ≥80 |
| A | ≥60 |
| B | ≥40 (通过阈值, 达标企业才送DeepSeek) |
| C | ≥20 |
| D | <20 |

---

## 二、运行方法

### 2.1 环境搭建

```bash
cd /Users/kc/Desktop/E-InfoInsight-agent-codes
python3.11 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
pip install -r requirements-dev.txt  # 可选: 测试依赖
createdb rating_system
psql -d rating_system -f db/init.sql
psql -d rating_system -f db/seed_real.sql   # 导入1000家真实企业
cp .env.example .env  # 编辑: 填入 DEEPSEEK_API_KEY, DATABASE_URL
```

### 2.2 端到端全链路 (推荐, 10步)

| 步骤 | 命令 | 说明 | 耗时参考 |
|------|------|------|---------|
| 1 | 环境检查 | DB可连, API Key存在, 查status分布 | — |
| 2 | `python scripts/integrated_crawl.py` | 清空关联表→新闻→技术→招聘→招投标→内含评分 | 15家5-8分钟 |
| 3 | `python scripts/supplement_tech.py` | KNOWN_TECH覆盖tech_profiles(GitHub国内不可达时必需) | ~1分钟 |
| 4 | `python engine/rules_engine.py --mode full` | 全量规则评分(步骤2已内含, 此为独立重跑) | ~1分钟 |
| 5 | `python scripts/run_deepseek_rating.py --mode full` | DeepSeek评级(仅达标企业≥40分, 3家/批) | ~3小时(330批) |
| 6 | `python scripts/enrich_company_biz.py --resume` | 多渠道补全: 注册资本/员工人数/真实招聘(天眼查/企查查/BOSS等) | ~3小时 |
| 7 | `python scripts/export_individual.py` | 导出individual.csv (15列, 规则+LLM双评级) | ~10秒 |
| 8 | `python scripts/verify_results.py` | 评级结果验证(规则校验+模型判定) | ~30分钟 |
| 9 | 多机同步(可选) | `export_sync.py`→git push / pull→`import_sync.py` | — |
| 10 | 日志复盘 | 写logs/summary.md, 优化点回写skill | — |

### 2.3 替代方案: kscc确定性评级 (不调DeepSeek)

```bash
python scripts/rate_by_kscc.py              # 基于经营范围+技术栈+5维度实得分
python scripts/export_1000_results.py       # 导出CSV (支持kscc/deepseek/rules_engine三层)
```

### 2.4 Shell脚本

```bash
bash scripts/full_scan.sh      # 全量: business→tech→recruit→news→bidding→评分→评级→报告
bash scripts/daily_update.sh   # 增量: business增量→recruit→news→评分→评级→日报
bash scripts/hot_track.sh      # 热点: news(S,A级)→重评→追踪报告
```

### 2.5 可视化看板

```bash
python dashboard/dashboard.py           # 默认端口8642, 浏览器打开 http://127.0.0.1:8642
python dashboard/dashboard.py 9000      # 指定端口
python dashboard/dashboard.py 9000 path/to/scored.csv  # 指定数据源(xlsx/csv)
```

### 2.6 常用运维命令速查

```bash
# 查企业状态分布
psql -d rating_system -c "SELECT status, count(*) FROM companies GROUP BY status"

# 查评级分布
psql -d rating_system -c "SELECT rating_level, count(*) FROM ratings WHERE rated_by='rules_engine' GROUP BY rating_level"

# 重置全量数据 (慎用!)
psql -d rating_system -c "DELETE FROM ratings; UPDATE companies SET status='raw'"

# 导出全量CSV
python scripts/export_1000_results.py
```

---

## 三、Hermes Prompt 格式

### 3.1 Skill 文件格式

Hermes Skill 以 `.skill.md` 文件定义, 位于 `skills/` 目录。

**Frontmatter 格式:**

```yaml
---
name: <skill-name>              # 调用名: hermes skill run <name>
description: <触发描述>          # 何时使用, Hermes据此自动匹配
version: 1.1.0                  # 可选
author: Hermes Agent            # 可选
license: MIT                    # 可选
metadata:
  hermes:
    tags: [crawler, pipeline]   # 可选: 标签
    related_skills: [...]       # 可选: 关联skill
---
```

**Body 结构:** Markdown, 包含 流程/配置/验证/容错/Pitfalls/Checklist 章节。

**5个Skill:**

| Skill | 触发场景 | 对应命令 |
|-------|---------|---------|
| rating-pipeline | 全链路/排查效率 | 10步流水线 (见2.2节) |
| rating-crawl | 启动爬虫 | `scrapy crawl {spider}` |
| rating-score | 规则评分 | `python engine/rules_engine.py --mode {mode}` |
| rating-analyze | DeepSeek评级 | `python scripts/run_deepseek_rating.py` |
| rating-report | 导出报告 | `python engine/report.py --date {date}` |

### 3.2 Hermes 交接Prompt格式

文件 `docs/Hermes交接Prompt.md` 是完整的Hermes交接指南, **整体发送给Hermes作为系统指令**。

**结构 (11节):**

| 节 | 内容 |
|----|------|
| 一 项目概览 | 元数据表, 目录树, 数据流状态图 |
| 二 环境搭建 | venv/PG/env/验证命令 |
| 三 修复已知Bug | 具体替换代码, 验证命令 |
| 四 补充Spider测试 | 测试代码 |
| 五 端到端联调 | 规则引擎→DeepSeek→报告, 每步有期望输出 |
| 六 Hermes Skills集成 | Skill→命令→验收条件 映射表 |
| 七 线上代码同步 | test→master合并检查清单, 热修复流程 |
| 八 关键配置 | 环境变量/评分规则/DeepSeek容错/Cron调度 |
| 九 常见问题 | 6个FAQ + 排查命令 |
| 十 优先级 | P0-P3工作排序 |
| 十一 速查 | bash一行命令合集 |

**每步格式:** `**Prompt:**` (bash代码块) + `**验收:**` (期望结果)

### 3.3 DeepSeek LLM Prompt模板

文件 `engine/prompts/analysis_prompt.md`, 通过 `LLMRatingClient._build_batch_prompt` 填充企业数据后发送。

**结构:**
1. **角色定义**: "资深的企业数字化转型顾问, 15年经验"
2. **任务**: 对每家企业输出结构化JSON评分
3. **评分维度与权重表** (5维, 与规则引擎对齐)
4. **评级标准表** (S/A/B/C/D 分数区间)
5. **输入数据占位符**: `{companies}` → 替换为逐企业的:
   - 名称 / 经营范围 / 技术岗位占比 / 融资阶段 / 近期动态
6. **输出格式要求**: 严格JSON数组, 禁止markdown代码块
7. **字段约束**: score∈0-100, level∈S/A/B/C/D, demand_tags非空数组, sales_pitch≤50字, reasoning非空
8. **评估原则**: "仅基于提供的数据判断, 不要编造信息"; 缺失维度按中位数(满分一半)计分

**Few-shot示例:** `engine/prompts/few_shot_examples.json` 覆盖S/A/B/C/D各等级

---

## 四、分支策略与代码同步

### 4.1 分支职责

| 分支 | 内容 | 推送 |
|------|------|------|
| **master** | 纯程序代码 (crawler/engine/scripts/tests/config/db/skills/docs/dashboard程序文件) | 仅代码commit |
| **test** | 程序代码 + 运行时数据/日志/进度文件 | 代码commit + 可选数据commit |

### 4.2 .gitignore 规则 (test分支扩展)

master和test共享同一个 `.gitignore`, 忽略所有运行时文件:
- `data/` 下除 `individual.csv` 外的所有csv/db/json/progress文件
- `logs/` 全目录
- `dashboard/` 的 `__pycache__/`, `*.pid`, `api_check.json`, `out_raw.txt`

### 4.3 代码同步流程

```bash
# 1. 代码修改 → 在test分支commit
git checkout test
# ... 修改代码 ...
git add <代码文件>
git commit -m "feat: 描述"

# 2. 同步到master
git checkout master
git merge test    # 或 git cherry-pick <sha>
git push origin master
git checkout test

# 3. 运行时数据 → 仅在test分支显式commit (通常不需要, 因被ignore)
git add data/reports/some_result.csv
git commit -m "data: 某次运行结果"
```

---

## 五、已知差异与勘误

以下为文档/配置/代码间的不一致, 已验证属实:

| 项 | 文档/配置声明 | 实际代码 | 处置 |
|---|---|---|---|
| Skill数量 | CLAUDE.md: "4个" | 实际5个 (含rating-pipeline) | 本文档已记5 |
| `scripts/setup.sh` | CLAUDE.md "快速开始"引用 | 不存在 | 本文档已内联环境搭建步骤 |
| pass_threshold | config.yaml: 60 | rules_engine.py读scoring_rules.yaml B=40; config.yaml值未被读取(死配置) | 有效值=40 |
| 评级阈值(LLM vs 规则) | analysis_prompt.md: S90/A75/B60/C40/D0 | rules_engine+scoring_rules.yaml: S80/A60/B40/C20 | 两套阈值独立运行; 规则引擎用于入库评分, LLM用于深度评级 |
| max_tokens | config.yaml: 500 | llm_client.py默认: 8000 | 以代码为准, config.yaml为旧值 |
| batch_size / timeout | config.yaml: 5 / 30 | llm_client.py: 3 / 120 | 以代码为准 |
| 评分权重 | analysis_prompt.md: tech30/fund20/intent25/team15/ind10 | scoring_rules.yaml: tech30/fund25/intent30/team15/ind10 (合计110, min(total,100)) | 以YAML为准 |
| 搜索引擎数 | Hermes交接Prompt: 百度/搜狗/必应(3) | websearch.py: 5 (含360搜索/头条搜索) | 以代码为准 |
| rated_by值 | Hermes交接Prompt Step7: 'llm' | llm_client.py写: 'deepseek' | 查询时用'deepseek' |
| 招聘字段名 | Hermes交接Prompt测试代码: job_title/source_platform | DB/Item: position_title/source_name | 以DB为准 |

---

*文档生成时间: 2026-08-27 | 基于代码库 test 分支最新提交 | 数据库: PostgreSQL rating_system*

一、6 个 Scrapy Spider
  
  1. news（新闻舆情）

  - 爬取网站：百度新闻 news.baidu.com/ns、搜狗新闻、必应新闻、360新闻
  - 逻辑：读 companies 表 → 按企业全名（已去掉冗余"武汉"）搜新闻 → _mentions_company
  品牌主词硬过滤（标题/摘要须含核心词才入库）→ 情感打分（正面+0.5/负面-0.5）+ 数字化相关度
  - 调用：scrapy crawl news [-a limit=N] [-a skip_crawled=0]（须在 crawler/ 目录）

  2. recruitment（招聘）

  - 爬取网站：WebSearchEngine 聚合搜索 "{企业名} 招聘"，覆盖 BOSS直聘/猎聘/拉勾/智联等招聘站（经搜索引擎摘要，非直连）
  - 逻辑：从摘要提取岗位名/薪资/技术关键词 → headcount=该企业真实岗位去重计数 → 招聘数量反推企业规模写 employee_count →
  inferred 虚构回退已删除（不再产占位数据）
  - 调用：scrapy crawl recruitment [-a limit=N]

  3. bidding（招投标）

  - 爬取网站：WebSearchEngine 聚合搜索 "{企业名} 中标"（千里马/建设通/招标网等结果）
  - 逻辑：按企业名搜 → 标题须含"中标/成交/预成交" + 噪声过滤（官网入口/欢迎页）→ 提取项目名/预算/是否数字化 → company_id
  天然关联 100%（原泛搜索"武汉 信息化 招标"全部 NULL 已废弃）
  - 调用：scrapy crawl bidding [-a limit=N]

  4. tech（技术能力）

  - 爬取网站：百度搜索企业官网 → 抓取官网页面；GitHub API 已弃用（命中率0+国内不可达）
  - 逻辑：搜官网URL → 抓页面文本 → _extract_tech_stack 按技术词库提取技术栈 + 云服务商 + 技术博客URL →
  置信度溯源（/products /tech /about 子页高可信，首页 homepage_inferred 降权）→ 招聘联动：营销页技术栈用
  recruitments.tech_keywords 补充
  - 调用：scrapy crawl tech [-a limit=N]

  5. websearch（工商信息聚合）

  - 爬取网站：WebSearchEngine 聚合 "{企业名} 注册资本/天眼查/企查查/参保人数"
  - 逻辑：从搜索摘要正则提取注册资本/参保人数等 → 渠道逐级 fallback：搜索引擎聚合 → 百度百科 API → 参保定向搜索
  - 调用：scrapy crawl websearch

  6. business（经营范围）

  - 爬取网站：国家企业信用信息公示系统 gsxt.gov.cn（反爬严，实际降级到搜索聚合 + 百度百科）
  - 逻辑：补全 business_scope 字段，用于 IT 身份过滤
  - 调用：scrapy crawl business

  ---
  二、2 个补全脚本（非 Scrapy）
  
  7. enrich_via_browser.py（企查查/天眼查浏览器）

  - 爬取网站：企查查 qcc.com / 天眼查 tianyancha.com（Playwright 持久登录态）
  - 逻辑：搜索结果页直接提取注册资本/法人/成立日期/地址 → 详情页补参保人数 → --source 
  qcc；IP级风控（verify.qcc.com/limits）自动检测暂停，--resume 断点续传
  - 调用：python scripts/enrich_via_browser.py --source qcc --resume --sleep-min 10 --sleep-max 22

  8. enrich_capital_search.py（搜索引擎资本兜底）

  - 爬取网站：WebSearchEngine "{企业名} 注册资本/工商信息"
  - 逻辑：从摘要正则提取注册资本，无IP风控，但对无网络足迹小微企业命中率低
  - 调用：python scripts/enrich_capital_search.py --resume
