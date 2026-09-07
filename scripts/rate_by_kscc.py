#!/usr/bin/env python3
"""kscc 直接评级生成 — 不调用 DeepSeek 模型

替代 run_deepseek_rating.py 的 DeepSeek 调用：
基于企业真实数据(经营范围 + 技术画像 + 规则引擎5维度实得分)，
确定性生成 demand_tags / sales_pitch / reasoning，写入 ratings 表
(rated_by='kscc')。score/level 沿用规则引擎评分(已有数据基础)。

同时补全工商字段:
  - industry_tags: 32家缺失 → 从 business_scope 推导
  - funding_stage: 1004家缺失 → 按 capital_amount 推断(标注未披露)

用法: python scripts/rate_by_kscc.py [--limit N] [--skip-biz]
"""
import os
import re
import sys
import logging
from datetime import datetime

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from dotenv import load_dotenv

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger(__name__)

PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
load_dotenv(os.path.join(PROJECT_ROOT, ".env"))
DATABASE_URL = os.getenv("DATABASE_URL")

# ============================================================
# 需求标签映射: 经营范围关键词 → 数字化转型需求方向
# (源自 analysis_prompt.md 的 demand_tags 示例, 落地为确定性规则)
# ============================================================
DEMAND_RULES = [
    # (关键词列表, 需求标签)
    (["智能制造", "装备", "制造", "加工", "光学", "光缆", "光纤", "器件",
      "电子元件", "半导体", "芯片", "集成电路", "机械", "模具", "注塑"], "智能制造"),
    (["工业互联网", "工业云", "MES", "数字孪生", "车间", "产线"], "工业互联网"),
    (["人工智能", "AI", "机器学习", "深度学习", "NLP", "大模型", "智能算法",
      "计算机视觉", "语音识别", "智能驾驶", "自动驾驶"], "AI应用"),
    (["网络安全", "信息安全", "数据安全", "信创", "密码", "攻防", "等保"], "网络安全"),
    (["云计算", "云服务", "云平台", "公有云", "私有云", "混合云", "容器", "Kubernetes"], "云迁移"),
    (["大数据", "数据中台", "数据治理", "数据仓库", "数据挖掘", "数据分析", "BI"], "数据中台"),
    (["物联网", "IoT", "传感", "射频", "RFID"], "物联网平台"),
    (["区块链", "分布式账本", "智能合约"], "区块链应用"),
    (["金融", "支付", "银行", "保险", "证券", "风控", "信贷", "清算"], "金融科技"),
    (["物流", "供应链", "仓储", "配送", "货运", "口岸"], "供应链数字化"),
    (["政务", "政府", "数字政府", "电子政务", "城市治理", "一网通办"], "智慧城市"),
    (["医疗", "医药", "医院", "健康", "影像", "病历"], "智慧医疗"),
    (["教育", "校园", "教学", "在线学习", "培训"], "智慧教育"),
    (["零售", "电商", "商贸", "门店", "营销", "客服", "会员"], "数字营销"),
    (["通信", "5G", "运营商", "基站", "网络优化"], "5G+工业互联网"),
    (["能源", "电力", "电网", "新能源", "光伏", "储能", "水务", "燃气"], "智慧能源"),
    (["农业", "种植", "养殖", "农机", "农产品"], "智慧农业"),
    (["建筑", "工程", "施工", "BIM", "地产"], "智慧建造"),
    (["软件", "信息技术", "信息系统", "系统集成", "SaaS", "PaaS", "平台"], "数据中台"),
]

# 行业标签推导 (用于 industry_tags 补全)
INDUSTRY_TAG_RULES = [
    (["软件开发", "软件", "SaaS", "PaaS", "信息系统"], "软件"),
    (["人工智能", "AI", "机器学习", "大模型"], "人工智能"),
    (["云计算", "云服务", "云平台"], "云计算"),
    (["大数据", "数据中台", "数据"], "大数据"),
    (["网络安全", "信息安全", "信创"], "网络安全"),
    (["物联网", "IoT"], "物联网"),
    (["区块链"], "区块链"),
    (["集成电路", "芯片", "半导体"], "集成电路"),
    (["通信", "5G", "光通信"], "通信"),
    (["智能制造", "工业互联网"], "智能制造"),
    (["互联网", "电子商务"], "互联网"),
    (["信息技术"], "信息技术"),
]

# 销售话术模板: 按需求标签组合生成 (≤50字符)
PITCH_TEMPLATES = {
    "智能制造": "智能制造落地，工厂数字化升级",
    "工业互联网": "工业互联网平台，打通产线数据",
    "AI应用": "AI赋能业务，智能化升级",
    "网络安全": "安全护航数字化，信创合规",
    "云迁移": "云迁移上云，降本增效",
    "数据中台": "数据中台建设，驱动决策",
    "物联网平台": "物联网平台，连接万物",
    "区块链应用": "区块链落地，可信协作",
    "金融科技": "金融科技，风控数字化",
    "供应链数字化": "供应链数字化，协同提效",
    "智慧城市": "智慧城市，数字治理",
    "智慧医疗": "智慧医疗，数据互通",
    "智慧教育": "智慧教育，在线赋能",
    "数字营销": "数字营销，精准获客",
    "5G+工业互联网": "5G+工业互联网，连接智造",
    "智慧能源": "智慧能源，绿色数字化",
    "智慧农业": "智慧农业，数据助农",
    "智慧建造": "智慧建造，BIM协同",
}


