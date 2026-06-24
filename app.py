# IPCS Drawing Control System — Flask 백엔드
import os
import re
import io
import time as _time
import pandas as pd
from datetime import datetime
from flask import Flask, render_template, request, jsonify, send_file, make_response
from supabase import create_client, Client, ClientOptions


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
app = Flask(__name__, template_folder=template_dir, static_folder=template_dir)

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
TABLE_ALL        = "dwg_iso"
TABLE_LATEST     = "dwg_latest"
TABLE_SUPPORT    = "support_master"
TABLE_VALVE      = "valve_master"
TABLE_SPECIALITY = "speciality_master"
TABLE_PID        = "pid_master"

SYSTEMS   = ["AS", "ATM", "CCW", "CD", "DW", "FG", "FGH", "FO", "FW", "GT MISC",
             "HP", "HW", "IA", "LO", "LP", "N2", "PW", "RW", "SA", "SS", "ST MISC", "SW", "WWT"]
REVISIONS = ["C01", "C01A", "C01B"]

_SIZE_RE = re.compile(r'^(\d+(?:\s+\d+/\d+)?(?:/\d+)?)\s*"')
SMALL_PREFIXES = ['1/2"', '3/4"', '1 1/4"', '1 1/2"', '1"', '2"']

def _line_size(line_no):
    if not line_no:
        return ''
    m = _SIZE_RE.match(str(line_no).strip())
    if not m:
        return ''
    raw = m.group(1).strip()
    try:
        if ' ' in raw:
            parts = raw.split()
            whole = int(parts[0])
            n, d = parts[1].split('/')
            val = whole + int(n) / int(d)
        elif '/' in raw:
            n, d = raw.split('/')
            val = int(n) / int(d)
        else:
            val = float(raw)
        return 'Small' if val <= 2 else 'Large'
    except (ValueError, ZeroDivisionError):
        return ''

def _apply_size_filter(query, size):
    if size == 'Small':
        patterns = ','.join(f'line_no.ilike.{p}%' for p in SMALL_PREFIXES)
        return query.or_(patterns)
    elif size == 'Large':
        for p in SMALL_PREFIXES:
            query = query.filter("line_no", "not.ilike", f'{p}%')
        return query
    return query

_supabase_client: Client = None

def get_client() -> Client:
    global _supabase_client
    if _supabase_client is not None:
        return _supabase_client
    if not SUPABASE_URL or not SUPABASE_KEY:
        raise ValueError("SUPABASE_URL, SUPABASE_KEY를 확인하세요.")
    _supabase_client = create_client(SUPABASE_URL, SUPABASE_KEY, options=ClientOptions(schema="drawing"))
    return _supabase_client

_stats_cache = None
_stats_cache_ts = 0
STATS_CACHE_TTL = 60

_cld_cache: dict = None
_cld_cache_ts: float = 0
CLD_CACHE_TTL = 300  # 5분

def _invalidate_stats_cache():
    global _stats_cache, _stats_cache_ts
    _stats_cache = None
    _stats_cache_ts = 0

def _safe_int(val, default, min_val=1):
    try:
        return max(min_val, int(val))
    except (TypeError, ValueError):
        return default

def get_cloudinary_url(file_key):
    if not file_key:
        return None
    file_key = str(file_key).strip()
    if file_key.startswith("http"):
        if "cloudinary.com" in file_key and not any(
            file_key.lower().endswith(ext) for ext in [".pdf", ".jpg", ".jpeg", ".png"]
        ):
            return file_key + ".pdf"
        return file_key
    return file_key

def _sanitize_link(d: dict, key: str = "file_link"):
    fk = d.get(key, "")
    if fk and "res.cloudinary.com" not in fk:
        d[key] = None

def _configure_cloudinary():
    import cloudinary
    import cloudinary.api
    cld_url = os.environ.get("CLOUDINARY_URL", "")
    m = re.match(r"cloudinary://([^:]+):([^@]+)@(.+)", cld_url)
    if not m:
        raise ValueError("CLOUDINARY_URL 형식이 잘못되었습니다.")
    cloud_name = m.group(3)
    cloudinary.config(api_key=m.group(1), api_secret=m.group(2), cloud_name=cloud_name)
    return cloud_name

def _fetch_cloudinary_all(resource_type="image", force=False):
    global _cld_cache, _cld_cache_ts
    import cloudinary.api
    if not force and _cld_cache and (_time.time() - _cld_cache_ts) < CLD_CACHE_TTL:
        return _cld_cache
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
    _cld_cache = uploaded
    _cld_cache_ts = _time.time()
    return uploaded


