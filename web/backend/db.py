"""数据库连接池 + 通用查询工具"""

import os
import json
import logging
import psycopg2
from psycopg2 import pool
from dotenv import load_dotenv
from typing import Optional, List, Dict, Any

load_dotenv(os.path.join(os.path.dirname(__file__), '..', '..', '.env'))

logger = logging.getLogger(__name__)

# 连接池（延迟初始化）
_pool: Optional[pool.SimpleConnectionPool] = None

DATABASE_URL = os.getenv(
    'DATABASE_URL',
    'postgresql://kc@localhost:5432/rating_system'
)


def get_pool() -> pool.SimpleConnectionPool:
    global _pool
    if _pool is None or _pool.closed:
        _pool = pool.SimpleConnectionPool(
            minconn=1, maxconn=10, dsn=DATABASE_URL
        )
    return _pool


def get_conn():
    return get_pool().getconn()


def put_conn(conn):
    get_pool().putconn(conn)


def query(sql: str, params: tuple = None) -> List[Dict[str, Any]]:
    """执行查询，返回字典列表"""
    conn = get_conn()
    try:
        with conn.cursor() as cur:
            cur.execute(sql, params)
            columns = [desc[0] for desc in cur.description]
            return [dict(zip(columns, row)) for row in cur.fetchall()]
    finally:
        put_conn(conn)


def query_one(sql: str, params: tuple = None) -> Optional[Dict[str, Any]]:
    """执行查询，返回单条"""
    conn = get_conn()
    try:
        with conn.cursor() as cur:
            cur.execute(sql, params)
            columns = [desc[0] for desc in cur.description]
            row = cur.fetchone()
            return dict(zip(columns, row)) if row else None
    finally:
        put_conn(conn)


def serialize(row: dict) -> dict:
    """将 PostgreSQL 特殊类型转为 JSON 可序列化"""
    result = {}
    for k, v in row.items():
        if isinstance(v, (list, tuple)):
            result[k] = list(v)
        elif hasattr(v, 'isoformat'):
            result[k] = v.isoformat()
        elif isinstance(v, (int, float, str, bool, type(None))):
            result[k] = v
        else:
            result[k] = str(v)
    return result