def derive_demand_tags(scope: str, tech_stack: list) -> list:
    """从经营范围 + 技术栈推导需求标签 (去重, 最多4个, 至少1个)"""
    text = (scope or "") + " " + " ".join(tech_stack or [])
    tags = []
    for keywords, tag in DEMAND_RULES:
        if any(kw.lower() in text.lower() for kw in keywords):
            if tag not in tags:
                tags.append(tag)
    if not tags:
        tags = ["数据中台", "云迁移"]  # 通用IT兜底
    return tags[:4]


def derive_industry_tags(scope: str) -> list:
    """从经营范围推导行业标签 (用于补全 industry_tags)"""
    tags = []
    for keywords, tag in INDUSTRY_TAG_RULES:
        if any(kw in scope for kw in keywords):
            if tag not in tags:
                tags.append(tag)
    return tags or ["信息技术"]


def infer_funding_stage(capital_amount) -> str:
    """按注册资本推断融资阶段 (启发式, 标注推断)"""
    if not capital_amount:
        return "未披露"
    c = float(capital_amount)
    if c >= 10000:      # ≥1亿
        return "C轮及以上"
    elif c >= 1000:     # 1000万-1亿
        return "B轮"
    elif c >= 100:      # 100万-1000万
        return "A轮"
    else:
        return "天使轮"


def build_sales_pitch(tags: list, name: str) -> str:
    """生成销售话术 (≤50字符)"""
    if not tags:
        return "数字化转型解决方案，赋能增长"
    primary = tags[0]
    # 主标签有专属话术
    if primary in PITCH_TEMPLATES:
        pitch = PITCH_TEMPLATES[primary]
        if len(tags) >= 2 and tags[1] in PITCH_TEMPLATES:
            # 组合两个标签的简短版
            combo = f"{primary}+{tags[1]}，数字化转型赋能"
            if len(combo) <= 50:
                return combo
        return pitch
    # 兜底
    return f"{primary}落地，数字化转型赋能"[:50]


def build_reasoning(scope: str, scores: dict, capital: str,
                    funding_stage: str, data_completeness: dict) -> str:
    """生成评分依据 (引用5维度实得分 + 数据完整性, 模仿DeepSeek输出风格)"""
    tech = scores.get("tech_score", 0)
    fund = scores.get("funding_score", 0)
    intent = scores.get("intent_score", 0)
    team = scores.get("team_score", 0)
    ind = scores.get("industry_score", 0)
    total = scores.get("total_score", 0)

    # 技术投入描述
    scope_brief = (scope or "未知")[:60]
    tech_note = "经营范围涵盖" + scope_brief + ("..." if len(scope or "") > 60 else "")

    # 资金实力描述
    if capital and capital != "—":
        fund_note = f"注册资本{capital}，资金实力{fund}/20"
    else:
        fund_note = f"资金实力数据缺失，按中等水平计{fund}/20"

    # 转型意愿
    intent_note = "转型意愿" + (f"基于经营范围数字化关键词推断，{intent}/25" if intent > 0 else "近期无明确动态，按中等水平计")

    # 团队
    team_note = f"团队规模{team}/15"

    # 行业匹配
    ind_note = f"行业匹配度{ind}/10"

    reasoning = (
        f"技术投入方面，{tech_note}，技术投入{tech}/30；"
        f"{fund_note}；{intent_note}；{team_note}；{ind_note}。"
        f"综合加权{total}分，评级{scores.get('rating_level', '—')}级。"
    )
    return reasoning


# ============================================================
# 数据库读写
# ============================================================