@app.route("/")
def index():
    resp = make_response(render_template("index.html"))
    resp.headers['Cache-Control'] = 'no-cache, no-store, must-revalidate'
    resp.headers['Pragma'] = 'no-cache'
    resp.headers['Expires'] = '0'
    return resp


@app.route("/api/drawings")
def get_drawings():
    try:
        search   = request.args.get("search", "").strip()
        system   = request.args.get("system", "")
        status   = request.args.get("status", "")
        size     = request.args.get("size", "")
        page     = _safe_int(request.args.get("page", 1), 1)
        per_page = _safe_int(request.args.get("per_page", 20), 20)
        offset   = (page - 1) * per_page

        supabase = get_client()
        target   = TABLE_LATEST if status == "" else TABLE_ALL
        query    = supabase.table(target).select("*", count="exact")

        if search:
            s = search.replace(',', '\\,')
            query = query.or_(f"drawing_no.ilike.%{s}%,line_no.ilike.%{s}%,title.ilike.%{s}%,system.ilike.%{s}%")
        if system: query = query.eq("system", system)
        if status: query = query.eq("revision", status)
        if size:   query = _apply_size_filter(query, size)

        res = query.order("drawing_no").range(offset, offset + per_page - 1).execute()
        for row in res.data:
            row["size"] = _line_size(row.get("line_no"))
            if row.get("file_link"):
                row["file_link"] = get_cloudinary_url(row["file_link"])

        return jsonify({"data": res.data, "total": res.count, "page": page})
    except Exception as e:
        return jsonify({"error": str(e)}), 500


def _get_iso_stats(supabase):
    from concurrent.futures import ThreadPoolExecutor
    def q_total(): return supabase.table(TABLE_ALL).select("id", count="exact").limit(1).execute()
    def q_c01():   return supabase.table(TABLE_ALL).select("id", count="exact").eq("revision", "C01").limit(1).execute()
    def q_c01a():  return supabase.table(TABLE_ALL).select("id", count="exact").eq("revision", "C01A").limit(1).execute()
    def q_c01b():  return supabase.table(TABLE_ALL).select("id", count="exact").eq("revision", "C01B").limit(1).execute()
    with ThreadPoolExecutor(max_workers=4) as ex:
        t, c01, c01a, c01b = list(ex.map(lambda f: f(), [q_total, q_c01, q_c01a, q_c01b]))
    return {
        "total": t.count    if hasattr(t,    "count") else 0,
        "C01":   c01.count  if hasattr(c01,  "count") else 0,
        "C01A":  c01a.count if hasattr(c01a, "count") else 0,
        "C01B":  c01b.count if hasattr(c01b, "count") else 0,
    }


@app.route("/api/stats")
def get_stats():
    try:
        return jsonify(_get_iso_stats(get_client()))
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/api/init")
def api_init():
    global _stats_cache, _stats_cache_ts
    try:
        supabase = get_client()
        FILTERS = {"systems": SYSTEMS, "statuses": REVISIONS}
        DWG_COLS = "system,drawing_no,line_no,title,revision,issued_date,file_link"

        def q_drawings():
            res = supabase.table(TABLE_LATEST).select(DWG_COLS, count="exact").order("drawing_no").range(0, 19).execute()
            for row in res.data:
                row["size"] = _line_size(row.get("line_no"))
                if row.get("file_link"):
                    row["file_link"] = get_cloudinary_url(row["file_link"])
            return res

        if _stats_cache and (_time.time() - _stats_cache_ts) < STATS_CACHE_TTL:
            dwg_res = q_drawings()
            stats = _stats_cache
        else:
            from concurrent.futures import ThreadPoolExecutor
            with ThreadPoolExecutor(max_workers=2) as ex:
                f_dwg   = ex.submit(q_drawings)
                f_stats = ex.submit(_get_iso_stats, supabase)
                dwg_res = f_dwg.result()
                stats   = f_stats.result()
            _stats_cache = stats
            _stats_cache_ts = _time.time()

        return jsonify({"filters": FILTERS, "stats": stats,
                        "drawings": {"data": dwg_res.data, "total": dwg_res.count}})
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/api/filters")
def get_filters():
    return jsonify({"systems": SYSTEMS, "statuses": REVISIONS})


