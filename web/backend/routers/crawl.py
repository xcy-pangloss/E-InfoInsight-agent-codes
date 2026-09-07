"""页面一：发起爬取/评分指令

任务在后端独立运行（task_manager），与前端 SSE 连接解耦。
切页面只断开订阅，不断任务；关闭整个后端/网页服务才中断任务。

端点：
  POST /api/crawl/run     — 启动后台任务（立即返回）
  GET  /api/crawl/stream  — SSE 订阅日志流（先历史后实时）
  GET  /api/crawl/status  — 当前任务状态
  POST /api/crawl/stop    — 停止当前任务
  POST /api/crawl/chat    — Hermes 对话理解意图（无 terminal，纯建议）
"""

import logging
from fastapi import APIRouter, Query
from typing import Optional
from sse_starlette.sse import EventSourceResponse

from db import query, query_one, serialize
from services.task_manager import start_task, stop_task, get_status, subscribe
from services.task_runner import list_tasks
from services.hermes_service import chat as hermes_chat

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/crawl", tags=["爬取指令"])


@router.get("/tasks-list")
def tasks_list():
    """受控任务清单"""
    return {"items": list_tasks()}


@router.get("/presets")
def presets():
    """预设指令快捷按钮"""
    return {"items": [
        {"label": "增量爬取", "prompt": "增量爬取未完成企业四维数据", "desc": "断点续跑"},
        {"label": "分批爬取50家", "prompt": "前50家企业分批爬取", "desc": "小批量验证"},
        {"label": "规则评分", "prompt": "对全部企业运行规则引擎评分", "desc": "零token"},
        {"label": "DeepSeek评级", "prompt": "达标企业deepseek深度评级", "desc": "省token"},
        {"label": "导出报告", "prompt": "导出评级结果报告线索", "desc": "CSV输出"},
    ]}


@router.post("/run")
async def crawl_run(body: dict):
    """启动后台任务（立即返回，不阻塞）"""
    prompt = body.get('prompt', '').strip()
    if not prompt:
        return {"error": "指令不能为空"}
    return start_task(prompt)


@router.get("/stream")
async def crawl_stream():
    """SSE 订阅日志流。先吐历史日志再实时跟随，断开重连可补看。"""
    async def event_gen():
        try:
            async for line in subscribe():
                yield {"event": "log", "data": line}
        except Exception as e:
            yield {"event": "error", "data": str(e)}

    return EventSourceResponse(event_gen())


@router.get("/status")
def crawl_status():
    """当前任务状态"""
    return get_status()


@router.post("/stop")
async def crawl_stop():
    """停止当前任务"""
    return await stop_task()


@router.post("/chat")
async def crawl_chat(body: dict):
    """Hermes 对话理解（无 terminal）。返回建议，不执行。"""
    prompt = body.get('prompt', '').strip()
    if not prompt:
        return {"error": "指令不能为空"}

    async def event_gen():
        try:
            async for line in hermes_chat(prompt):
                yield {"event": "log", "data": line}
        except Exception as e:
            yield {"event": "error", "data": str(e)}
        finally:
            yield {"event": "done", "data": "结束"}

    return EventSourceResponse(event_gen())


@router.get("/records")
def crawl_records(
    status: Optional[str] = Query(None),
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=200),
):
    """历史爬取记录"""
    where = []
    params: list = []
    if status:
        where.append("status = %s"); params.append(status)
    where_sql = ("WHERE " + " AND ".join(where)) if where else ""

    total = query_one(f"SELECT count(*) AS n FROM crawl_tasks {where_sql}", tuple(params))
    total_count = total.get("n", 0) if total else 0
    rows = query(f"""
        SELECT * FROM crawl_tasks {where_sql}
        ORDER BY created_at DESC NULLS LAST
        LIMIT %s OFFSET %s
    """, tuple(params + [page_size, (page - 1) * page_size]))
    return {"total": total_count, "items": [serialize(r) for r in rows]}
