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
    options = ClientOptions(schema="public")
    return create_client(url, key, options=options)

c_name = get_secret("CLOUDINARY_NAME")
c_key = get_secret("CLOUDINARY_API_KEY")
c_secret = get_secret("CLOUDINARY_API_SECRET")
if all([c_name, c_key, c_secret]):
    cloudinary.config(cloud_name=c_name, api_key=c_key, api_secret=c_secret, secure=True)

TABLE_ALL = "dwg_iso"
TABLE_LATEST = "dwg_latest"

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

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 8080)), debug=True)