@app.route("/api/upload", methods=["POST"])
def upload_excel():
    try:
        file = request.files.get("file")
        if not file:
            return jsonify({"error": "Invalid file format"}), 400
        df = pd.read_excel(io.BytesIO(file.read()), sheet_name=0)
        df.columns = [str(c).lower().strip() for c in df.columns]
        df = df.fillna("")
        supabase = get_client()
        batch = []
        for r in df.to_dict("records"):
            dr_no = str(r.get("drawing_no", r.get("drawing_n", ""))).strip()
            if not dr_no:
                continue
            f_link = get_cloudinary_url(str(r.get("file_link", "")).strip()) or ""
            batch.append({
                "drawing_no": dr_no,
                "line_no":    str(r.get("line_no", "")).strip(),
                "system":     str(r.get("system", "")).strip(),
                "bore":       str(r.get("bore", "")).strip(),
                "title":      str(r.get("title", "")).strip(),
                "revision":   str(r.get("revision", "")).strip(),
                "file_link":  f_link,
            })
        inserted = 0
        for i in range(0, len(batch), 1000):
            supabase.table(TABLE_ALL).upsert(batch[i:i+1000], on_conflict="drawing_no,revision").execute()
            inserted += len(batch[i:i+1000])
        _invalidate_stats_cache()
        return jsonify({"success": True, "inserted": inserted, "processed": len(batch)})
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/api/export")
def export_excel():
    search = request.args.get("search", "").strip()
    status = request.args.get("status", "")
    try:
        from concurrent.futures import ThreadPoolExecutor
        supabase = get_client()
        cols = "system,drawing_no,line_no,title,revision,issued_date,bore"
        target = TABLE_LATEST if status == "" else TABLE_ALL
        count_res = supabase.table(target).select("id", count="exact").limit(1).execute()
        total = count_res.count if hasattr(count_res, "count") else 0
        page_size = 1000

        def fetch_batch(offset):
            q = supabase.table(target).select(cols)
            if search:
                s = search.replace(',', '\\,')
                q = q.or_(f"drawing_no.ilike.%{s}%,line_no.ilike.%{s}%,title.ilike.%{s}%")
            if status:
                q = q.eq("revision", status)
            return q.order("drawing_no").range(offset, offset + page_size - 1).execute().data

        with ThreadPoolExecutor(max_workers=4) as ex:
            results = list(ex.map(fetch_batch, range(0, total, page_size)))
        all_data = [item for batch in results for item in batch]
        if not all_data:
            return jsonify({"error": "No data to export"}), 404

        df = pd.DataFrame(all_data)
        output = io.BytesIO()
        with pd.ExcelWriter(output, engine="xlsxwriter") as writer:
            df.to_excel(writer, index=False, sheet_name="DrawingMaster")
        output.seek(0)
        filename = f"ISO_Drawing_Master_{datetime.now().strftime('%Y%m%d_%H%M')}.xlsx"
        return send_file(output, as_attachment=True, download_name=filename,
                         mimetype="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")
    except Exception as e:
        return jsonify({"error": f"Export failed: {str(e)}"}), 500


