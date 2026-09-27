# Checklist — 전체 코드 점검 개선 1~4절 적용 (2026-09-27)

Plan: 2026-09-27 전체 점검 보고서(ipcs-control·ipcs-material 형식)의 개선안을 "전부 적용". 4절 ⑤·⑥(control·material DB 연계)은 다른 Supabase 프로젝트 키가 필요한 새 외부 접근이라 사용자 확인 후 진행. 운영 DB 쓰기 시험은 dry-run 위주로 하고, 실제 쓰기는 값이 바뀌지 않는 경로로만 확인.

## 1절 (로직, 높음)
- [x] ① 탭별 Export/Print (Support·Valve·Speciality·PID·Marked PID)
- [x] ② 업로드는 값이 있는 칸만 upsert (file_link·빈 칸 덮어쓰기 방지)
- [x] ③ 검색어의 쉼표·괄호·따옴표가 조회를 깨지 않도록 처리
- [x] ④ 헤더 통계를 최신 도면 기준 + Revision 분포 전체 + PDF 연결률
- [x] ⑤ 쓰기 API 보호 — WRITE_PASSWORD 환경변수가 있을 때만 활성 (설정은 사용자)

## 2절 (로직, 중간)
- [x] Sync Links를 바뀐 행만 갱신(전체 NULL 초기화 제거) + 6개 탭 공용 함수
- [x] Revision 없는 파일명은 최신 Revision 행에만 연결
- [x] PID·Valve·Speciality·Marked PID 업로드 id를 기존 행 매칭 + max(id)+1로 통일 (`_apply_upload`)
- [x] `/api/cache/clear` 추가, 보조 스크립트 끝에서 호출
- [x] 신규 도면 issued_date 비어 있으면 등록일(오늘)
- [x] 필터 목록 캐시: 링크 동기화 때는 유지, 시작 시 백그라운드 선계산
- [x] pandas 제거 → openpyxl/xlsxwriter

## 3절 (메뉴·화면)
- [x] Support Revision 필터 (실제 값에서 추출)
- [x] Support Type 필터가 괄호 없는 `GS-1…`도 포함
- [x] Export에 Remark·PDF Link 칸
- [x] UI 문구 영어 통일 (토스트·업로드·Print·Sync 메시지)

## 4절 (범위 확장)
- [x] ① Revision 이력 보기 (ISO·Support)
- [x] ② Data Health 화면 (카드 + Excel)
- [x] ③ 업로드 미리보기 (dry-run: 신규·개정·변경·변경없음)
- [x] ④ 발행 대장 Excel (기간별 신규·개정)
- [ ] ⑤ ipcs-control 연계 — 사용자 확인 필요 (새 DB 접근)
- [ ] ⑥ ipcs-material 연계 — 사용자 확인 필요 (새 DB 접근)
- [x] ⑦ 수정 이력(updated_at/updated_by) — 칸이 있을 때만 기록, SQL 전달 (SQL 실행은 사용자)

## 마무리
- [x] 로컬 서버 + 브라우저로 전 탭 확인, 콘솔 오류 0
- [x] semantic commit (push는 finish 때 — 아직 안 함)
