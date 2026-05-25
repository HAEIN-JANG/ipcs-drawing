from flask import Flask, render_template, request, jsonify, send_file
import pandas as pd
import io
import os
from datetime import datetime
from supabase import create_client, Client, ClientOptions
import cloudinary
import cloudinary.uploader
from werkzeug.utils import secure_filename
from dotenv import load_dotenv

load_dotenv()

app = Flask(__name__)

def get_secret(key, default=None):
    return os.environ.get(key, default)

def get_supabase() -> Client:
    url = get_secret("SUPABASE_URL")
    key = get_secret("SUPABASE_KEY")
    if not url or not key:
        raise ValueError("Missing Supabase configuration")
    options = ClientOptions(schema="drawing")
    return create_client(url, key, options=options)

c_name = get_secret("CLOUDINARY_NAME")
c_key = get_secret("CLOUDINARY_API_KEY")
c_secret = get_secret("CLOUDINARY_API_SECRET")
if all([c_name, c_key, c_secret]):
    cloudinary.config(cloud_name=c_name, api_key=c_key, api_secret=c_secret, secure=True)

TABLE_ALL = "dwg_iso"
TABLE_LATEST = "dwg_latest"
TABLE_SUPPORT = "support_master"
TABLE_VALVE = "valve_master"

def get_cloudinary_url(file_key):
    if not file_key: return None
    if file_key.startswith("http"): return file_key
    return cloudinary.utils.cloudinary_url(file_key, resource_type="image", secure=True)[0]

@app.route("/")
def index():
    return render_template("index.html")

@app.route("/api/stats")
def api_stats():
    try:
        supabase = get_supabase()
        total_res = supabase.table(TABLE_ALL).select("id", count="exact").limit(1).execute()
        c01_res   = supabase.table(TABLE_ALL).select("id", count="exact").eq("revision", "C01").execute()
        c01a_res  = supabase.table(TABLE_ALL).select("id", count="exact").eq("revision", "C01A").execute()
        c01b_res  = supabase.table(TABLE_ALL).select("id", count="exact").eq("revision", "C01B").execute()
        return jsonify({
            "total": total_res.count if hasattr(total_res, 'count') else 0,
            "C01": c01_res.count if hasattr(c01_res, 'count') else 0,
            "C01A": c01a_res.count if hasattr(c01a_res, 'count') else 0,
            "C01B": c01b_res.count if hasattr(c01b_res, 'count') else 0
        })
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@app.route("/api/filters")
def api_filters():
    return jsonify({
        "areas": ["MB", "YARD", "YD BLDG"],
        "systems": ["AS", "ATM", "CCW", "CD", "DW", "FG", "FGH", "FO", "FW", "GT MISC", "HP", "HW", "IA", "LO", "LP", "N2", "PW", "RW", "SA", "SS", "ST MISC", "SW", "WWT"],
        "statuses": ["C01", "C01A", "C01B"]
    })

