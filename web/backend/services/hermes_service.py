"""Hermes 对话服务 — 仅用于自然语言理解，不执行脚本

架构：
  用户输入自然语言 → Hermes(仅 web+skill 工具)理解意图并返回结构化建议
  → 后端 task_runner 把意图映射到受控 subprocess 脚本执行

Hermes 不持有 terminal 工具，架构上无法修改任何文件。
"""

import os
import logging
from typing import AsyncIterator

logger = logging.getLogger(__name__)

PROJECT_ROOT = os.path.abspath(
    os.path.join(os.path.dirname(__file__), '..', '..', '..')
)
WRITE_SAFE_ROOT = os.path.join(PROJECT_ROOT, 'data')

HERMES_BIN = os.environ.get('HERMES_BIN', '/Users/kc/.local/bin/hermes')

# 关键：只给 web 工具，不给 terminal
# Hermes 能搜索、读取 skill、做推理，但无法执行任何命令或写文件
ALLOWED_TOOLSETS = "web"


def build_env() -> dict:
    env = os.environ.copy()
    env['HERMES_WRITE_SAFE_ROOT'] = WRITE_SAFE_ROOT
    env['HERMES_WORKDIR'] = PROJECT_ROOT
    return env


def build_command(prompt: str, skills: str = "rating-pipeline") -> list:
    """构建 Hermes 对话命令（纯理解模式，无执行能力）"""
    system_hint = (
        "你是武汉IT企业评级系统的智能助手。用户会描述想执行的爬取或评级任务，"
        "请你理解意图并用简洁语言告诉用户应该执行什么操作。"
        "可用操作：增量爬取、分批爬取、规则评分、DeepSeek评级、导出报告。"
        "不要尝试执行任何命令，只给出建议。"
    )
    return [
        HERMES_BIN, 'chat',
        '-q', f"{system_hint}\n\n用户指令：{prompt}",
        '-t', ALLOWED_TOOLSETS,   # 仅 web，无 terminal
        '-s', skills,
        '-Q',
    ]


async def chat(prompt: str, skills: str = "rating-pipeline") -> AsyncIterator[str]:
    """与 Hermes 对话，流式产出回复。Hermes 只做理解，不执行。"""
    import asyncio

    cmd = build_command(prompt, skills)
    env = build_env()

    logger.info(f"Hermes 对话模式 (无 terminal): prompt={prompt[:60]}...")

    proc = await asyncio.create_subprocess_exec(
        *cmd,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.STDOUT,
        env=env,
        cwd=PROJECT_ROOT,
    )

    try:
        while True:
            line = await proc.stdout.readline()
            if not line:
                break
            yield line.decode('utf-8', errors='replace').rstrip('\n')
        await proc.wait()
    except asyncio.CancelledError:
        proc.terminate()
        yield "[hermes] 对话已停止"
        raise
