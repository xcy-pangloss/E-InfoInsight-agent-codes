"""配置与规则管理 — 读取和编辑 yaml 配置文件"""

import os
import yaml
import logging
from fastapi import APIRouter, Body
from typing import Any

logger = logging.getLogger(__name__)

PROJECT_ROOT = os.path.abspath(
    os.path.join(os.path.dirname(__file__), '..', '..', '..')
)

router = APIRouter(prefix="/api/config", tags=["配置管理"])

CONFIG_DIR = os.path.join(PROJECT_ROOT, 'config')

# 允许读写的配置文件白名单（安全：只允许这三个）
ALLOWED_FILES = {
    'scoring': 'scoring_rules.yaml',
    'keywords': 'industry_keywords.yaml',
    'global': 'config.yaml',
}


def _read_yaml(filename: str) -> dict:
    path = os.path.join(CONFIG_DIR, filename)
    with open(path, 'r', encoding='utf-8') as f:
        return yaml.safe_load(f) or {}


def _write_yaml(filename: str, data: dict):
    path = os.path.join(CONFIG_DIR, filename)
    with open(path, 'w', encoding='utf-8') as f:
        yaml.dump(data, f, allow_unicode=True, default_flow_style=False, sort_keys=False)
    logger.info(f"配置已写入: {filename}")


# ------------------------------------------------------------------
# 评分规则
# ------------------------------------------------------------------
@router.get("/scoring")
def get_scoring():
    """读取评分规则配置"""
    return _read_yaml(ALLOWED_FILES['scoring'])


@router.post("/scoring")
def save_scoring(data: dict = Body(...)):
    """保存评分规则配置"""
    _write_yaml(ALLOWED_FILES['scoring'], data)
    return {"ok": True, "msg": "评分规则已保存"}


# ------------------------------------------------------------------
# 行业关键词
# ------------------------------------------------------------------
@router.get("/keywords")
def get_keywords():
    """读取行业关键词库"""
    return _read_yaml(ALLOWED_FILES['keywords'])


@router.post("/keywords")
def save_keywords(data: dict = Body(...)):
    """保存行业关键词库"""
    _write_yaml(ALLOWED_FILES['keywords'], data)
    return {"ok": True, "msg": "关键词库已保存"}


# ------------------------------------------------------------------
# 全局配置
# ------------------------------------------------------------------
@router.get("/global")
def get_global():
    """读取全局配置"""
    return _read_yaml(ALLOWED_FILES['global'])


@router.post("/global")
def save_global(data: dict = Body(...)):
    """保存全局配置"""
    _write_yaml(ALLOWED_FILES['global'], data)
    return {"ok": True, "msg": "全局配置已保存"}


# ------------------------------------------------------------------
# 多机同步状态
# ------------------------------------------------------------------
@router.get("/sync-status")
def sync_status():
    """多机同步状态（读 data/sync 目录）"""
    sync_dir = os.path.join(PROJECT_ROOT, 'data', 'sync')
    if not os.path.exists(sync_dir):
        return {"exists": False, "files": []}
    files = []
    for f in sorted(os.listdir(sync_dir)):
        fp = os.path.join(sync_dir, f)
        if os.path.isfile(fp):
            stat = os.stat(fp)
            files.append({"name": f, "size": stat.st_size, "mtime": int(stat.st_mtime)})
    return {"exists": True, "files": files}
