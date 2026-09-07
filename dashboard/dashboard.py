#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
武汉IT企业智能评级 —— 实时可视化看板

读取 all_rated_companies.xlsx, 每 3 秒自动检测文件变化, 文件更新后页面自动刷新图表与表格。
依赖: openpyxl (用于读取 xlsx); 其余仅用 Python 标准库, 无需联网。

用法:
    python dashboard.py                          # 默认端口 8642
    python dashboard.py 9000                     # 指定端口
    python dashboard.py 9000 "D:/xxx/all_rated_companies.xlsx"   # 指定端口与xlsx路径

启动后浏览器自动打开 http://127.0.0.1:<端口>  (设环境变量 NO_OPEN=1 可禁止自动打开)

无控制台窗口方式(推荐): 双击本目录下的 启动看板.bat / 停止看板.bat
"""
import hashlib
import json
import os
import re
import sys
import threading
import time
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse

try:
    import openpyxl
except ImportError:
    sys.stderr.write("[错误] 缺少 openpyxl, 请先安装: pip install openpyxl\n")
    raise

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

DEFAULT_PORT = 8642
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
HTML_PATH = os.path.join(BASE_DIR, "index.html")
PID_PATH = os.path.join(BASE_DIR, "dashboard.pid")

# 需要从 xlsx 中读取的列 (顺序无关, 按表头名称匹配)
COL_COMPANY = "company_name"
COL_CREDIT = "credit_code"
COL_CAPITAL = "registered_capital"
COL_SCOPE = "business_scope"
COL_TAGS = "industry_tags"
COL_ADDRESS = "registered_address"
COL_FUNDING = "funding_stage"
COL_TOTAL = "total_score"
COL_RATING = "rating_level"
COL_TECH = "tech_score"
COL_FUND = "funding_score"
COL_INTENT = "intent_score"
COL_TEAM = "team_score"
COL_IND = "industry_score"
COL_DEMAND = "demand_tags"
COL_PITCH = "sales_pitch"
COL_REASON = "reasoning"
COL_CRAWL = "crawl_time"


def log(msg):
    """打印日志; 用 pythonw 运行时无控制台(stdout 为 None), 静默即可。"""
    if sys.stdout is not None:
        try:
            print(msg)
        except Exception:
            pass


CANDIDATE_XLSX = [
    os.path.normpath(os.path.join(BASE_DIR, "..", "data", "reports", "all_rated_companies.xlsx")),
    os.path.normpath(os.path.join(BASE_DIR, "data", "reports", "all_rated_companies.xlsx")),
    os.path.normpath(os.path.join(os.getcwd(), "data", "reports", "all_rated_companies.xlsx")),
]

XLSX_PATH = ""
_cache_key = None      # (mtime_ns, size), 文件未变时不重复解析
_cache_payload = None  # 上次成功解析的 JSON payload


def _cell(row, idx):
    """安全取单元格值, 越界或 None 返回空串。"""
    if idx is None or idx >= len(row):
        return ""
    v = row[idx]
    if v is None:
        return ""
    return str(v).strip()


def to_int(v):
    try:
        return int(float(str(v).strip()))
    except (TypeError, ValueError):
        return 0


try:
    import csv as _csv
except ImportError:  # csv 是标准库, 正常不会缺
    _csv = None


def parse_xlsx(path):
    """解析 xlsx 为列表。同名/同信用代码去重, 保留文件中最后一条(最新)。"""
    wb = openpyxl.load_workbook(path, read_only=True, data_only=True)
    try:
        ws = wb.active
        rows = ws.iter_rows(values_only=True)
        try:
            header = next(rows)
        except StopIteration:
            return []
        # 建立列名 -> 列索引映射
        idx = {}
        for i, h in enumerate(header):
            if h is not None:
                idx[str(h).strip()] = i

        def col(name):
            return idx.get(name)

        by_key = {}
        for row in rows:
            name = _cell(row, col(COL_COMPANY))
            if not name or name == "—":
                continue
            cc = _cell(row, col(COL_CREDIT))
            key = cc if cc and cc != "—" else name
            tags = [t.strip() for t in re.split(r"[,，]", _cell(row, col(COL_TAGS))) if t.strip() and t.strip() != "—"]
            demand = [t.strip() for t in re.split(r"[,，]", _cell(row, col(COL_DEMAND))) if t.strip() and t.strip() != "—"]
            by_key[key] = {
                "name": name,
                "score": to_int(_cell(row, col(COL_TOTAL))),
                "rating": _cell(row, col(COL_RATING)) or "—",
                "tags": tags,
                "demand": demand,
                "funding": _cell(row, col(COL_FUNDING)) or "—",
                "capital": _cell(row, col(COL_CAPITAL)) or "—",
                "address": _cell(row, col(COL_ADDRESS)) or "—",
                "scope": _cell(row, col(COL_SCOPE)) or "—",
                "tech": to_int(_cell(row, col(COL_TECH))),
                "fund": to_int(_cell(row, col(COL_FUND))),
                "intent": to_int(_cell(row, col(COL_INTENT))),
                "team": to_int(_cell(row, col(COL_TEAM))),
                "ind": to_int(_cell(row, col(COL_IND))),
                "pitch": _cell(row, col(COL_PITCH)),
                "reason": _cell(row, col(COL_REASON)),
                "crawl": _cell(row, col(COL_CRAWL)),
            }
        return list(by_key.values())
    finally:
        wb.close()


# ---------- CSV 解析 ----------

_CSV_ENCODINGS = ("gb18030", "utf-8-sig", "utf-8")


def parse_csv(path):
    """解析 csv 为列表, 字段与 parse_xlsx 相同。自动探测编码。"""
    for enc in _CSV_ENCODINGS:
        try:
            with open(path, encoding=enc, newline="") as f:
                reader = _csv.reader(f)
                header = next(reader)
                idx = {}
                for i, h in enumerate(header):
                    if h is not None:
                        idx[str(h).strip()] = i

                def col(name):
                    return idx.get(name)

                by_key = {}
                for row in reader:
                    name = _cell(row, col(COL_COMPANY))
                    if not name or name == "—":
                        continue
                    cc = _cell(row, col(COL_CREDIT))
                    key = cc if cc and cc != "—" else name
                    tags = [t.strip() for t in re.split(r"[,，]", _cell(row, col(COL_TAGS))) if t.strip() and t.strip() != "—"]
                    demand = [t.strip() for t in re.split(r"[,，]", _cell(row, col(COL_DEMAND))) if t.strip() and t.strip() != "—"]
                    by_key[key] = {
                        "name": name,
                        "score": to_int(_cell(row, col(COL_TOTAL))),
                        "rating": _cell(row, col(COL_RATING)) or "—",
                        "tags": tags,
                        "demand": demand,
                        "funding": _cell(row, col(COL_FUNDING)) or "—",
                        "capital": _cell(row, col(COL_CAPITAL)) or "—",
                        "address": _cell(row, col(COL_ADDRESS)) or "—",
                        "scope": _cell(row, col(COL_SCOPE)) or "—",
                        "tech": to_int(_cell(row, col(COL_TECH))),
                        "fund": to_int(_cell(row, col(COL_FUND))),
                        "intent": to_int(_cell(row, col(COL_INTENT))),
                        "team": to_int(_cell(row, col(COL_TEAM))),
                        "ind": to_int(_cell(row, col(COL_IND))),
                        "pitch": _cell(row, col(COL_PITCH)),
                        "reason": _cell(row, col(COL_REASON)),
                        "crawl": _cell(row, col(COL_CRAWL)),
                    }
                return list(by_key.values())
        except UnicodeDecodeError:
            continue
    raise UnicodeDecodeError("csv", b"", 0, 1, "tried encodings: " + ", ".join(_CSV_ENCODINGS))


# ---------- 统一解析入口 ----------

def parse_data(path):
    """根据扩展名自动分流 xlsx / csv 解析。"""
    ext = os.path.splitext(path)[1].lower()
    if ext in (".xlsx", ".xlsm"):
        return parse_xlsx(path)
    if ext == ".csv":
        return parse_csv(path)
    raise ValueError("不支持的文件格式: " + ext)


def _reset_cache():
    """清空文件缓存, 切换数据源或强制重新解析时调用。"""
    global _cache_key, _cache_payload
    _cache_key = None
    _cache_payload = None


def load_data():
    global _cache_key, _cache_payload
    try:
        st = os.stat(XLSX_PATH)
        key = (st.st_mtime_ns, st.st_size)
        if key == _cache_key and _cache_payload is not None:
            return _cache_payload
        with open(XLSX_PATH, "rb") as f:
            raw = f.read()
        fp = hashlib.md5(raw).hexdigest()[:12]
        companies = parse_data(XLSX_PATH)
        payload = {
            "fingerprint": fp,
            "mtime": time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(st.st_mtime)),
            "total": len(companies),
            "warning": "",
            "source": XLSX_PATH,
            "companies": companies,
        }
        _cache_key = key
        _cache_payload = payload
        return payload
    except FileNotFoundError:
        payload = {"fingerprint": "missing", "mtime": "", "total": 0,
                   "warning": "xlsx 文件不存在: " + XLSX_PATH, "source": XLSX_PATH,
                   "companies": []}
        _cache_key = None
        _cache_payload = payload
        return payload
    except Exception as e:  # 文件可能正在被写入(半截/被Excel占用), 返回上次成功数据
        if _cache_payload is not None:
            return {**_cache_payload, "warning": "读取失败(文件可能正在写入或被Excel占用): %s —— 页面展示上次成功数据" % e}
        payload = {"fingerprint": "err", "mtime": "", "total": 0,
                   "warning": "读取失败: %s" % e, "source": XLSX_PATH,
                   "companies": []}
        _cache_payload = payload
        return payload


class Handler(BaseHTTPRequestHandler):
    server_version = "RatingDashboard/1.0"

    def _send(self, code, ctype, body):
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        path = urlparse(self.path).path
        if path in ("/", "/index.html"):
            try:
                with open(HTML_PATH, "rb") as f:
                    self._send(200, "text/html; charset=utf-8", f.read())
            except OSError:
                self._send(500, "text/plain; charset=utf-8", b"index.html not found")
        elif path == "/api/data":
            body = json.dumps(load_data(), ensure_ascii=False).encode("utf-8")
            self._send(200, "application/json; charset=utf-8", body)
        else:
            self._send(404, "text/plain; charset=utf-8", b"not found")

    def do_POST(self):
        path = urlparse(self.path).path
        if path == "/api/source":
            length = int(self.headers.get("Content-Length", 0))
            raw = self.rfile.read(length) if length else b""
            try:
                obj = json.loads(raw.decode("utf-8"))
                p = os.path.abspath(obj.get("path", "").strip())
            except Exception:
                self._send(400, "application/json; charset=utf-8",
                           json.dumps({"ok": False, "error": "请求格式错误"},
                                      ensure_ascii=False).encode("utf-8"))
                return
            if not p or not os.path.isfile(p):
                self._send(200, "application/json; charset=utf-8",
                           json.dumps({"ok": False, "error": "文件不存在: " + p},
                                      ensure_ascii=False).encode("utf-8"))
                return
            if not p.lower().endswith((".xlsx", ".xlsm", ".csv")):
                self._send(200, "application/json; charset=utf-8",
                           json.dumps({"ok": False, "error": "仅支持 .xlsx / .xlsm / .csv 文件"},
                                      ensure_ascii=False).encode("utf-8"))
                return
            global XLSX_PATH
            XLSX_PATH = p
            _reset_cache()
            log("[切换数据源] %s" % p)
            self._send(200, "application/json; charset=utf-8",
                       json.dumps({"ok": True, "path": p},
                                  ensure_ascii=False).encode("utf-8"))
            return
        self._send(404, "text/plain; charset=utf-8", b"not found")

    def log_message(self, fmt, *args):
        pass  # 静默访问日志, 避免刷屏


def main():
    global XLSX_PATH
    port, xlsx_arg = DEFAULT_PORT, None
    for a in sys.argv[1:]:
        if a.isdigit():
            port = int(a)
        else:
            xlsx_arg = a
    if xlsx_arg:
        XLSX_PATH = os.path.abspath(xlsx_arg)
    else:
        XLSX_PATH = next((p for p in CANDIDATE_XLSX if os.path.exists(p)), CANDIDATE_XLSX[0])

    if not os.path.exists(XLSX_PATH):
        log("[错误] 找不到 xlsx 文件: %s" % XLSX_PATH)
        log("      请用: python dashboard.py [端口] [xlsx路径] 指定文件")
        sys.exit(1)

    try:
        httpd = ThreadingHTTPServer(("127.0.0.1", port), Handler)
    except OSError as e:
        log("[错误] 端口 %d 被占用: %s" % (port, e))
        log("      看板可能已在后台运行(无窗口模式), 直接打开 http://127.0.0.1:%d 即可" % port)
        log("      如需强制重启: 先运行 停止看板.bat")
        sys.exit(1)

    try:
        with open(PID_PATH, "w", encoding="ascii") as f:
            f.write(str(os.getpid()))
    except OSError:
        pass

    url = "http://127.0.0.1:%d" % port
    log("=" * 58)
    log("  武汉IT企业智能评级 · 实时可视化看板")
    log("  数据文件 : %s" % XLSX_PATH)
    log("  访问地址 : %s" % url)
    log("  说明     : 每 3 秒自动检测 xlsx 更新, 文件改动后页面自动刷新")
    log("  停止     : 运行 停止看板.bat  (或 Ctrl+C)")
    log("=" * 58)

    if not os.environ.get("NO_OPEN"):
        def _open():
            try:
                webbrowser.open(url)
            except Exception:
                pass
        threading.Timer(1.0, _open).start()

    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        log("\n已停止。")
    finally:
        try:
            if os.path.exists(PID_PATH):
                os.remove(PID_PATH)
        except OSError:
            pass


if __name__ == "__main__":
    main()
