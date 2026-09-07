"""任务管理器 — 后端独立运行爬取/评分任务，与前端连接解耦

任务在后端常驻运行，日志写入内存缓冲 + crawl_tasks 表。
前端通过 SSE 订阅日志流，切页面断开只影响订阅，不影响任务执行。
任务仅在进程退出（关闭整个后端/网页服务）时中断。
"""

import os
import asyncio
import logging
import psycopg2
from typing import Optional
from datetime import datetime

logger = logging.getLogger(__name__)

from services.task_runner import match_task, run_task

# 数据库连接（从 db 模块取 URL）
from db import DATABASE_URL

# 单任务串行（评级系统资源敏感，不并发跑）
_current: Optional[dict] = None
# 日志缓冲：任务运行中累积所有日志行，新订阅者可读到历史
_log_buffer: list = []
# 当前订阅该任务日志流的 asyncio.Queue 列表
_subscribers: list = []
# 任务 asyncio.Task 句柄
_task_handle: Optional[asyncio.Task] = None
# crawl_tasks 表中的记录 ID
_task_db_id: Optional[int] = None

MAX_BUFFER = 5000


def _db_exec(sql: str, params: tuple = None, fetch: bool = False):
    """同步写库（短操作，不用连接池）。fetch=True 时返回 RETURNING 的首行"""
    try:
        conn = psycopg2.connect(DATABASE_URL)
        with conn.cursor() as cur:
            cur.execute(sql, params)
            result = cur.fetchone() if fetch else None
            conn.commit()
            return result
    except Exception as e:
        logger.warning(f"crawl_tasks 写库失败: {e}")
        return None
    finally:
        try:
            conn.close()
        except:
            pass


def get_status() -> dict:
    """当前任务状态"""
    if not _current:
        return {"running": False}
    return {
        "running": _task_handle is not None and not _task_handle.done(),
        "label": _current.get("label"),
        "key": _current.get("key"),
        "desc": _current.get("desc"),
        "started_at": _current.get("started_at"),
        "log_count": len(_log_buffer),
    }


def get_history_logs(limit: int = 200) -> list:
    """读取历史日志（供新订阅者补看）"""
    if not _log_buffer:
        return []
    return _log_buffer[-limit:]


async def _emit(line: str):
    """写日志到缓冲并广播给所有订阅者"""
    ts = datetime.now().strftime("%H:%M:%S")
    entry = f"[{ts}] {line}"
    _log_buffer.append(entry)
    if len(_log_buffer) > MAX_BUFFER:
        del _log_buffer[: len(_log_buffer) - MAX_BUFFER]
    # 广播给所有订阅队列
    dead = []
    for q in _subscribers:
        try:
            q.put_nowait(entry)
        except asyncio.QueueFull:
            dead.append(q)
    for q in dead:
        if q in _subscribers:
            _subscribers.remove(q)


def _write_task_to_db(task: dict, status: str, result_summary: str = None,
                      started_at: str = None, completed_at: str = None):
    """写入或更新 crawl_tasks 记录"""
    global _task_db_id
    if _task_db_id is None:
        # 插入新记录
        row = _db_exec("""
            INSERT INTO crawl_tasks (task_type, source_name, status, result_summary, started_at)
            VALUES (%s, %s, %s, %s, %s)
            RETURNING id
        """, (task.get("key", "unknown"), "web_ui", status, result_summary, started_at), fetch=True)
        if row:
            _task_db_id = row[0]
            logger.info(f"crawl_tasks 插入: id={_task_db_id}, status={status}")
        else:
            logger.warning(f"crawl_tasks 插入失败: task={task.get('key')}, status={status}")
    else:
        # 更新已有记录
        _db_exec("""
            UPDATE crawl_tasks SET status = %s, result_summary = %s,
                completed_at = %s, started_at = COALESCE(started_at, %s)
            WHERE id = %s
        """, (status, result_summary, completed_at, started_at, _task_db_id), fetch=False)
        logger.info(f"crawl_tasks 更新: id={_task_db_id}, status={status}")


