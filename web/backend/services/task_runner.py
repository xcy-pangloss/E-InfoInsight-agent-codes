"""受控任务执行器 — 把自然语言意图映射到预定义脚本，subprocess 执行

设计：Hermes 仅做对话理解（无 terminal），实际脚本执行由后端 subprocess 完成。
这样 Hermes 架构上接触不到文件系统，绝对保证不改代码。
"""

import os
import re
import logging
import asyncio
from typing import Optional, AsyncIterator

logger = logging.getLogger(__name__)

PROJECT_ROOT = os.path.abspath(
    os.path.join(os.path.dirname(__file__), '..', '..', '..')
)
PYTHON = os.path.join(PROJECT_ROOT, '.venv', 'bin', 'python')

# 预定义受控任务 — 意图关键词 → 脚本
TASKS = [
    {
        'key': 'crawl_incremental',
        'keywords': ['增量爬取', '爬取', '采集', 'crawl', '四维'],
        'label': '增量爬取',
        'cmd': [PYTHON, 'scripts/crawl_1000.py', '--resume'],
        'desc': '断点续跑未完成企业的四维爬取',
    },
    {
        'key': 'crawl_batch',
        'keywords': ['前50', '前100', '小批量', '分批'],
        'label': '分批爬取',
        'cmd': [PYTHON, 'scripts/crawl_1000.py', '--limit', '50'],
        'desc': '爬取前50家企业',
    },
    {
        'key': 'score_rules',
        'keywords': ['规则评分', '规则引擎', '评分', 'score'],
        'label': '规则评分',
        'cmd': [PYTHON, 'engine/rules_engine.py', '--mode', 'full'],
        'desc': '全量规则引擎评分',
    },
    {
        'key': 'score_full',
        'keywords': ['评级', 'deepseek', '深度评级', 'analyze'],
        'label': '规则+DeepSeek评分',
        'cmd': [PYTHON, 'scripts/score_1000.py', '--threshold', '40'],
        'desc': '规则评分 + 达标企业DeepSeek评级',
    },
    {
        'key': 'export_report',
        'keywords': ['报告', '导出', '线索', 'report', 'export'],
        'label': '导出报告',
        'cmd': [PYTHON, 'scripts/export_1000_results.py'],
        'desc': '导出评级结果CSV',
    },
]


def match_task(prompt: str) -> Optional[dict]:
    """从自然语言匹配受控任务"""
    p = prompt.lower()
    best, best_score = None, 0
    for t in TASKS:
        score = sum(1 for kw in t['keywords'] if kw in p)
        if score > best_score:
            best, best_score = t, score
    return best


def list_tasks() -> list:
    """返回受控任务清单（供前端展示）"""
    return [
        {'key': t['key'], 'label': t['label'], 'desc': t['desc']}
        for t in TASKS
    ]


async def run_task(task: dict) -> AsyncIterator[str]:
    """subprocess 执行受控脚本，流式产出日志"""
    cmd = task['cmd']
    logger.info(f"受控执行: {task['label']} → {' '.join(cmd)}")

    yield f"[runner] 启动任务：{task['label']}"
    yield f"[runner] 命令：{' '.join(os.path.basename(c) if i < 2 else c for i, c in enumerate(cmd))}"
    yield f"[runner] 说明：{task['desc']}"
    yield ""

    proc = await asyncio.create_subprocess_exec(
        *cmd,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.STDOUT,
        cwd=PROJECT_ROOT,
    )

    try:
        while True:
            line = await proc.stdout.readline()
            if not line:
                break
            yield line.decode('utf-8', errors='replace').rstrip('\n')
        await proc.wait()
        yield f"[runner] 完成，退出码 {proc.returncode}"
    except asyncio.CancelledError:
        proc.terminate()
        yield "[runner] 任务已停止"
        raise
