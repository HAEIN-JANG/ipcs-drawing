"""
IPCS Drawing - Supabase 데이터 마이그레이션 스크립트
구 프로젝트(liwonki / construction 스키마)
→ 신 프로젝트(hijang0909 / drawing 스키마)

실행 전: setup_drawing_schema.sql을 신 프로젝트 SQL Editor에서 먼저 실행하세요.
실행: python migrate_to_new_supabase.py
"""

from supabase import create_client, ClientOptions

# ── 구 DB (liwonki / construction) ──
OLD_URL = "https://ognhvfvlboqblueuldlm.supabase.co"
OLD_KEY = "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJpc3MiOiJzdXBhYmFzZSIsInJlZiI6Im9nbmh2ZnZsYm9xYmx1ZXVsZGxtIiwicm9sZSI6ImFub24iLCJpYXQiOjE3NzI3MzY2NTUsImV4cCI6MjA4ODMxMjY1NX0.paO5jr16M7yTySUAp9LgberoatDds9rTNa_eCU_ET_I"

# ── 신 DB (hijang0909 / drawing) ──
NEW_URL = "https://wsvqeoufppcoeclbfbgz.supabase.co"
NEW_KEY = "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJpc3MiOiJzdXBhYmFzZSIsInJlZiI6IndzdnFlb3VmcHBjb2VjbGJmYmd6Iiwicm9sZSI6ImFub24iLCJpYXQiOjE3NzYwMTQwNDIsImV4cCI6MjA5MTU5MDA0Mn0.p0rk8oPdVWO7xgvQiGUDSxzNWoi06NJZ3zcFN9SvGrE"

PAGE_SIZE = 500

# 신 DB에 존재하는 컬럼만 허용 (구 DB의 updated_at 등 불필요 컬럼 제거)
DWG_ISO_COLS = {"id", "drawing_no", "line_no", "system", "area", "bore", "title", "revision", "issued_date", "file_link"}
SUPPORT_COLS = {"id", "system", "support_drawing", "type", "iso_drawing", "line_no", "l1", "l2", "l3", "l4", "revision", "issued_date", "file_link"}


def filter_cols(rows, allowed_cols):
    return [{k: v for k, v in row.items() if k in allowed_cols} for row in rows]


def fetch_all(client, table):
    """1000행 제한 우회 - 페이지네이션으로 전체 조회"""
    all_rows = []
    offset = 0
    while True:
        res = client.table(table).select("*").range(offset, offset + PAGE_SIZE - 1).execute()
        if not res.data:
            break
        all_rows.extend(res.data)
        print(f"  [{table}] {len(all_rows)}건 조회됨...", end="\r")
        if len(res.data) < PAGE_SIZE:
            break
        offset += PAGE_SIZE
    print(f"  [{table}] 총 {len(all_rows)}건 조회 완료")
    return all_rows


def upsert_all(client, table, rows, conflict_key):
    """500건씩 배치 upsert"""
    total = len(rows)
    inserted = 0
    for i in range(0, total, PAGE_SIZE):
        chunk = rows[i:i + PAGE_SIZE]
        client.table(table).upsert(chunk, on_conflict=conflict_key).execute()
        inserted += len(chunk)
        print(f"  [{table}] {inserted}/{total} upsert 완료...", end="\r")
    print(f"  [{table}] 총 {inserted}건 upsert 완료")
    return inserted


def main():
    print("=" * 60)
    print("IPCS Drawing - Supabase 마이그레이션 시작")
    print("=" * 60)

    old_db = create_client(OLD_URL, OLD_KEY, options=ClientOptions(schema="construction"))
    new_db = create_client(NEW_URL, NEW_KEY, options=ClientOptions(schema="drawing"))

    # ── 1. dwg_iso 마이그레이션 ──
    print("\n[1/2] dwg_iso (ISO 도면) 마이그레이션...")
    dwg_rows = fetch_all(old_db, "dwg_iso")
    if dwg_rows:
        upsert_all(new_db, "dwg_iso", filter_cols(dwg_rows, DWG_ISO_COLS), "drawing_no,revision")
    else:
        print("  dwg_iso: 데이터 없음")

    # ── 2. support_master 마이그레이션 ──
    print("\n[2/2] support_master (지지대 도면) 마이그레이션...")
    sup_rows = fetch_all(old_db, "support_master")
    if sup_rows:
        upsert_all(new_db, "support_master", filter_cols(sup_rows, SUPPORT_COLS), "support_drawing,revision")
    else:
        print("  support_master: 데이터 없음")

    print("\n" + "=" * 60)
    print("마이그레이션 완료!")
    print(f"  ISO 도면:   {len(dwg_rows)}건")
    print(f"  지지대 도면: {len(sup_rows)}건")
    print("=" * 60)
    print("\n다음 단계:")
    print("  1. .env 파일이 이미 업데이트되었습니다.")
    print("  2. Render 환경변수에서 SUPABASE_URL, SUPABASE_KEY를 업데이트하세요.")
    print("  3. git push 후 Render 자동 배포 확인")


if __name__ == "__main__":
    main()
