#!/usr/bin/env python3
"""批量补全 5号名录企业工商数据 — credit_code/法人/地址/成立日期/注册资本

数据来源 (按优先级):
1. 百度百科 API — 稳定, 覆盖约60% (含信用代码/法人/地址)
2. 东方财富 F10 — 上市公司 (股票代码已知)
3. 水滴信用 shuidi.cn 详情页 (通过必应搜索定位)

输出: data/potential_companies/biz_info_5.csv
用法: python scripts/fetch_biz_info.py [--limit N]
"""
import os
import re
import csv
import sys
import time
import json
import logging
import requests
from urllib.parse import quote_plus

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger(__name__)

PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, os.path.join(PROJECT_ROOT, "scripts"))
from incremental_crawl_and_score import random_headers

CSV_PATH = os.path.join(PROJECT_ROOT, "data", "potential_companies", "5_smart_manufacturing.csv")
OUT_PATH = os.path.join(PROJECT_ROOT, "data", "potential_companies", "biz_info_5.csv")

CODE_RE = re.compile(r"^(91|92)\d{6}[0-9A-HJ-NP-RTUW-Y]{10}$")

# 上市公司股票代码映射 (5号名录) — 东财F10需要交易所前缀
STOCK_MAP = {
    "武汉华中数控股份有限公司": "SZ300161",
    "东风汽车集团有限公司": None,  # 未上市主体
    "长飞光纤光缆股份有限公司": "SH601869",
}


def fetch_baike(name: str) -> dict:
    """百度百科 API 提取工商信息"""
    url = (f"https://baike.baidu.com/api/openapi/BaikeLemmaCardApi"
           f"?scope=103&format=json&appid=379020&bk_key={quote_plus(name)}&bk_length=600")
    try:
        resp = requests.get(url, headers=random_headers(), timeout=8)
        data = resp.json()
        if not data.get("title"):
            return None
        info = {"found": True, "source": "baike"}
        card_map = {}
        for item in data.get("card", []):
            if isinstance(item, dict):
                nm = item.get("name", "")
                vals = item.get("value", [])
                if nm and vals:
                    card_map[nm] = vals[0] if isinstance(vals, list) else vals

        info["company_name"] = name
        info["credit_code"] = ""
        info["established"] = ""
        info["capital"] = ""
        info["employee_count"] = None

        # D增强: 从 card_map 直接取注册信息 (百科结构化字段)
        # card_map 值含 HTML 如 "<sup>24</sup>", 需清洗
        def clean_html(s):
            """去除HTML标签 + 尾随脚注数字(百科上标引用编号, 可能有多个<sup>合并为长数字串)"""
            t = re.sub(r'<[^>]+>', '', str(s)).strip()
            # 尾随脚注: 多个<sup>N</sup>合并后变长数字串。反复剥离"空格+数字"和"中文/字母后数字"
            prev = None
            while prev != t:
                prev = t
                t = re.sub(r'\s+\d{1,6}$', '', t)
                t = re.sub(r'([一-龥a-zA-Z元万亿])(\d{1,6})$', r'\1', t)
            return t

        info["legal_rep"] = clean_html(card_map.get("法定代表人", ""))[:30]
        info["address"] = clean_html(card_map.get("总部地点", ""))[:120]

        for key in ("注册资本", "注册资金"):
            cap_val = clean_html(card_map.get(key, ""))
            if cap_val:
                info["capital"] = cap_val
                break
        for key in ("成立时间", "成立日期"):
            est_val = clean_html(card_map.get(key, ""))
            if est_val:
                # 规范化: "2000年11月13日" → "2000-11-13"
                m = re.search(r'(\d{4})[年/\-](\d{1,2})[月/\-](\d{1,2})', est_val)
                if m:
                    info["established"] = f"{int(m.group(1)):04d}-{int(m.group(2)):02d}-{int(m.group(3)):02d}"
                else:
                    info["established"] = est_val.replace("年", "-").replace("月", "-").replace("日", "")
                break
        # 参保人数 (百科偶有此字段)
        for key in ("参保人数", "社保人数", "员工人数", "人员规模"):
            emp_val = clean_html(card_map.get(key, ""))
            if emp_val:
                m = re.search(r'(\d+)', emp_val)
                if m:
                    val = int(m.group(1))
                    if 1 <= val <= 500000:
                        info["employee_count"] = val
                        break

        # 成立日期/注册资本从摘要提取 (card_map 没有时回退到摘要)
        abstract = clean_html(data.get("abstract", "") or "")
        if not info["established"]:
            m = re.search(r"成立于(\d{4})[年/\-](\d{1,2})[月/\-](\d{1,2})", abstract)
            if m:
                info["established"] = f"{int(m.group(1)):04d}-{int(m.group(2)):02d}-{int(m.group(3)):02d}"
            else:
                m = re.search(r"(\d{4})年(\d{1,2})月(\d{1,2})日.*成立", abstract)
                if m:
                    info["established"] = f"{int(m.group(1)):04d}-{int(m.group(2)):02d}-{int(m.group(3)):02d}"
        if not info["capital"]:
            # D增强: 多种资本格式, 容忍中间词(为/约/是)
            for pat, fmt in [
                (r"注册(?:资本|资金)[为约是]?\s*([\d.]+)\s*亿[元人民币]?", "亿元"),
                (r"注册(?:资本|资金)[为约是]?\s*([\d.]+)\s*万[元人民币]?", "万元"),
                (r"([\d.]+)\s*万[元人民币]\s*人民币", "万元"),
            ]:
                m = re.search(pat, abstract)
                if m:
                    info["capital"] = f"{m.group(1)}{fmt}"
                    break

        # 信用代码从全文本提取
        full_text = abstract + json.dumps(card_map, ensure_ascii=False)
        codes = re.findall(r"[0-9A-HJ-NP-RTUW-Y]{18}", full_text)
        valid = [c for c in set(codes) if CODE_RE.match(c)]
        if valid:
            info["credit_code"] = valid[0]
        return info
    except Exception as e:
        logger.debug(f"百科失败 {name}: {e}")
        return None


