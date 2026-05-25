import os
import re
import io
import pandas as pd
from datetime import datetime
from flask import Flask, render_template, request, jsonify, send_file, make_response
from supabase import create_client, Client, ClientOptions

# ── [.env 파일을 직접 읽어오는 로직] ──
def load_env_manually():
    base_dir = os.path.dirname(os.path.abspath(__file__))
    env_path = os.path.join(base_dir, ".env")
    if os.path.exists(env_path):
        with open(env_path, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line or line.startswith("#"): continue
                if "=" in line:
                    try:
                        key, val = line.split("=", 1)
                        os.environ[key.strip()] = val.strip()
                    except: continue

load_env_manually()

template_dir = os.path.abspath(os.path.dirname(__file__))
app = Flask(__name__, template_folder=template_dir, static_folder=template_dir)

# Gzip 압축 (JSON 응답 크기 최대 70% 감소)
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
TABLE_ALL = "dwg_iso"
TABLE_LATEST = "dwg_latest"
TABLE_SUPPORT = "support_master"

# ── Supabase 클라이언트 전역 캐시 (요청마다 재생성 방지) ──
_supabase_client: Client = None

def get_client() -> Client:
    global _supabase_client
    if _supabase_client is not None:
        return _supabase_client
    url = os.environ.get("SUPABASE_URL") or SUPABASE_URL
    key = os.environ.get("SUPABASE_KEY") or SUPABASE_KEY
    if not url or not key:
        raise ValueError("SUPABASE_URL, SUPABASE_KEY를 확인하세요.")
    _supabase_client = create_client(url, key, options=ClientOptions(schema="drawing"))
    return _supabase_client

# ── Stats 인메모리 캐시 (60초 TTL) ──
import time as _time
_stats_cache = None
_stats_cache_ts = 0
STATS_CACHE_TTL = 60  # seconds

def _invalidate_stats_cache():
    global _stats_cache, _stats_cache_ts
    _stats_cache = None
    _stats_cache_ts = 0
    
def get_cloudinary_url(file_key):
    if not file_key: return None
    file_key = str(file_key).strip()
    
    # Strip incorrect folder prefixes (IPCS_Drawing/.../Large/)
    prefix_pattern = r"(cloudinary\.com/[^/]+/image/upload/)IPCS_Drawing/[^/]+/Large/"
    if re.search(prefix_pattern, file_key):
        file_key = re.sub(prefix_pattern, r"\1", file_key)
        
    if file_key.startswith("http"):
        if "cloudinary.com" in file_key and not any(file_key.lower().endswith(ext) for ext in [".pdf", ".jpg", ".jpeg", ".png", ".dwg"]):
            return file_key + ".pdf"
        return file_key
    return file_key # Fallback

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
        search = request.args.get("search", "").strip()
        area = request.args.get("area", "")
        system = request.args.get("system", "")
        status = request.args.get("status", "")
        page = int(request.args.get("page", 1))
        per_page = int(request.args.get("per_page", 20))
        offset = (page - 1) * per_page

        supabase = get_client()
        target_table = TABLE_LATEST if status == "" else TABLE_ALL
        query = supabase.table(target_table).select("*", count="exact")
        
        if search:
            # Escape comma for Supabase OR filter
            s_esc = search.replace(',', '\\,')
            query = query.or_(f"drawing_no.ilike.%{s_esc}%,line_no.ilike.%{s_esc}%,title.ilike.%{s_esc}%,system.ilike.%{s_esc}%,area.ilike.%{s_esc}%")
        if area: query = query.eq("area", area)
        if system: query = query.eq("system", system)
        if status: query = query.eq("revision", status)

        res = query.order("drawing_no").range(offset, offset + per_page - 1).execute()
        
        data = res.data
        for row in data:
            fk = row.get('file_link')
            if fk:
                row['file_link'] = get_cloudinary_url(fk)
                
        return jsonify({"data": data, "total": res.count, "page": page})
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@app.route("/api/stats")
def get_stats():
    try:
        supabase = get_client()
        from concurrent.futures import ThreadPoolExecutor
        def q_total(): return supabase.table(TABLE_ALL).select("id", count="exact").limit(1).execute()
        def q_c01():   return supabase.table(TABLE_ALL).select("id", count="exact").eq("revision", "C01").execute()
        def q_c01a():  return supabase.table(TABLE_ALL).select("id", count="exact").eq("revision", "C01A").execute()
        def q_c01b():  return supabase.table(TABLE_ALL).select("id", count="exact").eq("revision", "C01B").execute()
        with ThreadPoolExecutor(max_workers=4) as ex:
            total_res, c01_res, c01a_res, c01b_res = list(ex.map(lambda f: f(), [q_total, q_c01, q_c01a, q_c01b]))
        return jsonify({
            "total": total_res.count if hasattr(total_res, 'count') else 0,
            "C01":   c01_res.count  if hasattr(c01_res,  'count') else 0,
            "C01A":  c01a_res.count if hasattr(c01a_res, 'count') else 0,
            "C01B":  c01b_res.count if hasattr(c01b_res, 'count') else 0
        })
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@app.route("/api/init")
def api_init():
    """filters + stats + 첫 페이지 drawings를 단일 요청으로 반환 (초기 로딩 최적화)"""
    global _stats_cache, _stats_cache_ts
    try:
        supabase = get_client()
        from concurrent.futures import ThreadPoolExecutor
        FILTERS = {
            "areas": ["MB", "YARD", "YD BLDG"],
            "systems": ["AS", "ATM", "CCW", "CD", "DW", "FG", "FGH", "FO", "FW", "GT MISC", "HP", "HW", "IA", "LO", "LP", "N2", "PW", "RW", "SA", "SS", "ST MISC", "SW", "WWT"],
            "statuses": ["C01", "C01A", "C01B"]
        }
        DWG_COLS = "area,system,drawing_no,line_no,title,revision,issued_date,file_link"

        def q_drawings():
            res = supabase.table(TABLE_LATEST).select(DWG_COLS, count="exact").order("drawing_no").range(0, 19).execute()
            for row in res.data:
                fk = row.get('file_link')
                if fk: row['file_link'] = get_cloudinary_url(fk)
            return res

        # Stats 캐시 유효 시 DB 조회 생략
        if _stats_cache and (_time.time() - _stats_cache_ts) < STATS_CACHE_TTL:
            dwg_res = q_drawings()
            stats = _stats_cache
        else:
            def q_total(): return supabase.table(TABLE_ALL).select("id", count="exact").limit(1).execute()
            def q_c01():   return supabase.table(TABLE_ALL).select("id", count="exact").eq("revision", "C01").limit(1).execute()
            def q_c01a():  return supabase.table(TABLE_ALL).select("id", count="exact").eq("revision", "C01A").limit(1).execute()
            def q_c01b():  return supabase.table(TABLE_ALL).select("id", count="exact").eq("revision", "C01B").limit(1).execute()
            with ThreadPoolExecutor(max_workers=5) as ex:
                dwg_res, total_res, c01_res, c01a_res, c01b_res = list(
                    ex.map(lambda f: f(), [q_drawings, q_total, q_c01, q_c01a, q_c01b])
                )
            stats = {
                "total": total_res.count if hasattr(total_res, 'count') else 0,
                "C01":   c01_res.count  if hasattr(c01_res,  'count') else 0,
                "C01A":  c01a_res.count if hasattr(c01a_res, 'count') else 0,
                "C01B":  c01b_res.count if hasattr(c01b_res, 'count') else 0
            }
            _stats_cache = stats
            _stats_cache_ts = _time.time()

        return jsonify({
            "filters": FILTERS,
            "stats": stats,
            "drawings": {"data": dwg_res.data, "total": dwg_res.count}
        })
    except Exception as e:
        return jsonify({"error": str(e)}), 500
@app.route("/api/support/stats")
def api_support_stats():
    try:
        supabase = get_client()
        res = supabase.table(TABLE_SUPPORT).select("id", count="exact").limit(1).execute()
        return jsonify({
            "total": res.count if hasattr(res, 'count') else 0
        })
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@app.route("/api/support/filters")
def api_support_filters():
    return jsonify({
        "systems": ["AS", "ATM", "CCW", "CD", "DW", "FG", "FGH", "FO", "FW", "GT MISC", "HP", "HW", "IA", "LO", "LP", "N2", "PW", "RW", "SA", "SS", "ST MISC", "SW", "WWT"],
        "revisions": ["C01", "C01A", "C01B"]
    })

@app.route("/api/support/drawings")
def api_support_drawings():
    try:
        search = request.args.get("search", "").strip()
        system = request.args.get("system", "")
        page = int(request.args.get("page", 1))
        per_page = int(request.args.get("per_page", 20))
        offset = (page - 1) * per_page

        supabase = get_client()
        # Query the view directly for latest revisions
        query = supabase.table("support_latest").select("*", count="exact")

        if search:
            # Escape comma for Supabase OR filter
            s_esc = search.replace(',', '\\,')
            query = query.or_(f"support_drawing.ilike.%{s_esc}%,line_no.ilike.%{s_esc}%,iso_drawing.ilike.%{s_esc}%,system.ilike.%{s_esc}%,type.ilike.%{s_esc}%")
        if system:
            query = query.eq("system", system)

        res = query.order("system").order("support_drawing").range(offset, offset + per_page - 1).execute()

        # Add title alias and validate file_link (only Cloudinary URLs allowed)
        for d in res.data:
            d['title'] = d.get('type', '')
            fk = d.get('file_link', '')
            if fk and 'res.cloudinary.com' not in fk:
                d['file_link'] = None

        return jsonify({
            "total": res.count,
            "data": res.data
        })
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@app.route("/api/filters")
def get_filters():
    return jsonify({
        "areas": ["MB", "YARD", "YD BLDG"],
        "systems": ["AS", "ATM", "CCW", "CD", "DW", "FG", "FGH", "FO", "FW", "GT MISC", "HP", "HW", "IA", "LO", "LP", "N2", "PW", "RW", "SA", "SS", "ST MISC", "SW", "WWT"],
        "statuses": ["C01", "C01A", "C01B"]
    })

@app.route("/api/upload", methods=["POST"])
def upload_excel():
    try:
        file = request.files["file"]
        if not file:    return jsonify({"error": "Invalid file format"}), 400
        df = pd.read_excel(io.BytesIO(file.read()), sheet_name=0)
        df.columns = [str(c).lower().strip() for c in df.columns]
        df = df.fillna("")
        records = df.to_dict("records")
        supabase = get_client()
        batch = []
        for r in records:
            dr_no = str(r.get("drawing_no", r.get("drawing_n", ""))).strip()
            if not dr_no: continue
            
            f_link = str(r.get("file_link", "")).strip()
            if f_link:
                f_link = get_cloudinary_url(f_link)
                
            batch.append({
                "drawing_no": dr_no,
                "line_no":    str(r.get("line_no", "")).strip(),
                "system":     str(r.get("system", "")).strip(),
                "area":       str(r.get("area", "")).strip(),
                "bore":       str(r.get("bore", "")).strip(),
                "title":      str(r.get("title", "")).strip(),
                "revision":   str(r.get("revision", "")).strip(),
                "file_link":  f_link
            })
        inserted_count = 0
        if batch:
            for i in range(0, len(batch), 1000):
                chunk = batch[i:i+1000]
                supabase.table(TABLE_ALL).upsert(chunk, on_conflict="drawing_no,revision").execute()
                inserted_count += len(chunk)
        _invalidate_stats_cache()
        return jsonify({"success": True, "inserted": inserted_count, "processed": len(batch)})
    except Exception as e:
        print(f"UPLOAD ERROR: {str(e)}")
        return jsonify({"error": str(e)}), 500

@app.route("/api/support/sync-links", methods=["POST"])
def api_support_sync_links():
    try:
        cld_url = os.environ.get("CLOUDINARY_URL", "")
        if not cld_url:
            return jsonify({"error": "Cloudinary 연동 설정이 없습니다. .env 파일에 CLOUDINARY_URL을 추가해주세요!"}), 400

        # Explicitly configure Cloudinary from URL
        import cloudinary
        import cloudinary.api
        m = re.match(r"cloudinary://([^:]+):([^@]+)@(.+)", cld_url)
        if not m:
            return jsonify({"error": "CLOUDINARY_URL 형식이 잘못되었습니다."}), 400
        cloud_name = m.group(3)
        cloudinary.config(api_key=m.group(1), api_secret=m.group(2), cloud_name=cloud_name)

        supabase = get_client()

        # 1. Clear all existing support links first
        supabase.table(TABLE_SUPPORT).update({"file_link": ""}).neq("id", 0).execute()

        # 2. Fetch all support drawings from DB (paginate to handle >1000 rows)
        master_data = []
        page_from = 0
        page_size = 1000
        while True:
            res_page = supabase.table(TABLE_SUPPORT).select("id, support_drawing, revision, system").range(page_from, page_from + page_size - 1).execute()
            if not res_page.data:
                break
            master_data.extend(res_page.data)
            if len(res_page.data) < page_size:
                break
            page_from += page_size

        # 3. Fetch ALL resources from Cloudinary (only pass next_cursor when not None)
        uploaded_files = set()
        next_cursor = None
        while True:
            kwargs = {"type": "upload", "max_results": 500}
            if next_cursor:
                kwargs["next_cursor"] = next_cursor
            res = cloudinary.api.resources(**kwargs)
            for item in res.get('resources', []):
                uploaded_files.add(item['public_id'].split('/')[-1])
            next_cursor = res.get('next_cursor')
            if not next_cursor:
                break

        updates = []
        for row in master_data:
            dwg = row.get("support_drawing")
            rev = row.get("revision")
            if not dwg or not rev:
                continue
            safe_dwg = str(dwg).replace('"', '').replace('/', '_')
            filename = f"{safe_dwg}_{str(rev).upper()}"
            filename_with_ext = f"{filename}.pdf"

            if filename in uploaded_files or filename_with_ext in uploaded_files:
                file_link = f"https://res.cloudinary.com/{cloud_name}/image/upload/{filename_with_ext}"
                updates.append({"id": row["id"], "file_link": file_link})

        if updates:
            for i in range(0, len(updates), 1000):
                supabase.table(TABLE_SUPPORT).upsert(updates[i:i + 1000]).execute()

        return jsonify({
            "success": True,
            "synced": len(updates),
            "message": f"실제 업로드된 {len(updates)}개의 도면만 링크를 연결했습니다."
        })
    except Exception as e:
        import traceback
        traceback.print_exc()
        return jsonify({"error": str(e)}), 500
@app.route("/api/support/upload", methods=["POST"])
def api_support_upload():
    try:
        file = request.files["file"]
        if not file: return jsonify({"error": "No file shared"}), 400
        df = pd.read_excel(io.BytesIO(file.read()), sheet_name=0)
        df.columns = [str(c).lower().strip() for c in df.columns]
        df = df.fillna("")
        records = df.to_dict("records")
        supabase = get_client()

        batch = []
        for r in records:
            sup_dwg = str(r.get("support drawing", "")).strip()
            if not sup_dwg: continue

            batch.append({
                "system":          str(r.get("system", "")).strip(),
                "support_drawing": sup_dwg,
                "type":            str(r.get("type", "")).strip(),
                "iso_drawing":     str(r.get("iso drawing", r.get("iso drawubg", ""))).strip(),
                "line_no":         str(r.get("line no", "")).strip(),
                "l1":              str(r.get("l1", "")).strip(),
                "l2":              str(r.get("l2", "")).strip(),
                "l3":              str(r.get("l3", "")).strip(),
                "l4":              str(r.get("l4", "")).strip(),
                "revision":        str(r.get("revision", "")).strip(),
                "issued_date":     str(r.get("issue date", "")).strip(),
                # file_link은 Excel에서 가져오지 않음 — Sync Links로만 설정
                "file_link":       ""
            })
        
        inserted_count = 0
        if batch:
            for i in range(0, len(batch), 500):
                chunk = batch[i:i+500]
                # We upsert based on (support_drawing, revision)
                # Note: This requires the unique index from update_support_master.sql
                supabase.table(TABLE_SUPPORT).upsert(chunk, on_conflict="support_drawing,revision").execute()
                inserted_count += len(chunk)
        
        return jsonify({
            "success": True, 
            "inserted": inserted_count, 
            "processed": len(batch),
            "skipped": 0,
            "failed": 0
        })
    except Exception as e:
        print(f"SUPPORT UPLOAD ERROR: {str(e)}")
        return jsonify({"error": str(e)}), 500

@app.route("/api/export")
def export_excel():
    search = request.args.get("search", "").strip()
    status = request.args.get("status", "")
    try:
        from concurrent.futures import ThreadPoolExecutor
        supabase = get_client()
        cols = "area,system,drawing_no,line_no,title,revision,issued_date,bore"
        count_res = supabase.table(TABLE_ALL).select("id", count="exact").limit(1).execute()
        total_count = count_res.count if hasattr(count_res, 'count') else 0
        page_size = 1000
        offsets = list(range(0, total_count, page_size))
        def fetch_batch(offset):
            # Export should probably also respect the "latest" logic if status is empty?
            # But usually export is for all or specific.
            # Let's match the get_drawings logic for consistency.
            target = TABLE_LATEST if status == "" else TABLE_ALL
            q = supabase.table(target).select(cols)
            if search: q = q.or_(f"drawing_no.ilike.%{search}%,line_no.ilike.%{search}%,title.ilike.%{search}%")
            if status: q = q.eq("revision", status)
            return q.order("drawing_no").range(offset, offset + page_size - 1).execute().data
        with ThreadPoolExecutor(max_workers=4) as executor:
            results = list(executor.map(fetch_batch, offsets))
        all_data = [item for sublist in results for item in sublist]
        if not all_data: return jsonify({"error": "No data to export"}), 404
        df = pd.DataFrame(all_data)
        output = io.BytesIO()
        with pd.ExcelWriter(output, engine='xlsxwriter') as writer:
            df.to_excel(writer, index=False, sheet_name='DrawingMaster')
        output.seek(0)
        filename = f"ISO_Drawing_Master_{datetime.now().strftime('%Y%m%d_%H%M')}.xlsx"
        return send_file(output, as_attachment=True, download_name=filename, mimetype="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")
    except Exception as e:
        return jsonify({"error": f"Export failed: {str(e)}"}), 500

@app.route('/api/print')
def print_drawings():
    try:
        from concurrent.futures import ThreadPoolExecutor
        supabase = get_client()
        
        search = request.args.get('search', '').strip()
        area = request.args.get('area', '').strip()
        system = request.args.get('system', '').strip()
        status = request.args.get('status', '').strip()

        target_table = TABLE_LATEST if status == "" else TABLE_ALL

        def build_print_query(base_q):
            q = base_q
            if search:
                q = q.or_(f"drawing_no.ilike.%{search}%,line_no.ilike.%{search}%,title.ilike.%{search}%")
            if area: q = q.eq('area', area)
            if system: q = q.eq('system', system)
            if status: q = q.eq('revision', status)
            return q

        count_q = build_print_query(supabase.table(target_table).select("id", count="exact"))
        count_res = count_q.limit(1).execute()
        total_count = count_res.count if hasattr(count_res, 'count') else 0

        batch_size = 1000
        offsets = [i * batch_size for i in range((total_count + batch_size - 1) // batch_size)]
        def fetch_batch(offset):
            q = build_print_query(supabase.table(target_table).select("area,system,drawing_no,line_no,title,revision,issued_date"))
            return q.order('drawing_no').range(offset, offset + batch_size - 1).execute().data

        with ThreadPoolExecutor(max_workers=4) as executor:
            results = list(executor.map(fetch_batch, offsets))
        all_data = [item for sublist in results for item in sublist]

        html = f"""
        <html>
        <head>
            <title>IPCS Print Report</title>
            <style>
                @import url('https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600&display=swap');
                @page {{ size: landscape; margin: 8mm !important; }}
                * {{ -webkit-print-color-adjust: exact !important; }}
                body {{ font-family: 'Inter', sans-serif; margin: 15px 0; background: #f8fafc; font-size: 8px !important; }}
                #print-main {{ background: #fff; padding: 20px; width: 96%; margin: 0 auto; box-shadow: 0 0 15px rgba(0,0,0,0.05); }}
                h2 {{ text-align: center; margin-bottom: 10px; font-size: 15px; font-weight: 600; color: #1e293b; }}
                .meta {{ text-align: right; margin-bottom: 5px; font-size: 7px; color: #64748b; }}
                <table> {{ width: 100%; border-collapse: collapse; border: 0.5px solid #94a3b8; }}
                th, td {{ border: 0.4px solid #cbd5e1; padding: 4px 6px; text-align: center !important; }}
                th {{ background-color: #f1f5f9; font-weight: 600; text-transform: uppercase; }}
                .col-dwg {{ color: #2563eb; font-weight: 500; text-decoration: none; }}
                .badge-rev {{ padding: 1px 5px; border-radius: 3px; font-weight: 600; background-color: #f0fdf4; color: #16a34a; border: 0.2px solid #dcfce7; }}
                #top-ctrl {{ width: 96%; margin: 10px auto; display: flex; justify-content: flex-end; align-items: center; gap: 15px; }}
                #print-btn {{ background: #2563eb; color: #fff; border: none; padding: 6px 15px; border-radius: 4px; font-size: 11px; cursor: pointer; }}
                @media print {{
                    body {{ background: #fff; margin: 0; }}
                    #print-main {{ width: 100%; padding: 0; box-shadow: none; }}
                    #top-ctrl {{ display: none !important; }}
                }}
            </style>
        </head>
        <body>
            <div id="top-ctrl">
                <div style="font-size: 9px; color: #dc2626; font-weight: 500;">
                    ⌛ 필터 적용 데이터({len(all_data)}건) 준비 중... 3.5초 후 인쇄창이 자동으로 뜹니다.
                </div>
                <button id="print-btn" onclick="window.print()">🖨️ 수동 인쇄 호출 (Force Print)</button>
            </div>
            <div id="print-main">
                <h2>IPCS ISO Drawing Master List ({len(all_data)} Records)</h2>
                <div class="meta">Generated: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}</div>
                <table>
                    <thead>
                        <tr>
                            <th style="width:35px;">NO.</th>
                            <th>AREA</th>
                            <th>SYSTEM</th>
                            <th class="col-dwg">DWG. NO.</th>
                            <th style="white-space:nowrap;">LINE. NO.</th>
                            <th style="min-width:180px;">DRAWING TITLE</th>
                            <th>REV.</th>
                        </tr>
                    </thead>
                    <tbody>
        """
        for i, d in enumerate(all_data):
            rev = d.get('revision','')
            html += f"""
                <tr>
                    <td>{i+1}</td>
                    <td>{d.get('area','')}</td>
                    <td>{d.get('system','')}</td>
                    <td class="col-dwg">{d.get('drawing_no','')}</td>
                    <td style="white-space:nowrap;">{d.get('line_no','')}</td>
                    <td style="white-space:normal; text-align:left !important;">{d.get('title','')}</td>
                    <td><span class="badge-rev">{rev}</span></td>
                </tr>
            """
        html += f"""
                    </tbody>
                </table>
            </div>
            <script>
                function runPrint() {{
                    window.print();
                    window.onafterprint = function() {{ window.close(); }};
                }}
                window.onload = function() {{
                    const wait = Math.max(3500, Math.min(6000, {len(all_data)} * 1.5));
                    setTimeout(runPrint, wait);
                }};
            </script>
        </body>
        </html>"""
        return html
    except Exception as e:
        return f"Print failed: {str(e)}", 500

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5100))
    app.run(host="0.0.0.0", port=port)