@app.route("/api/drawings")
def api_drawings():
    try:
        search_query = request.args.get("search", "")
        area = request.args.get("area", "")
        system = request.args.get("system", "")
        status = request.args.get("status", "")
        page = int(request.args.get("page", 1))
        per_page = int(request.args.get("per_page", 50))
        offset = (page - 1) * per_page

        supabase = get_supabase()
        target_table = TABLE_LATEST if status == "" else TABLE_ALL
        query = supabase.table(target_table).select("*", count="exact")
        
        if search_query:
            query = query.or_(f"drawing_no.ilike.%{search_query}%,line_no.ilike.%{search_query}%,title.ilike.%{search_query}%")
        if area: query = query.eq("area", area)
        if system: query = query.eq("system", system)
        if status: query = query.eq("revision", status)
        
        res = query.order("drawing_no").range(offset, offset + per_page - 1).execute()
        
        data = res.data
        for row in data:
            fk = str(row.get('file_link', '')).strip()
            if fk:
                row['file_link'] = get_cloudinary_url(fk)
                
        return jsonify({
            "total": res.count,
            "data": data
        })
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@app.route("/api/upload", methods=["POST"])
def api_upload():
    if 'file' not in request.files:
        return jsonify({"error": "No file part"}), 400
    file = request.files['file']
    if file.filename == '':
        return jsonify({"error": "No selected file"}), 400
    
    if file and (file.filename.endswith('.xlsx') or file.filename.endswith('.xls')):
        try:
            df_up = pd.read_excel(file)
            df_up.columns = [str(c).lower().strip() for c in df_up.columns]
            records = []
            for _, r in df_up.iterrows():
                dr_no = str(r.get("drawing_no", r.get("drawing_n", ""))).strip()
                if dr_no and dr_no != 'nan': 
                    records.append({
                        "drawing_no": dr_no, 
                        "line_no": str(r.get("line_no", "")).strip() if pd.notna(r.get("line_no")) else "", 
                        "system": str(r.get("system", "")).strip() if pd.notna(r.get("system")) else "", 
                        "area": str(r.get("area", "")).strip() if pd.notna(r.get("area")) else "", 
                        "bore": str(r.get("bore", "")).strip() if pd.notna(r.get("bore")) else "", 
                        "title": str(r.get("title", "")).strip() if pd.notna(r.get("title")) else "", 
                        "revision": str(r.get("revision", "")).strip() if pd.notna(r.get("revision")) else "", 
                        "file_link": str(r.get("file_link", "")).strip() if pd.notna(r.get("file_link")) else ""
                    })
            if records:
                supabase = get_supabase()
                inserted = 0
                for i in range(0, len(records), 1000):
                    batch = records[i:i+1000]
                    supabase.table(TABLE_ALL).upsert(batch, on_conflict="drawing_no,revision").execute()
                    inserted += len(batch)
                return jsonify({
                    "processed": len(records),
                    "inserted": inserted,
                    "skipped": 0,
                    "failed": 0
                })
            else:
                return jsonify({"error": "No valid records found in file"}), 400
        except Exception as e:
            return jsonify({"error": str(e)}), 500
    return jsonify({"error": "Invalid file format"}), 400

@app.route("/api/export")
def api_export():
    try:
        supabase = get_supabase()
        search_query = request.args.get("search", "")
        area = request.args.get("area", "")
        system = request.args.get("system", "")
        status = request.args.get("status", "")
        
        query = supabase.table(TABLE_ALL).select("*")
        if search_query:
            query = query.or_(f"drawing_no.ilike.%{search_query}%,line_no.ilike.%{search_query}%,title.ilike.%{search_query}%")
        if area: query = query.eq("area", area)
        if system: query = query.eq("system", system)
        if status: query = query.eq("revision", status)
        
        all_data = query.execute().data
        if all_data:
            export_df = pd.DataFrame(all_data)
            output = io.BytesIO()
            with pd.ExcelWriter(output, engine='xlsxwriter') as writer:
                export_df.to_excel(writer, index=False, sheet_name='DrawingMaster')
            output.seek(0)
            return send_file(
                output, 
                as_attachment=True, 
                download_name=f"ISO_Master_{datetime.now().strftime('%Y%m%d_%H%M')}.xlsx",
                mimetype="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
            )
        return jsonify({"error": "No data found"}), 404
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@app.route("/api/print")
def api_print():
    return "Print report not yet fully implemented for web layout.", 200

@app.route("/api/support/stats")
def api_support_stats():
    try:
        supabase = get_supabase()
        res = supabase.table(TABLE_SUPPORT).select("id", count="exact").limit(1).execute()
        return jsonify({"total": res.count if hasattr(res, 'count') else 0})
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@app.route("/api/support/filters")
def api_support_filters():
    try:
        supabase = get_supabase()
        res = supabase.table("support_latest").select("type").execute()
        all_types = sorted(set(r["type"] for r in res.data if r.get("type")))
        types = ["SPECIAL"] + [t for t in all_types if t != "SPECIAL"]
    except Exception:
        types = []
    return jsonify({
        "systems": ["AS", "ATM", "CCW", "CD", "DW", "FG", "FGH", "FO", "FW", "GT MISC", "HP", "HW", "IA", "LO", "LP", "N2", "PW", "RW", "SA", "SS", "ST MISC", "SW", "WWT"],
        "revisions": ["C01", "C01A", "C01B"],
        "types": types
    })