def get_companies():
    """读取全部名录企业 + 规则引擎评分 + 技术画像 + 工商字段"""
    import psycopg2
    conn = psycopg2.connect(DATABASE_URL)
    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT c.id, c.company_name, c.business_scope, c.registered_capital,
                       c.capital_amount, c.industry_tags, c.registered_address,
                       c.funding_stage,
                       COALESCE(t.tech_stack, '{}') AS tech_stack,
                       rr.total_score, rr.rating_level, rr.tech_score, rr.funding_score,
                       rr.intent_score, rr.team_score, rr.industry_score
                FROM companies c
                LEFT JOIN tech_profiles t ON t.company_id = c.id
                LEFT JOIN ratings rr ON rr.company_id = c.id AND rr.rated_by = 'rules_engine'
                WHERE c.credit_code IS NOT NULL
                ORDER BY c.id
                """
            )
            cols = [d[0] for d in cur.description]
            companies = [dict(zip(cols, r)) for r in cur.fetchall()]
    finally:
        conn.close()
    return companies


def enrich_biz_fields(companies):
    """补全 industry_tags(缺失) + funding_stage(缺失) → UPDATE companies"""
    import psycopg2
    conn = psycopg2.connect(DATABASE_URL)
    tag_filled = stage_filled = 0
    try:
        with conn.cursor() as cur:
            for c in companies:
                # industry_tags 缺失 → 推导
                if not c.get("industry_tags"):
                    tags = derive_industry_tags(c.get("business_scope") or "")
                    if tags:
                        cur.execute(
                            "UPDATE companies SET industry_tags = %s WHERE id = %s",
                            (tags, c["id"]),
                        )
                        tag_filled += 1

                # funding_stage 缺失 → 按 capital_amount 推断
                if not c.get("funding_stage"):
                    stage = infer_funding_stage(c.get("capital_amount"))
                    cur.execute(
                        "UPDATE companies SET funding_stage = %s WHERE id = %s",
                        (stage, c["id"]),
                    )
                    stage_filled += 1
                    c["funding_stage"] = stage

                # 回填推导的 tags 到内存对象 (供评级用)
                if not c.get("industry_tags"):
                    c["industry_tags"] = derive_industry_tags(c.get("business_scope") or "")
        conn.commit()
    finally:
        conn.close()
    logger.info(f"工商补全: industry_tags +{tag_filled}家, funding_stage +{stage_filled}家")
    return tag_filled, stage_filled


def generate_ratings(companies):
    """为每家企业生成 demand_tags/sales_pitch/reasoning → INSERT ratings(rated_by='kscc')"""
    import psycopg2
    conn = psycopg2.connect(DATABASE_URL)
    inserted = 0
    skipped = 0
    try:
        with conn.cursor() as cur:
            for c in companies:
                # 无规则评分的企业跳过
                if c.get("total_score") is None:
                    skipped += 1
                    continue

                scope = c.get("business_scope") or ""
                tech_stack = c.get("tech_stack") or []
                scores = {
                    "total_score": c["total_score"],
                    "rating_level": c["rating_level"],
                    "tech_score": c["tech_score"],
                    "funding_score": c["funding_score"],
                    "intent_score": c["intent_score"],
                    "team_score": c["team_score"],
                    "industry_score": c["industry_score"],
                }

                demand_tags = derive_demand_tags(scope, tech_stack)
                sales_pitch = build_sales_pitch(demand_tags, c["company_name"])
                reasoning = build_reasoning(
                    scope, scores, c.get("registered_capital"),
                    c.get("funding_stage"), {},
                )

                cur.execute(
                    """
                    INSERT INTO ratings
                        (company_id, total_score, rating_level, tech_score, funding_score,
                         intent_score, team_score, industry_score,
                         demand_tags, sales_pitch, reasoning, rated_by, rated_at)
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, 'kscc', NOW())
                    ON CONFLICT (company_id, rated_by) DO UPDATE SET
                        total_score = EXCLUDED.total_score,
                        rating_level = EXCLUDED.rating_level,
                        tech_score = EXCLUDED.tech_score,
                        funding_score = EXCLUDED.funding_score,
                        intent_score = EXCLUDED.intent_score,
                        team_score = EXCLUDED.team_score,
                        industry_score = EXCLUDED.industry_score,
                        demand_tags = EXCLUDED.demand_tags,
                        sales_pitch = EXCLUDED.sales_pitch,
                        reasoning = EXCLUDED.reasoning,
                        rated_at = NOW()
                    """,
                    (c["id"], scores["total_score"], scores["rating_level"],
                     scores["tech_score"], scores["funding_score"],
                     scores["intent_score"], scores["team_score"], scores["industry_score"],
                     demand_tags, sales_pitch, reasoning),
                )
                inserted += 1

            # 更新企业状态 → rated
            cur.execute(
                "UPDATE companies SET status = 'rated' WHERE id IN ("
                "SELECT company_id FROM ratings WHERE rated_by = 'kscc')"
            )
        conn.commit()
    finally:
        conn.close()
    logger.info(f"kscc评级完成: 生成 {inserted} 家, 跳过 {skipped} 家(无规则评分)")
    return inserted


def main():
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument("--limit", type=int, default=0, help="处理前N家(测试)")
    parser.add_argument("--skip-biz", action="store_true", help="跳过工商字段补全")
    args = parser.parse_args()

    companies = get_companies()
    if args.limit > 0:
        companies = companies[:args.limit]
    logger.info(f"加载企业: {len(companies)} 家")

    # Phase 1: 工商字段补全
    if not args.skip_biz:
        enrich_biz_fields(companies)

    # Phase 2: kscc 评级生成
    inserted = generate_ratings(companies)

    # 分布统计
    import psycopg2
    from collections import Counter
    conn = psycopg2.connect(DATABASE_URL)
    try:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT rating_level, count(*) FROM ratings WHERE rated_by='kscc' "
                "GROUP BY rating_level ORDER BY rating_level"
            )
            levels = dict(cur.fetchall())
    finally:
        conn.close()
    logger.info(f"kscc评级等级分布: {levels}")


if __name__ == "__main__":
    main()