async def _run_in_background(task: dict):
    """后台执行任务，日志经 _emit 广播，状态写 crawl_tasks"""
    global _current, _task_db_id
    _current = task
    started = datetime.now()
    _current["started_at"] = started.strftime("%Y-%m-%d %H:%M:%S")
    _task_db_id = None

    # 写库：running
    _write_task_to_db(task, "running", started_at=_current["started_at"])

    exit_code = None
    try:
        async for line in run_task(task):
            await _emit(line)
    except asyncio.CancelledError:
        await _emit("[runner] 任务被取消")
        _write_task_to_db(task, "failed", "任务被用户取消",
                          completed_at=datetime.now().strftime("%Y-%m-%d %H:%M:%S"))
        raise
    except Exception as e:
        await _emit(f"[runner] 任务异常: {e}")
        _write_task_to_db(task, "failed", f"任务异常: {e}",
                          completed_at=datetime.now().strftime("%Y-%m-%d %H:%M:%S"))
    else:
        # 从日志提取结果摘要
        summary = _extract_summary()
        _write_task_to_db(task, "completed", summary,
                          completed_at=datetime.now().strftime("%Y-%m-%d %H:%M:%S"))
    finally:
        # 兜底：确保状态不卡在 running
        _ensure_db_status()
        await _emit("[runner] 任务结束")
        _task_handle_set(None)


def _ensure_db_status():
    """兜底：若 crawl_tasks 记录仍为 running，查实际状态并修正"""
    global _task_db_id
    if _task_db_id is None:
        return
    try:
        conn = psycopg2.connect(DATABASE_URL)
        with conn.cursor() as cur:
            cur.execute("SELECT status FROM crawl_tasks WHERE id = %s", (_task_db_id,))
            row = cur.fetchone()
            if row and row[0] == "running":
                # 仍为 running 说明 completed 更新没生效，强制修正
                summary = _extract_summary()
                cur.execute("""
                    UPDATE crawl_tasks SET status = 'completed', result_summary = %s,
                        completed_at = COALESCE(completed_at, now())
                    WHERE id = %s
                """, (summary, _task_db_id))
                conn.commit()
                logger.info(f"兜底修正 crawl_tasks#{_task_db_id} → completed")
    except Exception as e:
        logger.warning(f"兜底状态修正失败: {e}")
    finally:
        try:
            conn.close()
        except:
            pass


def _extract_summary() -> str:
    """从日志缓冲提取结果摘要（最后几行关键信息）"""
    # 取日志中含数字的行（如"评分完成: 总计98家"）
    key_lines = []
    for line in _log_buffer:
        stripped = line.strip()
        if any(kw in stripped for kw in ['完成', '总计', '成功', '失败', '评级', '评分', '分布', '入库', '导出']):
            # 去掉时间戳前缀
            clean = stripped.split('] ', 1)[-1] if '] ' in stripped else stripped
            key_lines.append(clean)
    if key_lines:
        return '; '.join(key_lines[-5:])
    # 兜底：取最后3行
    tail = [l.split('] ', 1)[-1] if '] ' in l else l for l in _log_buffer[-3:]]
    return '; '.join(t.strip() for t in tail if t.strip())


def _task_handle_set(h):
    global _task_handle
    _task_handle = h


def start_task(prompt: str) -> dict:
    """启动任务（若已有任务在跑则拒绝）"""
    global _task_handle, _log_buffer, _subscribers
    if _task_handle is not None and not _task_handle.done():
        return {"error": "已有任务在运行，请等待完成或先停止"}

    task = match_task(prompt)
    if not task:
        return {"error": "未匹配到可执行任务，请使用预设指令或明确说明（爬取/评分/导出）"}

    # 重置缓冲和订阅
    _log_buffer = []
    _subscribers = []
    _task_handle = asyncio.create_task(_run_in_background(task))
    logger.info(f"后台任务已启动: {task['label']}")
    return {"ok": True, "label": task["label"], "key": task["key"]}


async def stop_task() -> dict:
    """停止当前任务"""
    global _task_handle
    if _task_handle is None or _task_handle.done():
        return {"error": "没有运行中的任务"}
    _task_handle.cancel()
    try:
        await _task_handle
    except asyncio.CancelledError:
        pass
    _task_handle = None
    return {"ok": True, "msg": "任务已停止"}


async def subscribe():
    """订阅日志流：先吐历史日志，再实时跟随"""
    q = asyncio.Queue(maxsize=1000)
    _subscribers.append(q)
    try:
        # 先发送历史日志
        for line in list(_log_buffer):
            yield line
        # 实时跟随
        while True:
            line = await q.get()
            yield line
            # 任务结束后继续读几秒确保缓冲排空
            if "[runner] 任务结束" in line:
                break
    finally:
        if q in _subscribers:
            _subscribers.remove(q)