@app.route("/api/print")
def print_drawings():
    try:
        from concurrent.futures import ThreadPoolExecutor
        supabase = get_client()
        search = request.args.get("search", "").strip()
        system = request.args.get("system", "").strip()
        status = request.args.get("status", "").strip()
        target = TABLE_LATEST if status == "" else TABLE_ALL

        def build_query(base_q):
            q = base_q
            if search:
                s = search.replace(',', '\\,')
                q = q.or_(f"drawing_no.ilike.%{s}%,line_no.ilike.%{s}%,title.ilike.%{s}%")
            if system: q = q.eq("system", system)
            if status: q = q.eq("revision", status)
            return q

        count_res = build_query(supabase.table(target).select("id", count="exact")).limit(1).execute()
        total = count_res.count if hasattr(count_res, "count") else 0
        batch_size = 1000

        def fetch_batch(offset):
            q = build_query(supabase.table(target).select("system,drawing_no,line_no,title,revision,issued_date"))
            return q.order("drawing_no").range(offset, offset + batch_size - 1).execute().data

        with ThreadPoolExecutor(max_workers=4) as ex:
            results = list(ex.map(fetch_batch, range(0, total, batch_size)))
        all_data = [item for batch in results for item in batch]

        def esc(v):
            return str(v or "").replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")

        rows_html = ""
        for i, d in enumerate(all_data):
            rows_html += (
                f"<tr><td>{i+1}</td><td>{esc(d.get('system'))}</td>"
                f"<td class='col-dwg'>{esc(d.get('drawing_no'))}</td>"
                f"<td style='white-space:nowrap'>{esc(d.get('line_no'))}</td>"
                f"<td style='white-space:normal;text-align:left'>{esc(d.get('title'))}</td>"
                f"<td><span class='badge-rev'>{esc(d.get('revision'))}</span></td></tr>"
            )

        html = f"""<!DOCTYPE html>
<html><head>
<title>IPCS Print Report</title>
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
.col-dwg {{ color: #2563eb; font-weight: 500; }}
.badge-rev {{ padding: 1px 5px; border-radius: 3px; font-weight: 600;
              background-color: #f0fdf4; color: #16a34a; border: 0.2px solid #dcfce7; }}
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
    ⌛ 필터 적용 데이터({len(all_data)}건) 준비 중... 3.5초 후 인쇄창이 자동으로 뜹니다.
  </div>
  <button id="print-btn" onclick="window.print()">🖨️ 수동 인쇄 호출 (Force Print)</button>
</div>
<div id="print-main">
  <h2>IPCS ISO Drawing Master List ({len(all_data)} Records)</h2>
  <div class="meta">Generated: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}</div>
  <table>
    <thead><tr>
      <th style="width:35px">NO.</th><th>SYSTEM</th>
      <th class="col-dwg">DWG. NO.</th><th style="white-space:nowrap">LINE. NO.</th>
      <th style="min-width:180px">DRAWING TITLE</th><th>REV.</th>
    </tr></thead>
    <tbody>{rows_html}</tbody>
  </table>
</div>
<script>
  window.onload = function() {{
    const wait = Math.max(3500, Math.min(6000, {len(all_data)} * 1.5));
    setTimeout(function() {{
      window.print();
      window.onafterprint = function() {{ window.close(); }};
    }}, wait);
  }};
</script>
</body></html>"""
        return html
    except Exception as e:
        return f"Print failed: {str(e)}", 500


# ── Support Drawing ───────────────────────────────────────────

@app.route("/api/support/stats")
def api_support_stats():
    try:
        supabase = get_client()
        res = supabase.table("support_latest").select("id", count="exact").limit(1).execute()
        return jsonify({"total": res.count or 0})
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/api/support/filters")
def api_support_filters():
    return jsonify({
        "systems":   ["ALL"] + SYSTEMS,
        "types":     ["TYPICAL", "SPECIAL", "G", "GS", "U", "US", "W", "WS"],
        "revisions": ["C01", "C01A", "C01B"],
    })


