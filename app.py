# IPCS Drawing Control System — Flask 백엔드
import os
import re
import io
import hmac
import threading
import time as _time
import traceback
from collections import Counter, defaultdict
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, date, timezone
from functools import wraps

import importlib
import httpx
import openpyxl
import xlsxwriter
from flask import Flask, render_template, request, jsonify, send_file, make_response, abort
from supabase import create_client, Client, ClientOptions

# httpx 0.28은 첫 연결 때 httpcore를 import한다. 요청 스레드 여럿이 동시에 첫 연결을 만들면
# 반쯤 초기화된 httpcore를 보고 모든 요청이 500이 될 수 있어(2026-09-28 운영) 로드 시점에 미리 import한다.
importlib.import_module("httpcore")


def _load_env():
    base_dir = os.path.dirname(os.path.abspath(__file__))
    env_path = os.path.join(base_dir, ".env")
    if not os.path.exists(env_path):
        return
    with open(env_path, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            if "=" in line:
                key, val = line.split("=", 1)
                os.environ.setdefault(key.strip(), val.strip())

_load_env()

template_dir = os.path.abspath(os.path.dirname(__file__))
# static_folder 를 프로젝트 루트로 두면 .env·app.py 등 모든 파일이 /<폴더명>/ 경로로 내려가므로 정적 서빙을 끈다(정적 파일은 쓰지 않음).
app = Flask(__name__, template_folder=template_dir, static_folder=None)
# /api/iso/... 와 예전 ISO 경로(/api/drawings 등)를 둘 다 받는다 — 기본값 경로로 리다이렉트하지 않는다.
app.url_map.redirect_defaults = False

try:
    from flask_compress import Compress
    app.config['COMPRESS_MIMETYPES'] = ['application/json', 'text/html']
    app.config['COMPRESS_MIN_SIZE'] = 500
    Compress(app)
except ImportError:
    pass

from jinja2 import ChoiceLoader, FileSystemLoader
app.jinja_loader = ChoiceLoader([
    FileSystemLoader(template_dir),
    FileSystemLoader(os.path.join(template_dir, "templates"))
])
app.config["MAX_CONTENT_LENGTH"] = 20 * 1024 * 1024

SUPABASE_URL = os.environ.get("SUPABASE_URL", "")
SUPABASE_KEY = os.environ.get("SUPABASE_KEY", "")
# 설정되어 있으면 모든 쓰기(POST) 요청에 X-Write-Password 헤더가 필요하다. 비어 있으면 보호하지 않는다.
WRITE_PASSWORD = os.environ.get("WRITE_PASSWORD", "")

SYSTEMS = ["AS", "ATM", "CCW", "CD", "DW", "FG", "FGH", "FO", "FW", "GT MISC",
           "HP", "HW", "IA", "LO", "LP", "N2", "PW", "RW", "SA", "SS", "ST MISC", "SW", "WWT"]
SUPPORT_TYPES = ["TYPICAL", "SPECIAL", "G", "GS", "U", "US", "W", "WS"]
SUPPORT_TYPE_PREFIXES = ("G", "GS", "U", "US", "W", "WS")

# 도면 종류별 설정 — 목록·Export·Print·통계·업로드·링크 동기화가 같은 테이블/칸/필터를 쓰도록 한곳에 모은다.
#   table: 전체(이력 포함) 테이블, view: 최신 Revision만 보여주는 VIEW(없으면 table)
#   key: 업로드 시 같은 도면으로 보는 칸, eq: 같은 이름의 파라미터로 일치 필터하는 칸
#   cols: (칸, Excel 머리글) — size·pid_drawing_no는 계산 칸
CATS = {
    "iso": dict(
        label="ISO Drawing", table="dwg_iso", view="dwg_latest", key=("drawing_no", "revision"),
        search=("drawing_no", "line_no", "title", "system"), eq=("system", "remark"),
        rev=True, size=True, order=("drawing_no",),
        cols=(("system", "SYSTEM"), ("size", "SIZE"), ("drawing_no", "DWG. NO."), ("line_no", "LINE NO."),
              ("bore", "BORE"), ("title", "TITLE"), ("revision", "REV."), ("issued_date", "ISSUE DATE"),
              ("remark", "REMARK"), ("file_link", "PDF LINK")),
        print_cols=("system", "drawing_no", "line_no", "title", "revision", "issued_date", "remark")),
    "support": dict(
        label="Support Drawing", table="support_master", view="support_latest", key=("support_drawing", "revision"),
        search=("support_drawing", "line_no", "iso_drawing", "system", "type"), eq=("system", "remark"),
        rev=True, size=True, order=("system", "support_drawing"),
        cols=(("system", "SYSTEM"), ("size", "SIZE"), ("support_drawing", "SUPPORT DRAWING"), ("type", "TYPE"),
              ("iso_drawing", "ISO DRAWING"), ("line_no", "LINE NO."), ("clamp_height", "CLAMP H"),
              ("l1", "L1"), ("l2", "L2"), ("l3", "L3"), ("l4", "L4"), ("revision", "REV."),
              ("issued_date", "ISSUE DATE"), ("remark", "REMARK"), ("file_link", "PDF LINK")),
        print_cols=("system", "support_drawing", "type", "iso_drawing", "line_no", "revision", "issued_date")),
    "valve": dict(
        label="Valve Drawing", table="valve_master", view=None, key=("drawing_no",),
        search=("drawing_no", "title", "valve"), eq=("valve",), rev=True, size=False, order=("id",),
        cols=(("valve", "ITEM"), ("drawing_no", "DRAWING NO."), ("title", "TITLE"), ("revision", "REV."),
              ("issued_date", "ISSUE DATE"), ("file_link", "PDF LINK")),
        print_cols=("valve", "drawing_no", "title", "revision", "issued_date")),
    "speciality": dict(
        label="Speciality Drawing", table="speciality_master", view=None, key=("drawing_no",),
        search=("drawing_no", "title", "vendor"), eq=("title",), rev=True, size=False, order=("drawing_no",),
        cols=(("drawing_no", "DRAWING NO."), ("title", "TITLE"), ("vendor", "VENDOR"), ("class", "CLASS"),
              ("connection", "CONNECTION"), ("revision", "REV."), ("issued_date", "ISSUE DATE"),
              ("file_link", "PDF LINK")),
        print_cols=("drawing_no", "title", "vendor", "class", "connection", "revision", "issued_date")),
    "pid": dict(
        label="P&ID Drawing", table="pid_master", view=None, key=("drawing_no",),
        search=("drawing_no", "title", "system"), eq=("system",), rev=True, size=False, order=("system", "drawing_no"),
        cols=(("system", "SYSTEM"), ("drawing_no", "DRAWING NO."), ("title", "TITLE"), ("revision", "REV."),
              ("issued_date", "ISSUE DATE"), ("file_link", "PDF LINK")),
        print_cols=("system", "drawing_no", "title", "revision", "issued_date")),
    "markedpid": dict(
        label="Marked PID", table="marked_pid_master", view=None, key=("drawing_no",),
        search=("drawing_no", "title", "system"), eq=("system",), rev=False, size=False, order=("id",),
        cols=(("system", "SYSTEM"), ("pid_drawing_no", "PID DRAWING NO."), ("drawing_no", "MARKED PID"),
              ("title", "DESCRIPTION"), ("issued_date", "ISSUE DATE"), ("file_link", "PDF LINK")),
        print_cols=("system", "pid_drawing_no", "drawing_no", "title", "issued_date")),
}
COMPUTED_COLS = {"size", "pid_drawing_no"}
PAGE = 1000  # Drawing DB 프로젝트는 PostgREST 1회 최대 1,000행


def _cat(cat):
    # /api/drawings/... 는 예전 ISO 경로라 iso로 본다.
    cat = "iso" if cat == "drawings" else cat
    if cat not in CATS:
        abort(404)
    return cat


# ── Supabase ─────────────────────────────────────────────────

_supabase_client: Client = None
_client_lock = threading.Lock()

def _use_http1(client: Client):
    # 하나의 HTTP/2 연결을 여러 스레드가 공유하면 간헐적으로 ReadError(WinError 10035)가 나므로
    # PostgREST 세션을 스레드마다 별도 연결을 쓰는 HTTP/1.1 세션으로 교체한다.
    try:
        old = client.postgrest.session
        client.postgrest.session = httpx.Client(base_url=old.base_url, headers=old.headers, timeout=old.timeout,
                                                follow_redirects=old.follow_redirects, http2=False)
        old.close()
    except AttributeError:
        pass  # 라이브러리 내부 구조가 다르면 기본 세션 유지

def get_client() -> Client:
    global _supabase_client
    if _supabase_client is not None:
        return _supabase_client
    if not SUPABASE_URL or not SUPABASE_KEY:
        raise ValueError("SUPABASE_URL and SUPABASE_KEY are not set.")
    with _client_lock:   # 여러 스레드가 동시에 첫 클라이언트를 만들지 않도록
        if _supabase_client is None:
            client = create_client(SUPABASE_URL, SUPABASE_KEY, options=ClientOptions(schema="drawing"))
            _use_http1(client)
            _supabase_client = client
    return _supabase_client

def _fetch_all_paginated(supabase, table, columns, page_size=PAGE, not_null=None, eq=None):
    # 1회 최대 행 수 제한 때문에 나눠 읽는다. 첫 페이지에서 전체 건수를 받고 나머지는 병렬로 조회한다.
    def page(offset, count=None):
        q = supabase.table(table).select(columns, count=count)
        if not_null: q = q.not_.is_(not_null, "null")
        if eq:       q = q.eq(*eq)
        return q.order("id").range(offset, offset + page_size - 1).execute()

    first = page(0, "exact")
    rows = list(first.data)
    offsets = range(page_size, first.count or 0, page_size)
    if offsets:
        with ThreadPoolExecutor(max_workers=4) as ex:
            for res in ex.map(page, offsets):
                rows.extend(res.data)
    return rows

def _upsert_partial(table, rows, chunk=500):
    # postgrest는 한 묶음의 열 목록을 키 합집합으로 보내 빠진 칸을 NULL로 덮어쓴다.
    # 칸 구성이 같은 행끼리 묶어 보내야 엑셀에서 비어 있던 칸의 기존 값이 지켜진다.
    groups = defaultdict(list)
    for r in rows:
        groups[tuple(sorted(r))].append(r)
    sb = get_client()
    for group in groups.values():
        for i in range(0, len(group), chunk):
            sb.table(table).upsert(group[i:i + chunk], on_conflict="id").execute()

_audit_cols: dict = {}

def _audit(table, rows, via):
    # updated_at/updated_by 칸이 있는 테이블에만 수정 이력을 붙인다(칸 추가 SQL은 context-notes.md 참고).
    if table not in _audit_cols:
        try:
            get_client().table(table).select("updated_at,updated_by").limit(1).execute()
            _audit_cols[table] = True
        except Exception:
            _audit_cols[table] = False
    if _audit_cols[table]:
        now = datetime.now(timezone.utc).isoformat(timespec="seconds")
        for r in rows:
            r["updated_at"] = now
            r["updated_by"] = via
    return rows


# ── 캐시 ─────────────────────────────────────────────────────

# 조회 응답 캐시 — 도면 데이터는 업로드/링크 동기화 시에만 바뀌므로
# 접속할 때마다 Supabase를 다시 조회하지 않고, 쓰기 작업 시에만 무효화한다.
_resp_cache: dict = {}
_resp_cache_lock = threading.Lock()
RESP_CACHE_TTL = 1800  # 30분 — 무효화를 놓쳤을 때를 대비한 안전망
RESP_CACHE_MAX = 500   # 검색어마다 키가 생겨 무한히 늘지 않도록 상한을 두고, 넘으면 비운다

_distinct_cache: dict = {}   # (table, col, system) → (ts, values)
_calc_cache: dict = {}       # 통계 계산 결과 → (ts, value)
DISTINCT_CACHE_TTL = 1800
CALC_CACHE_TTL = 300

def _invalidate_response_cache(filters=True):
    # 링크 동기화는 필터 목록(Size/Remark/Revision)을 바꾸지 않으므로 filters=False로 부른다.
    with _resp_cache_lock:
        _resp_cache.clear()
    _calc_cache.clear()
    if filters:
        _distinct_cache.clear()

def cached_get(fn):
    @wraps(fn)
    def wrapper(*args, **kwargs):
        key = request.full_path
        now = _time.time()
        with _resp_cache_lock:
            cached = _resp_cache.get(key)
            if cached and (now - cached[0]) < RESP_CACHE_TTL:
                return jsonify(cached[1])
        result = fn(*args, **kwargs)
        body, status = (result[0], result[1]) if isinstance(result, tuple) else (result, 200)
        if status == 200:
            try:
                with _resp_cache_lock:
                    if len(_resp_cache) >= RESP_CACHE_MAX:
                        _resp_cache.clear()
                    _resp_cache[key] = (now, body.get_json())
            except Exception:
                pass
        return result
    return wrapper

def _memo(key, fn, ttl=CALC_CACHE_TTL):
    hit = _calc_cache.get(key)
    if hit and (_time.time() - hit[0]) < ttl:
        return hit[1]
    value = fn()
    _calc_cache[key] = (_time.time(), value)
    return value


# ── 공통 도우미 ───────────────────────────────────────────────

_SIZE_RE = re.compile(r'^(\d+(?:\s+\d+/\d+)?(?:/\d+)?)\s*"')

def _size_numeric(raw):
    s = raw.rstrip('"')
    try:
        if ' ' in s:
            whole, frac = s.split()
            n, d = frac.split('/')
            return int(whole) + int(n) / int(d)
        elif '/' in s:
            n, d = s.split('/')
            return int(n) / int(d)
        return float(s)
    except (ValueError, ZeroDivisionError):
        return float('inf')

def _line_size_raw(line_no):
    if not line_no:
        return None
    m = _SIZE_RE.match(str(line_no).strip())
    if not m:
        return None
    return m.group(1).strip() + '"'

def _get_distinct(table, col, system=None):
    key = (table, col, system or "")
    hit = _distinct_cache.get(key)
    if hit and (_time.time() - hit[0]) < DISTINCT_CACHE_TTL:
        return hit[1]
    src = "line_no" if col == "size" else col
    rows = _fetch_all_paginated(get_client(), table, src, not_null=src, eq=("system", system) if system else None)
    if col == "size":
        values = sorted({s for s in (_line_size_raw(r.get("line_no")) for r in rows) if s}, key=_size_numeric)
    else:
        values = sorted({str(r[src]) for r in rows if r.get(src)})
    _distinct_cache[key] = (_time.time(), values)
    return values

def _safe_distinct(table, col, system=None):
    try:
        return _get_distinct(table, col, system)
    except Exception:
        return []

def _safe_int(val, default, min_val=1):
    try:
        return max(min_val, int(val))
    except (TypeError, ValueError):
        return default

def _q(value):
    # or_() 안의 값은 쉼표·괄호·따옴표가 구문을 깨므로 큰따옴표로 감싸고 \ 와 " 를 이스케이프한다.
    return '"' + value.replace('\\', '\\\\').replace('"', '\\"') + '"'

def get_cloudinary_url(file_key):
    if not file_key:
        return None
    file_key = str(file_key).strip()
    if file_key.startswith("http") and "cloudinary.com" in file_key and not any(
        file_key.lower().endswith(ext) for ext in [".pdf", ".jpg", ".jpeg", ".png"]
    ):
        return file_key + ".pdf"
    return file_key

def _sanitize_link(d: dict, key: str = "file_link"):
    fk = d.get(key, "")
    if fk and "res.cloudinary.com" not in fk:
        d[key] = None

def _rev_arg(args):
    # ISO 화면은 예전부터 revision을 status 파라미터로 보낸다.
    return (args.get("revision") or args.get("status") or "").strip()

def _target(cat, args):
    # 최신 VIEW가 있는 종류는 Revision을 고르면 이력 전체에서 찾는다(구 Revision 조회).
    cfg = CATS[cat]
    if cfg["view"] and not _rev_arg(args):
        return cfg["view"]
    return cfg["table"]

def _apply_filters(cat, q, args):
    # 목록·Export·Print가 같은 조건으로 조회되도록 필터를 한곳에서 적용한다.
    cfg = CATS[cat]
    search = (args.get("search") or "").strip()
    if search:
        pat = _q(f"%{search}%")
        q = q.or_(",".join(f"{c}.ilike.{pat}" for c in cfg["search"]))
    for col in cfg["eq"]:
        v = (args.get(col) or "").strip()
        if v:
            q = q.eq(col, v)
    rev = _rev_arg(args)
    if rev and cfg["rev"]:
        q = q.eq("revision", rev)
    size = (args.get("size") or "").strip()
    if size and cfg["size"]:
        q = q.ilike("line_no", f"{size}%")
    t = (args.get("type") or "").strip()
    if t and cat == "support":
        if t in SUPPORT_TYPE_PREFIXES:
            # 대부분 "(GS-12)" 형식이지만 여는 괄호가 빠진 "GS-12)"도 있어 둘 다 찾는다.
            q = q.or_(f"type.ilike.{_q('(' + t + '-%')},type.ilike.{_q(t + '-%')}")
        else:
            q = q.eq("type", t)
    return q

def _pid_by_system():
    rows = get_client().table("pid_master").select("system,drawing_no").execute().data
    return {p["system"]: p["drawing_no"] for p in rows if p.get("system")}

def _decorate(cat, rows):
    if cat in ("iso", "support"):
        for r in rows:
            r["size"] = _line_size_raw(r.get("line_no")) or ''
    if cat == "iso":
        for r in rows:
            if r.get("file_link"):
                r["file_link"] = get_cloudinary_url(r["file_link"])
    else:
        for r in rows:
            _sanitize_link(r)
    if cat == "markedpid":
        pids = _pid_by_system()
        for r in rows:
            r["pid_drawing_no"] = pids.get(r.get("system"))
    return rows

def _select_cols(cat):
    cols = [c for c, _ in CATS[cat]["cols"] if c not in COMPUTED_COLS]
    for extra in ("id", "line_no" if CATS[cat]["size"] else None, "system" if cat == "markedpid" else None):
        if extra and extra not in cols:
            cols.append(extra)
    return ",".join(cols)

def _fetch_filtered(cat, args):
    # 필터 결과 전체를 페이지로 나눠 읽는다(Export/Print용). id를 마지막 정렬 기준으로 넣어 페이지 경계 중복·누락을 막는다.
    sb = get_client()
    table = _target(cat, args)
    total = _apply_filters(cat, sb.table(table).select("id", count="exact"), args).limit(1).execute().count or 0
    cols = _select_cols(cat)

    def batch(offset):
        q = _apply_filters(cat, sb.table(table).select(cols), args)
        for o in CATS[cat]["order"] + ("id",):
            q = q.order(o)
        return q.range(offset, offset + PAGE - 1).execute().data

    with ThreadPoolExecutor(max_workers=4) as ex:
        rows = [r for part in ex.map(batch, range(0, total, PAGE)) for r in part]
    return _decorate(cat, rows)

def _xlsx(sheets, filename):
    # sheets: [(시트명, 머리글 목록, 행 목록)] → 다운로드 응답
    output = io.BytesIO()
    wb = xlsxwriter.Workbook(output, {"in_memory": True, "strings_to_formulas": False})
    head = wb.add_format({"bold": True, "bg_color": "#F1F5F9", "border": 1})
    for name, headers, rows in sheets:
        ws = wb.add_worksheet(name[:31])
        ws.write_row(0, 0, headers, head)
        widths = [len(h) + 2 for h in headers]
        for i, row in enumerate(rows, 1):
            vals = ["" if v is None else v for v in row]
            ws.write_row(i, 0, vals)
            for j, v in enumerate(vals):
                widths[j] = min(60, max(widths[j], len(str(v)) + 2))
        for j, w in enumerate(widths):
            ws.set_column(j, j, w)
        ws.freeze_panes(1, 0)
        if rows:
            ws.autofilter(0, 0, len(rows), len(headers) - 1)
    wb.close()
    output.seek(0)
    return send_file(output, as_attachment=True, download_name=filename,
                     mimetype="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")

def _stamp():
    return datetime.now().strftime('%Y%m%d_%H%M')

def _esc(v):
    return str(v or "").replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


# ── 쓰기 보호 ────────────────────────────────────────────────

@app.before_request
def _guard_writes():
    if request.method == "POST" and WRITE_PASSWORD:
        given = request.headers.get("X-Write-Password", "")
        if not hmac.compare_digest(given.encode(), WRITE_PASSWORD.encode()):
            return jsonify({"error": "Write password required.", "auth": True}), 401


@app.route("/api/cache/clear", methods=["POST"])
def api_cache_clear():
    # 보조 스크립트로 DB를 직접 바꾼 뒤 호출하면 30분 캐시를 기다리지 않고 바로 반영된다.
    _invalidate_response_cache()
    return jsonify({"success": True})


# ── 화면·조회 ─────────────────────────────────────────────────

@app.route("/")
def index():
    resp = make_response(render_template("index.html"))
    resp.headers['Cache-Control'] = 'no-cache, no-store, must-revalidate'
    resp.headers['Pragma'] = 'no-cache'
    resp.headers['Expires'] = '0'
    return resp


def _list(cat, args):
    page     = _safe_int(args.get("page", 1), 1)
    per_page = _safe_int(args.get("per_page", 20), 20)
    offset   = (page - 1) * per_page
    q = _apply_filters(cat, get_client().table(_target(cat, args)).select("*", count="exact"), args)
    for o in CATS[cat]["order"] + ("id",):
        q = q.order(o)
    res = q.range(offset, offset + per_page - 1).execute()
    return {"data": _decorate(cat, res.data), "total": res.count, "page": page}


@app.route("/api/drawings", defaults={"cat": "iso"})
@app.route("/api/<cat>/drawings")
@cached_get
def api_drawings(cat):
    cat = _cat(cat)
    try:
        return jsonify(_list(cat, request.args))
    except Exception as e:
        return jsonify({"error": str(e)}), 500


def _cat_stats(cat):
    # 헤더 통계 — 목록과 같은 기준(최신 Revision)으로 Revision 분포와 PDF 연결 수를 센다. VOID는 PDF 대상에서 뺀다.
    def calc():
        cfg = CATS[cat]
        cols = "id,file_link" + (",revision" if cfg["rev"] else "")
        rows = _fetch_all_paginated(get_client(), cfg["view"] or cfg["table"], cols)
        by_rev = Counter(r.get("revision") or "—" for r in rows) if cfg["rev"] else Counter()
        targets = [r for r in rows if r.get("revision") != "VOID"]
        return {"total": len(rows),
                "by_rev": sorted(by_rev.items()),
                "pdf_target": len(targets),
                "linked": sum(1 for r in targets if r.get("file_link"))}
    return _memo(("stats", cat), calc)


@app.route("/api/stats", defaults={"cat": "iso"})
@app.route("/api/<cat>/stats")
def api_stats(cat):
    cat = _cat(cat)
    try:
        return jsonify(_cat_stats(cat))
    except Exception as e:
        return jsonify({"error": str(e)}), 500


def _iso_filters(system=""):
    # Revision 목록은 DB 실제 값에서 뽑는다(새 Revision이 생겨도 필터에 바로 나타나도록).
    return {"systems": SYSTEMS, "statuses": _safe_distinct("dwg_iso", "revision"),
            "remarks": _safe_distinct("dwg_iso", "remark"),
            "sizes": _safe_distinct("dwg_iso", "size", system or None)}


@app.route("/api/filters")
@cached_get
def api_filters():
    return jsonify(_iso_filters(request.args.get("system", "")))


@app.route("/api/init")
@cached_get
def api_init():
    try:
        with ThreadPoolExecutor(max_workers=3) as ex:
            f_list  = ex.submit(_list, "iso", {})
            f_stats = ex.submit(_cat_stats, "iso")
            f_filt  = ex.submit(_iso_filters)
            return jsonify({"filters": f_filt.result(), "stats": f_stats.result(), "drawings": f_list.result()})
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/api/support/filters")
@cached_get
def api_support_filters():
    system = request.args.get("system", "")
    return jsonify({
        "systems":   SYSTEMS,
        "types":     SUPPORT_TYPES,
        "revisions": _safe_distinct("support_master", "revision"),
        "remarks":   _safe_distinct("support_master", "remark"),
        "sizes":     _safe_distinct("support_master", "size", system or None),
    })


@app.route("/api/pid/filters")
@cached_get
def api_pid_filters():
    return jsonify({"systems": _safe_distinct("pid_master", "system"),
                    "revisions": _safe_distinct("pid_master", "revision")})


@app.route("/api/valve/filters")
@cached_get
def api_valve_filters():
    return jsonify({"valves": _safe_distinct("valve_master", "valve"),
                    "revisions": _safe_distinct("valve_master", "revision")})


@app.route("/api/speciality/filters")
@cached_get
def api_speciality_filters():
    return jsonify({"revisions": _safe_distinct("speciality_master", "revision"),
                    "titles": _safe_distinct("speciality_master", "title")})


@app.route("/api/markedpid/filters")
@cached_get
def api_markedpid_filters():
    return jsonify({"systems": _safe_distinct("marked_pid_master", "system")})


@app.route("/api/<cat>/history")
def api_history(cat):
    # 같은 도면의 모든 Revision(구 Revision 포함)과 각 PDF
    cat = _cat(cat)
    cfg = CATS[cat]
    no = request.args.get("no", "").strip()
    if not cfg["view"] or not no:
        return jsonify({"data": []})
    try:
        rows = get_client().table(cfg["table"]).select("*").eq(cfg["key"][0], no).order("revision").execute().data
        return jsonify({"data": _decorate(cat, rows)})
    except Exception as e:
        return jsonify({"error": str(e)}), 500


# ── Export / Print ────────────────────────────────────────────

@app.route("/api/export", defaults={"cat": "iso"})
@app.route("/api/<cat>/export")
def api_export(cat):
    cat = _cat(cat)
    try:
        rows = _fetch_filtered(cat, request.args)
        if not rows:
            return jsonify({"error": "No data to export"}), 404
        cols = CATS[cat]["cols"]
        name = CATS[cat]["label"].replace("&", "").replace(" ", "_")
        return _xlsx([(CATS[cat]["label"], [h for _, h in cols], [[r.get(c) for c, _ in cols] for r in rows])],
                     f"{name}_Master_{_stamp()}.xlsx")
    except Exception as e:
        return jsonify({"error": f"Export failed: {e}"}), 500


@app.route("/api/print", defaults={"cat": "iso"})
@app.route("/api/<cat>/print")
def api_print(cat):
    cat = _cat(cat)
    try:
        rows = _fetch_filtered(cat, request.args)
        heads = dict(CATS[cat]["cols"])
        pcols = CATS[cat]["print_cols"]
        key_col = CATS[cat]["key"][0]
        thead = "".join(f"<th>{_esc(heads[c])}</th>" for c in pcols)
        body = []
        for i, d in enumerate(rows, 1):
            cells = "".join(
                f"<td class='{'col-dwg' if c == key_col else ''}'>{_esc(d.get(c))}</td>" for c in pcols)
            body.append(f"<tr><td>{i}</td>{cells}</tr>")
        title = f"IPCS {CATS[cat]['label']} Master List"
        return f"""<!DOCTYPE html>
<html><head>
<meta charset="utf-8">
<title>{_esc(title)}</title>
<style>
@page {{ size: landscape; margin: 8mm; }}
* {{ -webkit-print-color-adjust: exact; }}
body {{ font-family: 'Inter', sans-serif; margin: 15px 0; background: #f8fafc; font-size: 8px; }}
#print-main {{ background: #fff; padding: 20px; width: 96%; margin: 0 auto; }}
h2 {{ text-align: center; margin-bottom: 10px; font-size: 15px; font-weight: 600; color: #1e293b; }}
.meta {{ text-align: right; margin-bottom: 5px; font-size: 7px; color: #64748b; }}
table {{ width: 100%; border-collapse: collapse; border: 0.5px solid #94a3b8; }}
th, td {{ border: 0.4px solid #cbd5e1; padding: 4px 6px; text-align: center; }}
th {{ background-color: #f1f5f9; font-weight: 600; text-transform: uppercase; }}
.col-dwg {{ color: #2563eb; font-weight: 500; white-space: nowrap; }}
#top-ctrl {{ width: 96%; margin: 10px auto; display: flex; justify-content: flex-end;
             align-items: center; gap: 15px; }}
#print-btn {{ background: #2563eb; color: #fff; border: none; padding: 6px 15px;
              border-radius: 4px; font-size: 11px; cursor: pointer; }}
@media print {{ body {{ background: #fff; margin: 0; }}
                #print-main {{ width: 100%; padding: 0; }}
                #top-ctrl {{ display: none; }} }}
</style></head>
<body>
<div id="top-ctrl">
  <div style="font-size:9px;color:#dc2626;font-weight:500;">
    Preparing {len(rows)} filtered records — the print dialog opens automatically.
  </div>
  <button id="print-btn" onclick="window.print()">Print Now</button>
</div>
<div id="print-main">
  <h2>{_esc(title)} ({len(rows)} Records)</h2>
  <div class="meta">Generated: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}</div>
  <table>
    <thead><tr><th style="width:35px">NO.</th>{thead}</tr></thead>
    <tbody>{''.join(body)}</tbody>
  </table>
</div>
<script>
  window.onload = function() {{
    const wait = Math.max(3500, Math.min(6000, {len(rows)} * 1.5));
    setTimeout(function() {{
      window.print();
      window.onafterprint = function() {{ window.close(); }};
    }}, wait);
  }};
</script>
</body></html>"""
    except Exception as e:
        return f"Print failed: {_esc(e)}", 500


# ── Excel 업로드 ──────────────────────────────────────────────

def _read_excel(file, header_row=0, lower=False):
    # 첫 시트를 {머리글: 값} 목록으로 읽는다. header_row는 머리글이 있는 행 번호(0부터).
    wb = openpyxl.load_workbook(io.BytesIO(file.read()), read_only=True, data_only=True)
    try:
        it = wb.worksheets[0].iter_rows(values_only=True)
        for _ in range(header_row):
            next(it, None)
        headers = [str(h).strip() if h is not None else "" for h in next(it, ())]
        if lower:
            headers = [h.lower().replace("\n", " ") for h in headers]
        return [{h: v for h, v in zip(headers, vals) if h}
                for vals in it if any(v not in (None, "") for v in vals)]
    finally:
        wb.close()

def _cell(v):
    if v is None:
        return ""
    if hasattr(v, "strftime"):
        return v.strftime("%Y-%m-%d")
    if isinstance(v, float) and v.is_integer():
        return str(int(v))
    return str(v).strip()

def _pick(r, *names):
    for n in names:
        v = _cell(r.get(n))
        if v:
            return v
    return ""

def _date(v):
    s = _cell(v)
    return s.split(" ")[0][:10] if s else ""

def _row(**fields):
    # 값이 있는 칸만 남긴다 — 빈 칸은 보내지 않아 기존 값을 지킨다.
    return {k: v for k, v in fields.items() if v}

def _parse_upload(cat, file):
    if cat == "iso":
        return [_row(drawing_no=_pick(r, "drawing_no", "drawing_n"), line_no=_pick(r, "line_no"),
                     system=_pick(r, "system"), bore=_pick(r, "bore"), title=_pick(r, "title"),
                     revision=_pick(r, "revision"), issued_date=_date(r.get("issued_date")),
                     file_link=get_cloudinary_url(_pick(r, "file_link")))
                for r in _read_excel(file, 0, lower=True)]
    if cat == "support":
        # 신 포맷(support tag no.)과 구 포맷(support drawing) 모두 지원
        return [_row(system=_pick(r, "system"), support_drawing=_pick(r, "support tag no.", "support drawing"),
                     type=_pick(r, "type"), iso_drawing=_pick(r, "iso drawing no.", "iso drawing"),
                     line_no=_pick(r, "line no.", "line no"),
                     clamp_height=_pick(r, "shoe  height", "clamp height", "clamp h"),
                     l1=_pick(r, "l1"), l2=_pick(r, "l2"), l3=_pick(r, "l3"), l4=_pick(r, "l4"),
                     revision=_pick(r, "latest", "revision", "rev"), issued_date=_date(r.get("issue date")))
                for r in _read_excel(file, 0, lower=True)]
    if cat == "valve":
        return [_row(drawing_no=_pick(r, "Drawing No", "Drawing No."), valve=_pick(r, "Valve", "Item"),
                     title=_pick(r, "Title"), revision=_pick(r, "Rev.", "Revision"), issued_date=_date(r.get("Date")))
                for r in _read_excel(file, 1)]
    if cat == "speciality":
        return [_row(drawing_no=_pick(r, "Drawing No", "Drawing No."), title=_pick(r, "Title"),
                     vendor=_pick(r, "Vendor"), **{"class": _pick(r, "Class")}, connection=_pick(r, "Connection"),
                     revision=_pick(r, "Rev.", "Revision"), issued_date=_date(r.get("Date")))
                for r in _read_excel(file, 1)]
    if cat == "pid":
        return [_row(drawing_no=_pick(r, "Drawing No"), system=_pick(r, "System"), title=_pick(r, "Title"),
                     revision=_pick(r, "Rev."), issued_date=_date(r.get("Date")))
                for r in _read_excel(file, 1)]
    return [_row(drawing_no=_pick(r, "MARKED PID"), system=_pick(r, "SYSTEM"), title=_pick(r, "DESCRIPTION"),
                 issued_date=_date(r.get("DATE")))
            for r in _read_excel(file, 0)]


def _apply_upload(cat, rows, dry_run):
    # 기존 행과 비교해 신규 / 개정(같은 도면의 새 Revision) / 변경 / 변경없음으로 나누고,
    # 신규·변경 행만 upsert한다. dry_run이면 분류만 돌려준다(업로드 미리보기).
    cfg = CATS[cat]
    key = cfg["key"]
    valid = [r for r in rows if all(r.get(c) for c in key)]
    invalid = len(rows) - len(valid)
    deduped = {}
    for r in valid:
        deduped[tuple(r[c] for c in key)] = r   # 같은 파일 안 중복 키는 마지막 값만 남긴다
    rows = list(deduped.values())

    fields = sorted({c for r in rows for c in r} | set(key) | {"id"})
    existing_rows = _fetch_all_paginated(get_client(), cfg["table"], ",".join(fields))
    existing = {tuple(e.get(c) or "" for c in key): e for e in existing_rows}
    known_nos = {e.get(key[0]) for e in existing_rows}
    max_id = max((e["id"] for e in existing_rows), default=0)
    today = date.today().isoformat()

    new, revised, changed, writes, unchanged = [], [], [], [], 0
    for r in rows:
        e = existing.get(tuple(r[c] for c in key))
        if e is None:
            max_id += 1
            r["id"] = max_id
            r.setdefault("issued_date", today)   # 신규 도면 발행일이 비어 있으면 등록일
            (revised if len(key) > 1 and r[key[0]] in known_nos else new).append(r)
            writes.append(r)
            continue
        diff = {c: [e.get(c), v] for c, v in r.items() if c not in key and str(e.get(c) or "").strip() != v}
        if diff:
            r["id"] = e["id"]
            changed.append((r, diff))
            writes.append(r)
        else:
            unchanged += 1

    if writes and not dry_run:
        _upsert_partial(cfg["table"], _audit(cfg["table"], writes, "web upload"))
        _invalidate_response_cache()

    label = lambda r: " / ".join(str(r.get(c)) for c in key)
    return {"success": True, "dry_run": dry_run, "processed": len(rows) + invalid, "invalid": invalid,
            "new": len(new), "revised": len(revised), "changed": len(changed), "skipped": unchanged,
            "inserted": 0 if dry_run else len(writes),
            "samples": ([{"kind": "New", "no": label(r)} for r in new[:10]] +
                        [{"kind": "Revised", "no": label(r)} for r in revised[:10]] +
                        [{"kind": "Changed", "no": label(r),
                          "diff": {c: [str(a or ""), b] for c, (a, b) in d.items()}} for r, d in changed[:10]])}


@app.route("/api/upload", methods=["POST"], defaults={"cat": "iso"})
@app.route("/api/<cat>/upload", methods=["POST"])
def api_upload(cat):
    cat = _cat(cat)
    file = request.files.get("file")
    if not file:
        return jsonify({"error": "No file selected."}), 400
    try:
        rows = _parse_upload(cat, file)
        return jsonify(_apply_upload(cat, rows, dry_run=request.args.get("dry_run") == "1"))
    except Exception as e:
        return jsonify({"error": str(e)}), 500


# ── Cloudinary 링크 동기화 ────────────────────────────────────

def _configure_cloudinary():
    import cloudinary
    cld_url = os.environ.get("CLOUDINARY_URL", "")
    if not cld_url:
        raise ValueError("CLOUDINARY_URL is not set.")
    m = re.match(r"cloudinary://([^:]+):([^@]+)@(.+)", cld_url)
    if not m:
        raise ValueError("CLOUDINARY_URL format is invalid.")
    cloudinary.config(api_key=m.group(1), api_secret=m.group(2), cloud_name=m.group(3))

def _fetch_cloudinary_all(resource_type="image"):
    import cloudinary.api
    uploaded = {}
    next_cursor = None
    while True:
        kwargs = {"type": "upload", "max_results": 500, "resource_type": resource_type}
        if next_cursor:
            kwargs["next_cursor"] = next_cursor
        res = cloudinary.api.resources(**kwargs)
        for item in res.get("resources", []):
            basename = item["public_id"].split("/")[-1]
            uploaded[basename] = item.get("secure_url", "")
        next_cursor = res.get("next_cursor")
        if not next_cursor:
            break
    return uploaded

def _link_candidates(cat, row, is_latest):
    # 도면 행에 맞는 Cloudinary 파일명 후보(우선순위 순)
    if cat not in ("iso", "support"):
        return [str(row["drawing_no"]).strip()]
    if cat == "iso":
        safe = str(row["drawing_no"]).strip()
    else:
        safe = str(row["support_drawing"]).replace('"', '').replace('/', '_')
        if str(row.get("type") or "").strip().upper() != "SPECIAL":
            safe = re.sub(r'\s*\([^)]+\)\s*$', '', safe).strip()
    rev = str(row["revision"]).upper()
    names = [f"{safe}_{rev}", f"{safe}-{rev}"]
    # Revision 없는 파일명은 최신 Revision에만 연결한다(구 Revision·VOID에 옛 PDF가 붙지 않도록).
    if is_latest and rev != "VOID":
        names.append(safe)
    return names

def _sync_links(cat, dry_run=False):
    # Cloudinary에 실제 있는 파일로 file_link를 맞춘다. 전체를 비우고 다시 채우지 않고
    # 값이 바뀌는 행만 보내므로 중간에 실패해도 기존 링크가 사라지지 않는다.
    cfg = CATS[cat]
    key = cfg["key"]
    _configure_cloudinary()
    cols = ["id", "file_link"] + list(key) + (["type"] if cat == "support" else [])
    rows = _fetch_all_paginated(get_client(), cfg["table"], ",".join(cols))
    uploaded = _fetch_cloudinary_all()
    if not uploaded:
        raise RuntimeError("Cloudinary returned no files — links were left unchanged.")
    lookups = [uploaded,
               {k.replace('--', '-'): v for k, v in uploaded.items()},
               {k.lower(): v for k, v in uploaded.items()}]

    latest = {}
    if len(key) > 1:
        for r in rows:
            if r.get(key[0]) and r.get("revision") and (r["revision"] > latest.get(r[key[0]], "")):
                latest[r[key[0]]] = r["revision"]

    updates, added, updated, removed = [], 0, 0, 0
    for r in rows:
        if not all(r.get(c) for c in key):
            continue
        is_latest = len(key) == 1 or latest.get(r[key[0]]) == r["revision"]
        url = None
        for name in _link_candidates(cat, r, is_latest):
            for n in (name, f"{name}.pdf"):
                for i, lk in enumerate(lookups):
                    url = lk.get(n.lower() if i == 2 else n)
                    if url:
                        break
                if url:
                    break
            if url:
                break
        if url and not url.lower().endswith(".pdf"):
            url += ".pdf"
        if url == r.get("file_link"):
            continue
        if not r.get("file_link"):
            added += 1
        elif not url:
            removed += 1
        else:
            updated += 1
        updates.append({"id": r["id"], **{c: r[c] for c in key}, "file_link": url})

    if updates and not dry_run:
        _upsert_partial(cfg["table"], _audit(cfg["table"], updates, "web sync-links"))
        _invalidate_response_cache(filters=False)
    linked = sum(1 for r in rows if r.get("file_link")) + added - removed
    return {"success": True, "dry_run": dry_run, "linked": linked, "added": added, "updated": updated,
            "removed": removed,
            "message": f"{linked:,} linked · {added:,} added · {updated:,} updated · {removed:,} removed"}


@app.route("/api/<cat>/sync-links", methods=["POST"])
def api_sync_links(cat):
    cat = _cat(cat)
    try:
        return jsonify(_sync_links(cat, dry_run=request.args.get("dry_run") == "1"))
    except Exception as e:
        traceback.print_exc()
        return jsonify({"error": str(e)}), 500


# 모듈 로드 중에는 스레드를 띄우거나 DB를 조회하지 않는다. 시작 시 선계산 스레드를 두었다가
# httpcore 동시 import(500)와 gunicorn 기동 멈춤이 운영에서 났다(2026-09-28). 캐시는 첫 요청 때 채운다.

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5100))
    app.run(host="0.0.0.0", port=port, threaded=True)