def fetch_eastmoney(stock_code: str, name: str) -> dict:
    """东方财富 F10 — 上市公司工商信息"""
    try:
        url = f"https://emweb.securities.eastmoney.com/PC_HSF10/CompanySurvey/PageAjax?code={stock_code}"
        resp = requests.get(url, headers=random_headers(), timeout=8)
        data = resp.json()
        jb = data.get("jbzl", [{}])[0]
        info = {
            "found": True,
            "source": "eastmoney",
            "company_name": name,
            "credit_code": jb.get("REG_NUM", ""),
            "legal_rep": jb.get("LEGAL_PERSON", ""),
            "address": (jb.get("REG_ADDRESS") or "")[:120],
            "capital": f"{jb.get('REG_CAPITAL', '')}万元",
        }
        fx = data.get("fxxg", [{}])
        if fx:
            fd = fx[0].get("FOUND_DATE", "")
            if fd:
                info["established"] = fd[:10]
        else:
            info["established"] = ""
        return info
    except Exception as e:
        logger.debug(f"东财失败 {name}: {e}")
        return None


def main():
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument("--limit", type=int, default=0, help="只处理前N家(测试用)")
    args = parser.parse_args()

    with open(CSV_PATH, encoding="utf-8-sig") as f:
        rows = list(csv.DictReader(f))
    if args.limit > 0:
        rows = rows[:args.limit]

    logger.info(f"待处理企业: {len(rows)} 家")

    results = []
    for i, row in enumerate(rows, 1):
        name = (row.get("company_name") or "").strip()
        if not name:
            continue

        info = None
        # 1. 东方财富 (上市公司)
        stock = STOCK_MAP.get(name)
        if stock:
            info = fetch_eastmoney(stock, name)
            time.sleep(0.5)
        # 2. 百度百科
        if not info or not info.get("credit_code"):
            info = fetch_baike(name)
            time.sleep(0.5)

        if info and info.get("found"):
            results.append(info)
            logger.info(f"[{i}/{len(rows)}] ✓ {name}: code={info['credit_code'] or '∅'}, "
                        f"法人={info.get('legal_rep','')[:10] or '∅'}, 地址={info.get('address','')[:25] or '∅'}")
        else:
            results.append({
                "found": False, "source": "", "company_name": name,
                "credit_code": "", "legal_rep": "", "address": "",
                "established": "", "capital": "",
            })
            logger.info(f"[{i}/{len(rows)}] ✗ {name}: 未找到")

    with open(OUT_PATH, "w", newline="", encoding="utf-8-sig") as f:
        cols = ["company_name", "credit_code", "legal_rep", "address", "established", "capital", "source"]
        writer = csv.DictWriter(f, fieldnames=cols)
        writer.writeheader()
        for r in results:
            writer.writerow({k: r.get(k, "") for k in cols})

    found_n = sum(1 for r in results if r.get("found"))
    code_n = sum(1 for r in results if r.get("credit_code"))
    logger.info(f"完成: 命中 {found_n}/{len(results)}, 含信用代码 {code_n} 家 → {OUT_PATH}")


if __name__ == "__main__":
    main()