@app.route("/api/support/drawings")
def api_support_drawings():
    try:
        search      = request.args.get("search", "").strip()
        system      = request.args.get("system", "")
        type_filter = request.args.get("type", "")
        size        = request.args.get("size", "")
        page        = _safe_int(request.args.get("page", 1), 1)
        per_page    = _safe_int(request.args.get("per_page", 20), 20)
        offset      = (page - 1) * per_page

        supabase = get_client()
        query = supabase.table("support_latest").select("*", count="exact")

        if search:
            s = search.replace(',', '\\,')
            query = query.or_(f"support_drawing.ilike.%{s}%,line_no.ilike.%{s}%,iso_drawing.ilike.%{s}%,system.ilike.%{s}%,type.ilike.%{s}%")
        if system: query = query.eq("system", system)
        if type_filter:
            if type_filter in ("G", "GS", "U", "US", "W", "WS"):
                query = query.like("type", f"({type_filter}-%")
            else:
                query = query.eq("type", type_filter)
        if size: query = _apply_size_filter(query, size)

        res = query.order("system").order("support_drawing").range(offset, offset + per_page - 1).execute()

        for d in res.data:
            d["size"] = _line_size(d.get("line_no"))
            _sanitize_link(d)

        return jsonify({"total": res.count, "data": res.data})
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/api/support/upload", methods=["POST"])
def api_support_upload():
    try:
        file = request.files.get("file")
        if not file:
            return jsonify({"error": "No file shared"}), 400
        df = pd.read_excel(io.BytesIO(file.read()), sheet_name=0)
        df.columns = [str(c).lower().strip() for c in df.columns]
        df = df.fillna("")
        supabase = get_client()
        batch = []
        for r in df.to_dict("records"):
            sup_dwg = str(r.get("support drawing", "")).strip()
            if not sup_dwg:
                continue
            batch.append({
                "system":          str(r.get("system", "")).strip(),
                "support_drawing": sup_dwg,
                "type":            str(r.get("type", "")).strip(),
                "iso_drawing":     str(r.get("iso drawing", "")).strip(),
                "line_no":         str(r.get("line no", "")).strip(),
                "clamp_height":    str(r.get("clamp height", "")).strip(),
                "l1":              str(r.get("l1", "")).strip(),
                "l2":              str(r.get("l2", "")).strip(),
                "l3":              str(r.get("l3", "")).strip(),
                "l4":              str(r.get("l4", "")).strip(),
                "revision":        str(r.get("revision", "")).strip(),
                "issued_date":     str(r.get("issue date", "")).strip(),
                "file_link":       "",
            })
        inserted = 0
        for i in range(0, len(batch), 500):
            supabase.table(TABLE_SUPPORT).upsert(batch[i:i+500], on_conflict="support_drawing,revision").execute()
            inserted += len(batch[i:i+500])
        return jsonify({"success": True, "inserted": inserted, "processed": len(batch)})
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/api/support/sync-links", methods=["POST"])
def api_support_sync_links():
    try:
        _configure_cloudinary()
        supabase = get_client()

        master_data = []
        page_from, page_size = 0, 1000
        while True:
            res = supabase.table(TABLE_SUPPORT).select("id,support_drawing,revision,type").range(
                page_from, page_from + page_size - 1
            ).execute()
            if not res.data:
                break
            master_data.extend(res.data)
            if len(res.data) < page_size:
                break
            page_from += page_size

        uploaded = _fetch_cloudinary_all(force=True)
        uploaded_norm  = {k.replace('--', '-'): v for k, v in uploaded.items()}
        uploaded_lower = {k.lower(): v for k, v in uploaded.items()}

        updates = []
        for row in master_data:
            dwg, rev = row.get("support_drawing"), row.get("revision")
            if not dwg or not rev:
                continue
            safe = str(dwg).replace('"', '').replace('/', '_')

            drawing_type = str(row.get("type", "")).strip().upper()
            if drawing_type != "SPECIAL":
                safe = re.sub(r'\s*\([^)]+\)\s*$', '', safe).strip()

            rev_up = rev.upper()
            secure_url = None
            for fname in (f"{safe}_{rev_up}", f"{safe}-{rev_up}", safe):
                if fname in uploaded:
                    secure_url = uploaded[fname]
                    break
                if f"{fname}.pdf" in uploaded:
                    secure_url = uploaded[f"{fname}.pdf"]
                    break
                if fname in uploaded_norm:
                    secure_url = uploaded_norm[fname]
                    break
                if f"{fname}.pdf" in uploaded_norm:
                    secure_url = uploaded_norm[f"{fname}.pdf"]
                    break
                if fname.lower() in uploaded_lower:
                    secure_url = uploaded_lower[fname.lower()]
                    break
                if f"{fname}.pdf".lower() in uploaded_lower:
                    secure_url = uploaded_lower[f"{fname}.pdf".lower()]
                    break
            if secure_url:
                url = secure_url if secure_url.lower().endswith(".pdf") else secure_url + ".pdf"
                updates.append({"id": row["id"], "support_drawing": dwg, "revision": rev, "file_link": url})

        supabase.table(TABLE_SUPPORT).update({"file_link": None}).neq("id", 0).execute()
        for i in range(0, len(updates), 500):
            supabase.table(TABLE_SUPPORT).upsert(updates[i:i + 500], on_conflict="support_drawing,revision").execute()

        return jsonify({"success": True, "synced": len(updates),
                        "message": f"실제 업로드된 {len(updates)}개의 도면만 링크를 연결했습니다."})
    except Exception as e:
        return jsonify({"error": str(e)}), 500


# ── P&ID Drawing ──────────────────────────────────────────────

@app.route("/api/pid/stats")
def api_pid_stats():
    try:
        supabase = get_client()
        res = supabase.table(TABLE_PID).select("id", count="exact").limit(1).execute()
        return jsonify({"total": res.count or 0})
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/api/pid/filters")
def api_pid_filters():
    try:
        supabase = get_client()
        res = supabase.table(TABLE_PID).select("system,revision").execute()
        systems   = sorted(set(r["system"]   for r in res.data if r.get("system")))
        revisions = sorted(set(r["revision"] for r in res.data if r.get("revision")))
        return jsonify({"systems": systems, "revisions": revisions})
    except Exception:
        return jsonify({"systems": [], "revisions": []}), 200


