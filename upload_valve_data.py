"""
Valve Drawing List → Supabase valve_master 업로드 스크립트
실행: python upload_valve_data.py
"""

import os
import sys
import openpyxl
from datetime import datetime
from pathlib import Path

# ── 의존성 체크 ──────────────────────────────────────
try:
    from supabase import create_client, ClientOptions
except ImportError:
    print("❌ supabase 패키지 없음. 아래 명령어로 설치 후 재실행:")
    print("   pip install supabase openpyxl")
    sys.exit(1)

# ── 설정 ─────────────────────────────────────────────
SUPABASE_URL = "https://wsvqeoufppcoeclbfbgz.supabase.co"
SUPABASE_KEY = (
    "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9."
    "eyJpc3MiOiJzdXBhYmFzZSIsInJlZiI6IndzdnFlb3VmcHBjb2VjbGJmYmd6Iiwicm9sZSI6ImFub24iLC"
    "JpYXQiOjE3NzYwMTQwNDIsImV4cCI6MjA5MTU5MDA0Mn0."
    "p0rk8oPdVWO7xgvQiGUDSxzNWoi06NJZ3zcFN9SvGrE"
)

EXCEL_PATH = Path(__file__).parent / "Raw Data" / "Valve Drawing List.xlsx"
CHUNK_SIZE = 100

# ── 메인 ─────────────────────────────────────────────
def main():
    print("=" * 55)
    print("  Valve Drawing List → Supabase 업로드")
    print("=" * 55)

    # 엑셀 파일 존재 확인
    if not EXCEL_PATH.exists():
        print(f"❌ 파일 없음: {EXCEL_PATH}")
        sys.exit(1)

    # Supabase 연결
    print("\n[1/3] Supabase 연결 중...")
    supabase = create_client(
        SUPABASE_URL, SUPABASE_KEY, options=ClientOptions(schema="drawing")
    )
    res = supabase.table("valve_master").select("id", count="exact").limit(1).execute()
    print(f"      연결 성공 ✅  현재 valve_master 레코드: {res.count}건")

    # 엑셀 파싱
    print("\n[2/3] 엑셀 파싱 중...")
    wb = openpyxl.load_workbook(EXCEL_PATH)
    ws = wb["Sheet1"]

    rows = []
    for row in ws.iter_rows(min_row=2, values_only=True):
        no, drawing_no, title, vendor, valve_type, size, body, class_, connection, revision, issue_date = row
        if not drawing_no:
            continue

        # 날짜 처리
        if isinstance(issue_date, datetime):
            issued_date_str = issue_date.strftime("%Y-%m-%d")
        elif issue_date:
            issued_date_str = str(issue_date)
        else:
            issued_date_str = ""

        # class 처리 (숫자 → 문자)
        if isinstance(class_, (int, float)):
            class_str = str(int(class_))
        elif class_:
            class_str = str(class_).strip()
        else:
            class_str = ""

        rows.append({
            "id":          int(no),
            "drawing_no":  str(drawing_no).strip(),
            "valve":       str(valve_type).strip() if valve_type else "",
            "size":        str(size).strip()        if size        else "",
            "title":       str(title).strip()       if title       else "",
            "vendor":      str(vendor).strip()      if vendor      else "",
            "body":        str(body).strip()        if body        else "",
            "class":       class_str,
            "connection":  str(connection).strip()  if connection  else "",
            "revision":    str(revision).strip()    if revision    else "",
            "issued_date": issued_date_str,
            "file_link":   "",
        })

    print(f"      파싱 완료 ✅  {len(rows)}건 준비")

    # Supabase upsert
    print(f"\n[3/3] 업로드 중 (chunk: {CHUNK_SIZE}건)...")
    inserted = 0
    for i in range(0, len(rows), CHUNK_SIZE):
        chunk = rows[i : i + CHUNK_SIZE]
        supabase.table("valve_master").upsert(
            chunk, on_conflict="drawing_no,revision"
        ).execute()
        inserted += len(chunk)
        print(f"      {inserted}/{len(rows)}건 완료...", end="\r")

    # 결과 확인
    res2 = supabase.table("valve_master").select("id", count="exact").limit(1).execute()
    print(f"\n\n{'='*55}")
    print(f"  ✅ 업로드 완료!  valve_master 총 레코드: {res2.count}건")
    print(f"{'='*55}\n")


if __name__ == "__main__":
    main()
