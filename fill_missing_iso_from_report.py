# Support_ISO_Missing_NotInTotalFile.xlsx 내용을 support_master DB에 반영하는 스크립트
import os
import re
import sys

import pandas as pd


def load_env():
    root = os.path.dirname(os.path.abspath(__file__))
    with open(os.path.join(root, ".env"), "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            k, v = line.split("=", 1)
            os.environ.setdefault(k.strip(), v.strip())


load_env()

from supabase import create_client, ClientOptions

SRC_PATH = "Raw File/Support_ISO_Missing_NotInTotalFile.xlsx"
DRY_RUN = "--execute" not in sys.argv

# Remark가 TYPE 변경이 아닌 Support Tag No 자체의 변경을 의미하는 예외 케이스
TAG_RENAME = {
    '1"-ST-B2-34/453-GB1-G-211': '1"-ST-B2-34/453-GB1-U-211',
}

TYPE_CHANGE_RE = re.compile(r"TYPE\s*변경\s*:\s*.+->\s*([A-Za-z0-9\-]+)")


def safe_str(val):
    if val is None:
        return ""
    s = str(val).strip()
    return "" if s.lower() in ("nan", "none", "") else s


def normalize_type(raw):
    raw = raw.strip()
    if not raw or raw.upper() in ("SPECIAL", "TYPICAL"):
        return raw
    if raw.startswith("(") and raw.endswith(")"):
        return raw
    return f"({raw})"


def main():
    df = pd.read_excel(SRC_PATH)
    df.columns = [str(c).strip() for c in df.columns]

    supabase = create_client(
        os.environ["SUPABASE_URL"], os.environ["SUPABASE_KEY"],
        options=ClientOptions(schema="drawing")
    )

    updates = []
    for _, r in df.iterrows():
        tag = safe_str(r.get("Support Tag No."))
        revision = safe_str(r.get("REV"))
        if not tag or not revision:
            continue

        res = supabase.table("support_master").select("id,support_drawing,type").eq(
            "support_drawing", tag
        ).eq("revision", revision).execute()
        if not res.data:
            print(f"  [경고] DB에서 찾을 수 없음: {tag} ({revision})")
            continue
        row = res.data[0]

        payload = {
            "iso_drawing": safe_str(r.get("ISO Drawing No")),
            "line_no":     safe_str(r.get("Line No.")),
        }

        remark = safe_str(r.get("Remark"))
        m = TYPE_CHANGE_RE.search(remark)
        if m:
            payload["type"] = normalize_type(m.group(1))
        else:
            file_type = safe_str(r.get("TYPE"))
            if file_type:
                payload["type"] = normalize_type(file_type)

        new_tag = TAG_RENAME.get(tag)
        if new_tag:
            payload["support_drawing"] = new_tag

        updates.append((row["id"], tag, row.get("type"), payload))

    print(f"업데이트 대상: {len(updates)}건\n")
    for id_, tag, old_type, payload in updates:
        changes = []
        if payload.get("type") and payload["type"] != old_type:
            changes.append(f"TYPE {old_type} -> {payload['type']}")
        if payload.get("support_drawing"):
            changes.append(f"Tag {tag} -> {payload['support_drawing']}")
        changes.append(f"ISO={payload['iso_drawing']}")
        changes.append(f"Line={payload['line_no']}")
        print(f"  id={id_} {tag}: {', '.join(changes)}")

    if DRY_RUN:
        print("\n[DRY RUN] DB에 반영하지 않았습니다. 실제 반영하려면 --execute 옵션 사용")
        return

    print("\nDB 업데이트 중...")
    for id_, tag, old_type, payload in updates:
        supabase.table("support_master").update(payload).eq("id", id_).execute()
    print(f"완료: {len(updates)}건 업데이트")


if __name__ == "__main__":
    main()