@app.route("/api/pid/drawings")
def api_pid_drawings():
    try:
        search   = request.args.get("search", "").strip()
        system   = request.args.get("system", "")
        revision = request.args.get("revision", "")
        page     = _safe_int(request.args.get("page", 1), 1)
        per_page = _safe_int(request.args.get("per_page", 50), 20)
        offset   = (page - 1) * per_page

        supabase = get_client()
        query = supabase.table(TABLE_PID).select("*", count="exact")
        if search:
            s = search.replace(',', '\\,')
            query = query.or_(f"drawing_no.ilike.%{s}%,title.ilike.%{s}%,system.ilike.%{s}%")
        if system:   query = query.eq("system", system)
        if revision: query = query.eq("revision", revision)

        res = query.order("id").range(offset, offset + per_page - 1).execute()
        for d in res.data:
            _sanitize_link(d)
        return jsonify({"total": res.count, "data": res.data})
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/api/pid/upload", methods=["POST"])
def api_pid_upload():
    try:
        file = request.files.get("file")
        if not file:
            return jsonify({"error": "No file shared"}), 400
        df = pd.read_excel(io.BytesIO(file.read()), header=1)
        df.columns = [str(c).strip() for c in df.columns]
        df = df.fillna("")
        supabase = get_client()
        batch = []
        for idx, r in df.iterrows():
            dwg_no = str(r.get("Drawing No", "")).strip()
            if not dwg_no or dwg_no == "nan":
                continue
            raw_date = r.get("Date", "")
            if hasattr(raw_date, "strftime"):
                date_val = raw_date.strftime("%Y-%m-%d")
            else:
                date_val = str(raw_date).strip()[:10] if raw_date and str(raw_date) != "nan" else ""
            batch.append({
                "id":          int(r.get("No", idx + 1)),
                "system":      str(r.get("System", "")).strip(),
                "drawing_no":  dwg_no,
                "title":       str(r.get("Title", "")).strip(),
                "revision":    str(r.get("Rev.", "")).strip(),
                "issued_date": date_val,
                "file_link":   "",
            })
        inserted = 0
        for i in range(0, len(batch), 500):
            supabase.table(TABLE_PID).upsert(batch[i:i+500], on_conflict="drawing_no").execute()
            inserted += len(batch[i:i+500])
        return jsonify({"success": True, "processed": len(batch), "inserted": inserted})
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/api/pid/sync-links", methods=["POST"])
def api_pid_sync_links():
    try:
        _configure_cloudinary()
        supabase = get_client()

        master_data, page_from, page_size = [], 0, 1000
        while True:
            res = supabase.table(TABLE_PID).select("id,drawing_no").range(
                page_from, page_from + page_size - 1
            ).execute()
            if not res.data:
                break
            master_data.extend(res.data)
            if len(res.data) < page_size:
                break
            page_from += page_size

        pid_nos  = {row["drawing_no"].lower() for row in master_data if row.get("drawing_no")}
        all_cld  = _fetch_cloudinary_all(force=True)
        uploaded = {k.lower(): v for k, v in all_cld.items() if k.lower() in pid_nos}

        updates = []
        for row in master_data:
            dwg = row.get("drawing_no")
            if not dwg:
                continue
            url = uploaded.get(dwg.lower())
            if url:
                link = url if url.lower().endswith(".pdf") else url + ".pdf"
                updates.append({"id": row["id"], "drawing_no": dwg, "file_link": link})

        supabase.table(TABLE_PID).update({"file_link": None}).neq("id", 0).execute()
        for i in range(0, len(updates), 500):
            supabase.table(TABLE_PID).upsert(updates[i:i+500], on_conflict="drawing_no").execute()

        return jsonify({"success": True, "synced": len(updates),
                        "message": f"{len(updates)}개 PID 도면 링크 연결 완료"})
    except Exception as e:
        return jsonify({"error": str(e)}), 500


# ── Valve Drawing ─────────────────────────────────────────────

@app.route("/api/valve/stats")
def api_valve_stats():
    try:
        supabase = get_client()
        res = supabase.table(TABLE_VALVE).select("id", count="exact").limit(1).execute()
        return jsonify({"total": res.count or 0})
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/api/valve/filters")
def api_valve_filters():
    try:
        supabase = get_client()
        res = supabase.table(TABLE_VALVE).select("valve,revision").execute()
        valves    = sorted(set(r["valve"]    for r in res.data if r.get("valve")))
        revisions = sorted(set(r["revision"] for r in res.data if r.get("revision")))
        return jsonify({"valves": valves, "revisions": revisions})
    except Exception:
        return jsonify({"valves": [], "revisions": []}), 200


