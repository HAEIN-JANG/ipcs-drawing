# Support Master DB 업데이트 스크립트 (엑셀 → Supabase)
import openpyxl
import re
import os
import sys

def load_env():
    env_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".env")
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

load_env()

EXCEL_PATH = 'Raw File/TRUKISTAN PJT_PIPE SUPPORT LIST - TOTAL_260622.xlsx'
DRY_RUN = '--execute' not in sys.argv

SHEET_CONFIGS = [
    dict(col_system=0, col_support=1, col_type=2, col_iso=3, col_line=4,
         col_clamp=None, col_l1=5, col_l2=6, col_l3=7, col_l4=8,
         rev_cols=[(9, 10), (11, 12), (13, 14), (15, 16), (17, 18)]),
    dict(col_system=0, col_support=1, col_type=2, col_iso=3, col_line=4,
         col_clamp=5, col_l1=6, col_l2=7, col_l3=8, col_l4=9,
         rev_cols=[(10, 11), (12, 13)]),
]


def strip_type(val):
    return re.sub(r'\s*\([^)]+\)\s*$', '', str(val or '')).strip()


def extract_type_code(val):
    """(GS-46) → GS,  (U-101) → U,  None → ''"""
    m = re.match(r'\(\s*([A-Z]+)', str(val or ''))
    return m.group(1) if m else ''


def convert_iso(val):
    """CCP-W-B133-PI-140-AV-502(1OF1) → CCP-W-B133-PI-140-AV-502-1"""
    val = str(val or '').strip()
    m = re.search(r'\((\d+)OF\d+\)\s*$', val, re.IGNORECASE)
    if m:
        return f"{val[:m.start()].strip()}-{m.group(1)}"
    return val


def safe_str(val):
    if val is None:
        return ''
    s = str(val).strip()
    return '' if s.lower() in ('nan', 'none', '') else s


def fmt_date(val):
    if val is None:
        return ''
    if hasattr(val, 'strftime'):
        return val.strftime('%Y-%m-%d')
    s = str(val).strip().replace('.', '-').replace('/', '-')
    return s[:10] if len(s) >= 10 else s


def get_latest_rev(row, rev_col_pairs):
    latest_rev, latest_date = None, None
    for rev_idx, date_idx in rev_col_pairs:
        if rev_idx >= len(row):
            continue
        rv = safe_str(row[rev_idx].value)
        if rv:
            latest_rev = rv
            latest_date = fmt_date(row[date_idx].value) if date_idx < len(row) else ''
    return latest_rev, latest_date


def read_excel():
    wb = openpyxl.load_workbook(EXCEL_PATH, data_only=True)
    updates = {}   # stripped_key → row data dict
    deletes = set()

    for sheet_idx, cfg in enumerate(SHEET_CONFIGS):
        ws = wb.worksheets[sheet_idx]
        for row in ws.iter_rows(min_row=2):
            if cfg['col_support'] >= len(row):
                continue
            sup_cell = row[cfg['col_support']]
            sup_raw = safe_str(sup_cell.value)
            if not sup_raw:
                continue

            stripped = strip_type(sup_raw)
            is_strike = bool(sup_cell.font and sup_cell.font.strike)

            if is_strike:
                deletes.add(stripped)
                continue

            clamp = ''
            if cfg['col_clamp'] is not None and cfg['col_clamp'] < len(row):
                clamp = safe_str(row[cfg['col_clamp']].value)

            latest_rev, latest_date = get_latest_rev(row, cfg['rev_cols'])
            iso_raw = row[cfg['col_iso']].value if cfg['col_iso'] < len(row) else None

            updates[stripped] = {
                'support_drawing_excel': sup_raw,       # 원본 (type 포함)
                'system_name_excel':     safe_str(row[cfg['col_system']].value),
                'type_code':             extract_type_code(
                    row[cfg['col_type']].value if cfg['col_type'] < len(row) else None),
                'line_no':               safe_str(row[cfg['col_line']].value) if cfg['col_line'] < len(row) else '',
                'iso_drawing':           convert_iso(safe_str(iso_raw)),
                'clamp_height':          clamp,
                'l1':                    safe_str(row[cfg['col_l1']].value) if cfg['col_l1'] < len(row) else '',
                'l2':                    safe_str(row[cfg['col_l2']].value) if cfg['col_l2'] < len(row) else '',
                'l3':                    safe_str(row[cfg['col_l3']].value) if cfg['col_l3'] < len(row) else '',
                'l4':                    safe_str(row[cfg['col_l4']].value) if cfg['col_l4'] < len(row) else '',
                'revision':              latest_rev or '',
                'issued_date':           latest_date or '',
            }

    return updates, deletes