@app.route("/api/support/drawings")
def api_support_drawings():
    try:
        search = request.args.get("search", "").strip()
        system = request.args.get("system", "")
        type_filter = request.args.get("type", "")
        page = int(request.args.get("page", 1))
        per_page = int(request.args.get("per_page", 20))
        offset = (page - 1) * per_page

        supabase = get_supabase()
        query = supabase.table("support_latest").select("*", count="exact")
        if search:
            query = query.or_(f"support_drawing.ilike.%{search}%,line_no.ilike.%{search}%,type.ilike.%{search}%")
        if system:
            query = query.eq("system", system)
        if type_filter:
            query = query.eq("type", type_filter)

        res = query.order("system").order("support_drawing").range(offset, offset + per_page - 1).execute()

        for d in res.data:
            d['title'] = d.get('type', '')
            fk = d.get('file_link', '')
            if fk and 'res.cloudinary.com' not in fk:
                d['file_link'] = None

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
        records = df.to_dict("records")
        supabase = get_supabase()

        batch = []
        for r in records:
            sup_dwg = str(r.get("support drawing", "")).strip()
            if not sup_dwg:
                continue
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
                "file_link":       ""
            })

        inserted_count = 0
        if batch:
            for i in range(0, len(batch), 500):
                chunk = batch[i:i+500]
                supabase.table(TABLE_SUPPORT).upsert(chunk, on_conflict="support_drawing,revision").execute()
                inserted_count += len(chunk)

        return jsonify({"success": True, "inserted": inserted_count, "processed": len(batch), "skipped": 0, "failed": 0})
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@app.route("/api/valve/upload", methods=["POST"])
def api_valve_upload():
    try:
        file = request.files.get("file")
        if not file:
            return jsonify({"error": "No file shared"}), 400
        df = pd.read_excel(io.BytesIO(file.read()), sheet_name=0)
        df.columns = [str(c).lower().strip() for c in df.columns]
        df = df.fillna("")
        records = df.to_dict("records")
        supabase = get_supabase()

        batch = []
        for idx, r in enumerate(records, start=1):
            dwg_no = str(r.get("drawing no", r.get("drawing_no", ""))).strip()
            if not dwg_no:
                continue
            # class 컬럼: 숫자(150/300/600 등)로 저장된 경우 정수 문자열로 변환
            raw_class = r.get("class", "")
            try:
                class_val = str(int(float(raw_class))) if raw_class != "" else ""
            except (ValueError, TypeError):
                class_val = str(raw_class).strip()

            # issued_date: datetime → YYYY-MM-DD 문자열
            raw_date = r.get("issue date", r.get("issued_date", ""))
            if hasattr(raw_date, "strftime"):
                date_val = raw_date.strftime("%Y-%m-%d")
            else:
                date_val = str(raw_date).strip() if raw_date else ""

            batch.append({
                "id":          idx,
                "drawing_no":  dwg_no,
                "valve":       str(r.get("type", "")).strip(),
                "size":        str(r.get("size", "")).strip(),
                "title":       str(r.get("title", "")).strip(),
                "vendor":      str(r.get("vendor", "")).strip(),
                "body":        str(r.get("body", "")).strip(),
                "class":       class_val,
                "connection":  str(r.get("connection", "")).strip(),
                "revision":    str(r.get("revision", "")).strip(),
                "issued_date": date_val,
                "file_link":   ""
            })

        inserted_count = 0
        if batch:
            for i in range(0, len(batch), 500):
                chunk = batch[i:i+500]
                supabase.table(TABLE_VALVE).upsert(chunk, on_conflict="drawing_no,revision").execute()
                inserted_count += len(chunk)

        return jsonify({"success": True, "inserted": inserted_count, "processed": len(batch), "skipped": 0, "failed": 0})
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@app.route("/api/support/sync-links", methods=["POST"])
def api_support_sync_links():
    try:
        import cloudinary.api
        cld_url = os.environ.get("CLOUDINARY_URL", "")
        m = re.match(r"cloudinary://([^:]+):([^@]+)@(.+)", cld_url)
        if not m:
            return jsonify({"error": "CLOUDINARY_URL 설정 오류"}), 400
        cloud_name = m.group(3)
        cloudinary.config(api_key=m.group(1), api_secret=m.group(2), cloud_name=cloud_name)

        supabase = get_supabase()
        supabase.table(TABLE_SUPPORT).update({"file_link": ""}).neq("id", 0).execute()

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
                updates.append({"id": row["id"], "file_link": f"https://res.cloudinary.com/{cloud_name}/image/upload/{filename_with_ext}"})

        if updates:
            for i in range(0, len(updates), 1000):
                supabase.table(TABLE_SUPPORT).upsert(updates[i:i+1000]).execute()

        return jsonify({"success": True, "synced": len(updates), "message": f"{len(updates)}개 도면 링크 연결 완료"})
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@app.route("/api/valve/sync-links", methods=["POST"])
def api_valve_sync_links():
    try:
        import cloudinary.api
        cld_url = os.environ.get("CLOUDINARY_URL", "")
        m = re.match(r"cloudinary://([^:]+):([^@]+)@(.+)", cld_url)
        if not m:
            return jsonify({"error": "CLOUDINARY_URL 설정 오류"}), 400
        cloud_name = m.group(3)
        cloudinary.config(api_key=m.group(1), api_secret=m.group(2), cloud_name=cloud_name)

        supabase = get_supabase()
        supabase.table(TABLE_VALVE).update({"file_link": ""}).neq("id", 0).execute()

        master_data = []
        page_from = 0
        page_size = 1000
        while True:
            res_page = supabase.table(TABLE_VALVE).select("id, drawing_no, revision").range(page_from, page_from + page_size - 1).execute()
            if not res_page.data:
                break
            master_data.extend(res_page.data)
            if len(res_page.data) < page_size:
                break
            page_from += page_size

        uploaded_files = {} # base_name_lower -> secure_url
        
        # Image resources
        next_cursor = None
        while True:
            kwargs = {"type": "upload", "max_results": 500, "resource_type": "image"}
            if next_cursor:
                kwargs["next_cursor"] = next_cursor
            res = cloudinary.api.resources(**kwargs)
            for item in res.get('resources', []):
                pid = item['public_id']
                base_pid = pid.split('/')[-1].lower()
                secure_url = item.get('secure_url')
                if item.get('format') == 'pdf' and not secure_url.lower().endswith('.pdf'):
                    secure_url += '.pdf'
                
                uploaded_files[base_pid] = secure_url
                if base_pid.endswith('.pdf'):
                    uploaded_files[base_pid[:-4]] = secure_url
            next_cursor = res.get('next_cursor')
            if not next_cursor:
                break

        # Raw resources (just in case)
        next_cursor = None
        while True:
            kwargs = {"type": "upload", "max_results": 500, "resource_type": "raw"}
            if next_cursor:
                kwargs["next_cursor"] = next_cursor
            try:
                res = cloudinary.api.resources(**kwargs)
                for item in res.get('resources', []):
                    pid = item['public_id']
                    base_pid = pid.split('/')[-1].lower()
                    secure_url = item.get('secure_url')
                    
                    uploaded_files[base_pid] = secure_url
                    if base_pid.endswith('.pdf'):
                        uploaded_files[base_pid[:-4]] = secure_url
                next_cursor = res.get('next_cursor')
            except Exception:
                break
            if not next_cursor:
                break

        updates = []
        for row in master_data:
            dwg = row.get("drawing_no")
            rev = row.get("revision")
            if not dwg:
                continue
            safe_dwg = str(dwg).replace('"', '').replace('/', '_').strip()
            
            # Match 1: safe_dwg (new format, drawing number only)
            filename_dwg = safe_dwg.lower()
            # Match 2: safe_dwg_revision (old format/fallback)
            filename_rev = f"{safe_dwg}_{str(rev).upper()}".lower()
            
            match_url = None
            if filename_dwg in uploaded_files:
                match_url = uploaded_files[filename_dwg]
            elif filename_rev in uploaded_files:
                match_url = uploaded_files[filename_rev]
            
            if match_url:
                updates.append({"id": row["id"], "file_link": match_url})

        if updates:
            for i in range(0, len(updates), 1000):
                supabase.table(TABLE_VALVE).upsert(updates[i:i+1000]).execute()

        return jsonify({"success": True, "synced": len(updates), "message": f"{len(updates)}개 밸브 도면 링크 연결 완료"})
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@app.route("/api/debug/cld-list")
def api_debug_cld_list():
    """Cloudinary 업로드 파일 목록 조회 (디버그용)"""
    try:
        import cloudinary.api
        cld_url = os.environ.get("CLOUDINARY_URL", "")
        m = re.match(r"cloudinary://([^:]+):([^@]+)@(.+)", cld_url)
        if not m:
            return jsonify({"error": "CLOUDINARY_URL 설정 오류"}), 400
        cloudinary.config(api_key=m.group(1), api_secret=m.group(2), cloud_name=m.group(3))

        all_ids = []
        next_cursor = None
        while True:
            kwargs = {"type": "upload", "max_results": 500, "resource_type": "image"}
            if next_cursor:
                kwargs["next_cursor"] = next_cursor
            res = cloudinary.api.resources(**kwargs)
            for item in res.get("resources", []):
                all_ids.append(item["public_id"])
            next_cursor = res.get("next_cursor")
            if not next_cursor:
                break

        # valve 관련 파일만 필터링
        valve_ids = [x for x in all_ids if "146" in x or "valve" in x.lower() or "VALVE" in x]
        return jsonify({"total": len(all_ids), "valve_count": len(valve_ids), "valve_files": valve_ids[:50], "all_sample": all_ids[:20]})
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@app.route("/api/valve/stats")
def api_valve_stats():
    try:
        supabase = get_supabase()
        res = supabase.table(TABLE_VALVE).select("id", count="exact").limit(1).execute()
        return jsonify({"total": res.count or 0})
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@app.route("/api/valve/filters")
def api_valve_filters():
    try:
        supabase = get_supabase()
        res = supabase.table(TABLE_VALVE).select("valve,revision").execute()
        valves = sorted(set(r["valve"] for r in res.data if r.get("valve")))
        revisions = sorted(set(r["revision"] for r in res.data if r.get("revision")))
        return jsonify({"valves": valves, "revisions": revisions})
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@app.route("/api/valve/drawings")
def api_valve_drawings():
    try:
        search = request.args.get("search", "").strip()
        valve = request.args.get("valve", "")
        revision = request.args.get("revision", "")
        page = int(request.args.get("page", 1))
        per_page = int(request.args.get("per_page", 20))
        offset = (page - 1) * per_page

        supabase = get_supabase()
        query = supabase.table(TABLE_VALVE).select("*", count="exact")
        if search:
            query = query.or_(f"drawing_no.ilike.%{search}%,title.ilike.%{search}%,vendor.ilike.%{search}%,valve.ilike.%{search}%")
        if valve:
            query = query.eq("valve", valve)
        if revision:
            query = query.eq("revision", revision)

        res = query.order("drawing_no").range(offset, offset + per_page - 1).execute()

        for d in res.data:
            fk = d.get("file_link", "")
            if fk and "res.cloudinary.com" not in fk:
                d["file_link"] = None

        return jsonify({"total": res.count, "data": res.data})
    except Exception as e:
        return jsonify({"error": str(e)}), 500

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 8080)), debug=True)