@app.route("/api/valve/drawings")
def api_valve_drawings():
    try:
        search   = request.args.get("search", "").strip()
        valve    = request.args.get("valve", "")
        revision = request.args.get("revision", "")
        page     = _safe_int(request.args.get("page", 1), 1)
        per_page = _safe_int(request.args.get("per_page", 20), 20)
        offset   = (page - 1) * per_page

        supabase = get_client()
        query = supabase.table(TABLE_VALVE).select("*", count="exact")
        if search:
            s = search.replace(',', '\\,')
            query = query.or_(f"drawing_no.ilike.%{s}%,title.ilike.%{s}%,valve.ilike.%{s}%")
        if valve:    query = query.eq("valve", valve)
        if revision: query = query.eq("revision", revision)

        res = query.order("id").range(offset, offset + per_page - 1).execute()
        for d in res.data:
            _sanitize_link(d)
        return jsonify({"total": res.count, "data": res.data})
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/api/valve/upload", methods=["POST"])
def api_valve_upload():
    try:
        file = request.files.get("file")
        if not file:
            return jsonify({"error": "No file shared"}), 400
        df = pd.read_excel(io.BytesIO(file.read()), header=1)
        df.columns = [str(c).strip() for c in df.columns]
        df = df.fillna("")
        supabase = get_client()
        batch = []
        for idx, r in df.iterrows():
            dwg_no = str(r.get("Drawing No", r.get("Drawing No.", ""))).strip()
            if not dwg_no or dwg_no == "nan":
                continue
            raw_date = r.get("Date", "")
            if hasattr(raw_date, "strftime"):
                date_val = raw_date.strftime("%Y-%m-%d")
            else:
                date_val = str(raw_date).strip()[:10] if raw_date and str(raw_date) != "nan" else ""
            batch.append({
                "id":          int(r.get("No", idx + 1)),
                "valve":       str(r.get("Valve", r.get("Item", ""))).strip(),
                "drawing_no":  dwg_no,
                "title":       str(r.get("Title", "")).strip(),
                "revision":    str(r.get("Rev.", r.get("Revision", ""))).strip(),
                "issued_date": date_val,
                "file_link":   "",
            })
        inserted = 0
        for i in range(0, len(batch), 500):
            supabase.table(TABLE_VALVE).upsert(batch[i:i+500], on_conflict="drawing_no").execute()
            inserted += len(batch[i:i+500])
        return jsonify({"success": True, "processed": len(batch), "inserted": inserted})
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/api/valve/sync-links", methods=["POST"])
def api_valve_sync_links():
    try:
        _configure_cloudinary()
        supabase = get_client()

        master_data, page_from, page_size = [], 0, 1000
        while True:
            res = supabase.table(TABLE_VALVE).select("id,drawing_no").range(
                page_from, page_from + page_size - 1
            ).execute()
            if not res.data:
                break
            master_data.extend(res.data)
            if len(res.data) < page_size:
                break
            page_from += page_size

        all_cld  = _fetch_cloudinary_all(force=True)
        dwg_nos  = {row["drawing_no"].lower() for row in master_data if row.get("drawing_no")}
        uploaded = {k.lower(): v for k, v in all_cld.items() if k.lower() in dwg_nos}

        updates = []
        for row in master_data:
            dwg = row.get("drawing_no")
            if not dwg:
                continue
            url = uploaded.get(dwg.lower())
            if url:
                link = url if url.lower().endswith(".pdf") else url + ".pdf"
                updates.append({"id": row["id"], "drawing_no": dwg, "file_link": link})

        supabase.table(TABLE_VALVE).update({"file_link": None}).neq("id", 0).execute()
        for i in range(0, len(updates), 500):
            supabase.table(TABLE_VALVE).upsert(updates[i:i+500], on_conflict="drawing_no").execute()

        return jsonify({"success": True, "synced": len(updates),
                        "message": f"{len(updates)}개 Valve 도면 링크 연결 완료"})
    except Exception as e:
        return jsonify({"error": str(e)}), 500


# ── Speciality Drawing ────────────────────────────────────────

@app.route("/api/speciality/stats")
def api_speciality_stats():
    try:
        supabase = get_client()
        res = supabase.table(TABLE_SPECIALITY).select("id", count="exact").limit(1).execute()
        return jsonify({"total": res.count or 0})
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/api/speciality/filters")
def api_speciality_filters():
    try:
        supabase = get_client()
        res = supabase.table(TABLE_SPECIALITY).select("revision,title").execute()
        revisions = sorted(set(r["revision"] for r in res.data if r.get("revision")))
        titles    = sorted(set(r["title"]    for r in res.data if r.get("title")))
        return jsonify({"revisions": revisions, "titles": titles})
    except Exception:
        return jsonify({"revisions": [], "titles": []}), 200


