# E-InfoInsight-agent-codes — Hermes 端到端运行 Prompt

> **用途**：将本文件从下方的「Prompt 正文」开始整体复制发送给 Hermes，即可驱动武汉IT企业智能评级系统完整端到端运行（全量）或指定数据集测试。
> 每一步均给出可执行的 shell 命令与验收检查点，按顺序执行、每步验证通过再进入下一步。
> 基于代码库 `test` 分支最新提交（含新闻/招聘/招投标/技术爬虫重构 + 企查查浏览器补全方案）。

---

## Prompt 正文

你是武汉IT企业智能评级系统的执行 Agent。请按以下流程端到端运行系统，支持两种模式：**全量运行**与**指定数据集测试**。每步执行命令后必须做验收检查，失败即停下排查并报告，不要跳过验证继续。

### 一、环境与前置校验（必做，约2分钟）

1. 工作目录定位：`cd /Users/kc/Desktop/E-InfoInsight-agent-codes`
2. 确认虚拟环境与依赖：
```bash
test -d .venv && echo "venv OK" || echo "venv MISSING"
.venv/bin/python -c "import scrapy, psycopg2, playwright, dotenv; print('deps OK')"
```
3. 确认 PostgreSQL 与 `.env`：
```bash
source .env 2>/dev/null
.venv/bin/python -c "import os,psycopg2;from dotenv import load_dotenv;load_dotenv('.env');c=psycopg2.connect(os.getenv('DATABASE_URL'));print('DB OK',os.getenv('DATABASE_URL')[:30])"
```
4. 确认核心表存在且非空（预期 companies≈1200+）：
```bash
.venv/bin/python -c "
import os,psycopg2;from dotenv import load_dotenv;load_dotenv('.env')
c=psycopg2.connect(os.getenv('DATABASE_URL'));cur=c.cursor()
for t in ['companies','tech_profiles','recruitments','news_mentions','bidding_records','ratings']:
    cur.execute(f'SELECT count(*) FROM {t}');print(f'{t}:',cur.fetchone()[0])
"
```
- **验收**：DB OK 且 companies > 1000。若 DB 连不上，先 `createdb rating_system && psql rating_system -f db/init.sql -f db/seed.sql`。

---

### 二、模式 A：指定数据集测试（推荐先用，约5分钟）

用前 N 家企业跑通完整链路，验证无报错，再决定是否全量。

1. **选取测试集**（取前10家有credit_code的企业）：
```bash
source .env
psql "$DATABASE_URL" -t -A -c "SELECT id,company_name FROM companies WHERE credit_code IS NOT NULL ORDER BY id LIMIT 10"
```

2. **爬虫小批量**（每个spider加 `-a limit=10 -a skip_crawled=0`，在 crawler 目录跑）：
```bash
cd crawler
for s in news recruitment bidding tech; do
  ../.venv/bin/scrapy crawl $s -a limit=10 -a skip_crawled=0 -L INFO > ../logs/test_${s}.log 2>&1
  echo "$s exit=$? ; 产出: $(grep -c '✓' ../logs/test_${s}.log)"
done
cd ..
```
- **验收**：4个日志无 `Traceback`；news 有命中（relevance>0）；bidding 至少有 0 条（小公司可能无中标，正常）；tech 产出 tech_profiles 行。新闻/招聘不应出现 `websearch_inferred`（已废弃）。

3. **规则评分**（对全部 raw 企业增量评分，或先标记测试集）：
```bash
.venv/bin/python engine/rules_engine.py --mode incremental 2>&1 | tail -15
```
- **验收**：`scored` 企业数增加，分数分布合理（非全0/全100）。

4. **DeepSeek 评级**（仅对 scored 且 total_score≥50 的小批，约≤10家）：
```bash
.venv/bin/python scripts/run_deepseek_rating.py 2>&1 | tail -15
```
- **验收**：`ratings` 表新增记录，level ∈ {S,A,B,C,D}，无大量 429/5xx。

5. **导出与校验**：
```bash
.venv/bin/python scripts/verify_and_score.py 2>&1 | tail -20
.venv/bin/python scripts/export_individual.py 2>&1 | tail -5
```
- **验收**：`individual.csv` 存在、15列对齐、含 llm 评级；验证报告判定完成率 100%，存疑/可疑为 0。

**模式A通过后**，确认链路无误，再进入模式B全量。

---

### 三、模式 B：全量端到端运行（约数小时）

> 全量涉及 1019 家企业 × 4 爬虫 + 企查查浏览器补全 + DeepSeek 调用，耗时较长且可能触发第三方风控。建议分阶段执行，每阶段写日志。

#### B1. 数据爬取（4维度）