def fetch_db(supabase):
    db_data, pf, ps = [], 0, 1000
    while True:
        res = supabase.table("support_master").select(
            "id,system,support_drawing,type,line_no,iso_drawing,clamp_height,l1,l2,l3,l4,revision,issued_date"
        ).range(pf, pf + ps - 1).execute()
        if not res.data:
            break
        db_data.extend(res.data)
        if len(res.data) < ps:
            break
        pf += ps
    return db_data


def rev_rank(rev):
    """C01 < C01A < C01B < C02 < ... 정렬용"""
    m = re.match(r'C(\d+)([A-Z]*)', str(rev or '').upper())
    if not m:
        return (0, '')
    return (int(m.group(1)), m.group(2))


def main():
    print(f"{'[DRY RUN]' if DRY_RUN else '[EXECUTE]'} 엑셀 파일 읽는 중...")
    updates, deletes = read_excel()
    print(f"  엑셀 업데이트 대상: {len(updates):,}건")
    print(f"  엑셀 취소선(삭제): {len(deletes):,}건")

    from supabase import create_client, ClientOptions
    supabase = create_client(
        os.environ["SUPABASE_URL"], os.environ["SUPABASE_KEY"],
        options=ClientOptions(schema="drawing")
    )

    print("\nDB 데이터 조회 중...")
    db_data = fetch_db(supabase)
    print(f"  DB 전체 레코드: {len(db_data):,}건")
    max_id = max((r['id'] for r in db_data), default=0)

    # DB lookup (stripped key → list of DB rows)
    db_by_stripped = {}
    for r in db_data:
        k = strip_type(r['support_drawing'])
        db_by_stripped.setdefault(k, []).append(r)

    # (support_drawing, revision) → id (새 revision INSERT 시 id 확인용)
    db_by_sup_rev = {(r['support_drawing'], r['revision']): r['id'] for r in db_data}

    # system name → code 매핑 (매칭된 레코드로 역추론)
    sys_map = {}
    for stripped, excel_row in updates.items():
        db_rows = db_by_stripped.get(stripped, [])
        if db_rows:
            name = excel_row['system_name_excel'].strip().upper()
            if name and db_rows[0]['system']:
                sys_map[name] = db_rows[0]['system']

    # 매칭/신규 분리
    upsert_batch = []     # 매칭된 레코드 upsert (DB original support_drawing 사용)
    insert_batch = []     # 신규 레코드
    delete_ids = []
    rev_changed = []
    unmatched_keys = []
    no_sys_map = []
    next_id = max_id + 1

    for stripped, excel_row in updates.items():
        db_rows = db_by_stripped.get(stripped, [])
        new_rev = excel_row['revision']

        if db_rows:
            # 매칭 — DB rows 중 하나만 선택 (중복 id 방지)
            # 우선순위: new_rev와 같은 revision 행 > 가장 높은 revision 행
            rev_match = next((r for r in db_rows if r.get('revision') == new_rev), None)
            target = rev_match if rev_match else max(db_rows, key=lambda r: rev_rank(r.get('revision', '')))

            old_rev = target.get('revision', '')
            if new_rev and rev_rank(new_rev) != rev_rank(old_rev):
                rev_changed.append((stripped, old_rev, new_rev))

            upsert_batch.append({
                "id":              target['id'],
                "support_drawing": target['support_drawing'],
                "revision":        new_rev,
                "iso_drawing":     excel_row['iso_drawing'],
                "clamp_height":    excel_row['clamp_height'],
                "l1": excel_row['l1'], "l2": excel_row['l2'],
                "l3": excel_row['l3'], "l4": excel_row['l4'],
                "issued_date":     excel_row['issued_date'],
            })
        else:
            # 신규
            unmatched_keys.append(stripped)
            name = excel_row['system_name_excel'].strip().upper()
            sys_code = sys_map.get(name, '')
            if not sys_code:
                no_sys_map.append((stripped, excel_row['system_name_excel']))
                # 키워드 기반 폴백
                for k, v in sys_map.items():
                    if k and k[:5] in name:
                        sys_code = v
                        break
            insert_batch.append({
                "id":              next_id,
                "system":          sys_code,
                "support_drawing": excel_row['support_drawing_excel'],
                "type":            excel_row['type_code'],
                "iso_drawing":     excel_row['iso_drawing'],
                "line_no":         excel_row['line_no'],
                "clamp_height":    excel_row['clamp_height'],
                "l1": excel_row['l1'], "l2": excel_row['l2'],
                "l3": excel_row['l3'], "l4": excel_row['l4'],
                "revision":        excel_row['revision'],
                "issued_date":     excel_row['issued_date'],
                "file_link":       "",
            })
            next_id += 1

    for stripped in deletes:
        db_rows = db_by_stripped.get(stripped, [])
        for r in db_rows:
            delete_ids.append(r['id'])

    print(f"\n=== 매칭 결과 ===")
    print(f"  DB upsert (기존 갱신): {len(upsert_batch):,}건")
    print(f"  DB insert (신규):      {len(insert_batch):,}건")
    print(f"  DB delete (취소선):    {len(delete_ids):,}건")
    print(f"  Revision 변경:         {len(rev_changed):,}건")
    print(f"  system 코드 미매핑:    {len(no_sys_map):,}건")

    if rev_changed[:5]:
        print(f"\n  Revision 변경 샘플:")
        for k, old, new in rev_changed[:5]:
            print(f"    {k}: {old} → {new}")

    if no_sys_map[:5]:
        print(f"\n  system 미매핑 샘플 (신규 레코드 system 공백 가능):")
        for k, name in no_sys_map[:5]:
            print(f"    {k} | {name}")

    if DRY_RUN:
        print("\n[DRY RUN 완료] 실제 실행: python update_support_from_excel.py --execute")
        return

    # ── 실행 ─────────────────────────────────────────────────────
    print("\n[1/3] upsert (기존 갱신)...")
    done = 0
    for i in range(0, len(upsert_batch), 500):
        supabase.table("support_master").upsert(
            upsert_batch[i:i+500], on_conflict="id"
        ).execute()
        done += len(upsert_batch[i:i+500])
        print(f"  {done:,}/{len(upsert_batch):,}")

    print("\n[2/3] insert (신규)...")
    done = 0
    for i in range(0, len(insert_batch), 500):
        supabase.table("support_master").insert(insert_batch[i:i+500]).execute()
        done += len(insert_batch[i:i+500])
        print(f"  {done:,}/{len(insert_batch):,}")

    print("\n[3/3] delete (취소선)...")
    done = 0
    for i in range(0, len(delete_ids), 500):
        supabase.table("support_master").delete().in_("id", delete_ids[i:i+500]).execute()
        done += len(delete_ids[i:i+500])
        print(f"  {done:,}/{len(delete_ids):,}")

    print(f"\n완료. upsert {len(upsert_batch):,}건, insert {len(insert_batch):,}건, delete {len(delete_ids):,}건.")


if __name__ == '__main__':
    main()