@app.route("/api/speciality/drawings")
def api_speciality_drawings():
    try:
        search   = request.args.get("search", "").strip()
        revision = request.args.get("revision", "")
        title    = request.args.get("title", "")
        page     = _safe_int(request.args.get("page", 1), 1)
        per_page = _safe_int(request.args.get("per_page", 20), 20)
        offset   = (page - 1) * per_page

        supabase = get_client()
        query = supabase.table(TABLE_SPECIALITY).select("*", count="exact")
        if search:
            s = search.replace(',', '\\,')
            query = query.or_(f"drawing_no.ilike.%{s}%,title.ilike.%{s}%,vendor.ilike.%{s}%")
        if revision: query = query.eq("revision", revision)
        if title:    query = query.eq("title", title)

        res = query.order("drawing_no").range(offset, offset + per_page - 1).execute()
        for d in res.data:
            _sanitize_link(d)
        return jsonify({"total": res.count, "data": res.data})
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/api/speciality/upload", methods=["POST"])
def api_speciality_upload():
    try:
        file = request.files.get("file")
        if not file:
            return jsonify({"error": "No file shared"}), 400
        df = pd.read_excel(io.BytesIO(file.read()), header=1)
        df.columns = [str(c).strip() for c in df.columns]
        df = df.fillna("")
        supabase = get_client()
        batch = []
        for idx, r in df.iterrows():
            dwg_no = str(r.get("Drawing No", r.get("Drawing No.", ""))).strip()
            if not dwg_no or dwg_no == "nan":
                continue
            raw_date = r.get("Date", "")
            if hasattr(raw_date, "strftime"):
                date_val = raw_date.strftime("%Y-%m-%d")
            else:
                date_val = str(raw_date).strip()[:10] if raw_date and str(raw_date) != "nan" else ""
            batch.append({
                "id":          int(r.get("No", idx + 1)),
                "drawing_no":  dwg_no,
                "title":       str(r.get("Title", "")).strip(),
                "vendor":      str(r.get("Vendor", "")).strip(),
                "class":       str(r.get("Class", "")).strip(),
                "connection":  str(r.get("Connection", "")).strip(),
                "revision":    str(r.get("Rev.", r.get("Revision", ""))).strip(),
                "issued_date": date_val,
                "file_link":   "",
            })
        inserted = 0
        for i in range(0, len(batch), 500):
            supabase.table(TABLE_SPECIALITY).upsert(batch[i:i+500], on_conflict="drawing_no").execute()
            inserted += len(batch[i:i+500])
        return jsonify({"success": True, "processed": len(batch), "inserted": inserted})
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/api/speciality/sync-links", methods=["POST"])
def api_speciality_sync_links():
    try:
        _configure_cloudinary()
        supabase = get_client()

        master_data, page_from, page_size = [], 0, 1000
        while True:
            res = supabase.table(TABLE_SPECIALITY).select("id,drawing_no").range(
                page_from, page_from + page_size - 1
            ).execute()
            if not res.data:
                break
            master_data.extend(res.data)
            if len(res.data) < page_size:
                break
            page_from += page_size

        all_cld  = _fetch_cloudinary_all(force=True)
        dwg_nos  = {row["drawing_no"].lower() for row in master_data if row.get("drawing_no")}
        uploaded = {k.lower(): v for k, v in all_cld.items() if k.lower() in dwg_nos}

        updates = []
        for row in master_data:
            dwg = row.get("drawing_no")
            if not dwg:
                continue
            url = uploaded.get(dwg.lower())
            if url:
                link = url if url.lower().endswith(".pdf") else url + ".pdf"
                updates.append({"id": row["id"], "drawing_no": dwg, "file_link": link})

        supabase.table(TABLE_SPECIALITY).update({"file_link": None}).neq("id", 0).execute()
        for i in range(0, len(updates), 500):
            supabase.table(TABLE_SPECIALITY).upsert(updates[i:i+500], on_conflict="drawing_no").execute()

        return jsonify({"success": True, "synced": len(updates),
                        "message": f"{len(updates)}개 Speciality 도면 링크 연결 완료"})
    except Exception as e:
        return jsonify({"error": str(e)}), 500


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5100))
    app.run(host="0.0.0.0", port=port)