```bash
cd crawler
# 增量模式：跳过已有数据的企业（默认 skip_crawled=1）
for s in news recruitment bidding tech; do
  ../.venv/bin/scrapy crawl $s -L INFO > ../logs/full_${s}.log 2>&1
  echo "$s done; 产出 $(grep -c '✓' ../logs/full_${s}.log)"
done
cd ..
```
- **验收**：4日志无未捕获 Traceback；DB 表行数显著增长（news 数千、recruitments 真实岗位、tech_profiles≈1019）。

#### B2. 工商信息补全（注册资本）

优先用企查查浏览器（IP冷却后才可用）：
```bash
# 企查查浏览器补全（含注册资本+参保人数，IP级风控时返回 _blocked 自动暂停）
.venv/bin/python scripts/enrich_via_browser.py --source qcc --resume --sleep-min 10 --sleep-max 22 -L INFO > logs/qcc_enrich.log 2>&1 &
```
- 企查查被IP风控（`verify.qcc.com/limits`，body为空）时，**不要硬跑**，改为：
```bash
# 搜索引擎摘要兜底（仅注册资本，无IP风控，但对无网络足迹的小微企业命中率低）
.venv/bin/python scripts/enrich_capital_search.py --resume -L INFO > logs/capital_search.log 2>&1 &
```
- **验收**：`SELECT count(registered_capital) FROM companies WHERE credit_code IS NOT NULL` 覆盖率上升；`capital_amount > 500000`（50亿）的存疑记录需人工复核（多为新闻误匹配，应清除）。

#### B3. 技术画像补强

```bash
.venv/bin/python scripts/refine_tech_recruit.py 2>&1 | tail -10
.venv/bin/python scripts/supplement_tech.py 2>&1 | tail -10
```
- **验收**：`tech_profiles.tech_stack_source` 有值（website_subpage / homepage_and_recruit / no_website）；无 GitHub 相关字段（已弃用，命中率0）。

#### B4. 规则评分（全量）

```bash
.venv/bin/python engine/rules_engine.py --mode full 2>&1 | tail -20
```
- **验收**：所有 raw 企业转为 scored；评分分布 S/A/B/C/D 段位齐全。

#### B5. DeepSeek 深度评级（高价值企业，status=scored 且 ≥50）

```bash
.venv/bin/python scripts/run_deepseek_rating.py -L INFO 2>&1 | tee logs/deepseek_$(date +%Y%m%d).log | tail -20
```
- **验收**：达标企业全部 rated，level ∈ S/A/B/C/D；429 限速自动指数退避，5xx 重试3次。

#### B6. 模型验证

```bash
.venv/bin/python scripts/verify_and_score.py 2>&1 | tail -25
.venv/bin/python scripts/verify_results.py 2>&1 | tail -15
```
- **验收**：验证报告判定完成率 100%；存疑/可疑为 0；评分偏高/偏低企业已记录。

#### B7. 报告与导出

```bash
.venv/bin/python scripts/export_individual.py 2>&1 | tail -5
.venv/bin/python scripts/export_1000_results.py 2>&1 | tail -5
# 生成评级分布与销售线索
.venv/bin/python engine/report.py 2>&1 | tail -10
```
- **验收**：`data/reports/daily_*.md`、`data/reports/leads_*.csv/xlsx` 生成；S/A 级企业导出完整。

#### B8. 多机同步（可选，线上同步时执行）

```bash
.venv/bin/python scripts/export_sync.py 2>&1 | tail -10
# 将 data/sync push 到远端机器，对端 pull 后导入（幂等，参考 docs/多机数据同步方案.md）
```

---

### 四、关键约束与排障

1. **Scrapy 必须在 `crawler/` 目录运行**（`scrapy.cfg` 所在），否则报 `Scrapy 2.x started` 后无输出。
2. **settings.py 的 dotenv 路径已修正**为 `../../.env`；若 DATABASE_URL 读不到，先 `export $(grep -v '^#' .env | xargs)`。
3. **企查查浏览器**首次需扫码登录，cookies 存于 `.browser-session/qcc_state.json`；IP风控后需冷却数小时～1天再 `--resume` 续跑，重新登录无效（IP级封锁）。
4. **新闻/招聘爬虫已重构**：搜索词去掉冗余"武汉"、`_mentions_company` 品牌主词硬过滤、inferred 虚构回退已删除；若日志出现 `websearch_inferred` 或 `relevance=0` 大量入库，说明运行的是旧代码，检查 `git branch` 应在 `test`。
5. **参保人数(employee_count)已确认不需要**，资本补全以注册资本为准。
6. **每阶段产出写日志到 `logs/run_<ts>_summary.md`**，复盘爬取效率与命中率，异常回写 `skills/rating-pipeline.skill.md`。
7. DeepSeek API key 在 `.env` 的 `DEEPSEEK_API_KEY`；缺 key 时跳过 B5，仅做规则评分。

执行完毕后输出一份端到端运行摘要：各阶段产出计数、命中率、异常项、覆盖率（注册资本/技术栈/新闻），以及与上次运行的对比。
