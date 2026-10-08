# Context Notes — 전체 코드 점검 개선 1~4절 적용 (2026-09-27)

- 사용자 지시: 점검 보고서(ipcs-control·ipcs-material 형식) "전부 적용". UI 언어는 영어로 통일(한국어 토스트·업로드·Print·Sync 문구 교체).
- 4절 ⑤ ipcs-control 연계·⑥ ipcs-material 연계는 **보류(사용자 확인 대기)**. control(ognhvfvlboqblueuldlm)·material은 별도 Supabase 프로젝트라 drawing 앱에 그 키를 새로 넣어야 하는 "외부 서비스 새 접근"이다.
- ⑤ 쓰기 보호는 `WRITE_PASSWORD` 환경변수가 있을 때만 켜진다(없으면 지금과 같음). 모든 POST에 `X-Write-Password` 헤더가 필요하고, 화면은 401이 나면 한 번 묻고 sessionStorage에 기억한다. **Render 환경변수 설정은 사용자 몫**이며, 설정 전까지 접근 제어는 바뀌지 않는다.

## 구조
- `CATS`(app.py)에 6개 도면 종류의 테이블/VIEW/키/검색 칸/필터/Excel 칸을 모았다. 목록·Export·Print·통계·업로드·Sync·이력·발행 대장이 모두 `/api/<cat>/...` 공용 라우트를 쓴다. 예전 ISO 경로(`/api/drawings`, `/api/export`, `/api/print`, `/api/stats`, `/api/upload`, `/api/drawings/sync-links`)도 그대로 받는다(`redirect_defaults=False`, `_cat("drawings")→iso`).
- Revision을 고르면 최신 VIEW 대신 이력 테이블에서 찾는다. ISO는 예전처럼 `status` 파라미터, Support는 `revision` 파라미터를 쓴다.

## 결정·근거 (실측)
- 헤더 통계: 예전에는 dwg_iso(이력 포함)를 셌다(TOTAL 6,249 / C01 269 / C01A 3,629). 목록과 같은 dwg_latest 기준으로 바꿨다(4,026, C01 196 / C01A 1,665 / C01B 1,767 / C01C 86 / C03 259 / VOID 53). PDF 연결률은 VOID를 뺀 기준이다(ISO 100%, Support 5.2% = 1,102/21,204).
- 검색어: `\,` 이스케이프는 PostgREST에서 먹히지 않아 쉼표가 들어가면 `PGRST100` 500 오류가 났다. `_q()`로 값을 큰따옴표로 감싸고 `\`·`"`를 이스케이프해 쉼표·괄호·따옴표 모두 통과한다.
- 업로드: 값이 있는 칸만 보내고, 칸 구성이 같은 행끼리 묶어 upsert한다(`_upsert_partial`, ipcs-control과 같은 이유 — postgrest가 키 합집합으로 빠진 칸을 NULL로 덮음). Support 업로드가 매번 `file_link`를 NULL로 지우던 문제도 없어졌다. 대신 **엑셀로 값을 지우는 것은 불가**하다(빈 칸 = 유지).
- 업로드는 먼저 dry-run(`?dry_run=1`)으로 신규/개정/변경/변경없음을 보여주고, Confirm 때 신규·변경 행만 쓴다. id는 전 종류 모두 기존 행 매칭 + max(id)+1이고, upsert는 `on_conflict=id`다. 6개 테이블 모두 같은 값으로 1행 upsert를 시험해 허용·무변경을 확인했다. 예전 PID/Valve/Speciality의 엑셀 `No`→id 방식은 버렸다.
- 신규 도면 issued_date가 비어 있으면 등록일(오늘)로 넣는다(메모리 규칙).
- pandas 제거: 업로드는 openpyxl(read_only), Export는 xlsxwriter(`strings_to_formulas=False`). openpyxl은 정수를 `350`으로 읽어 DB 기존 값 형식(`'350'`)과 같다(pandas는 `350.0`이 될 수 있었음). `.xls`는 원래도 xlrd가 없어 안 됐으므로 `.xlsx`만 받는다. 로컬 전용 `fill_missing_iso_from_report.py`는 여전히 pandas를 쓴다(Render 불필요).
- Sync Links: 전체 NULL 초기화를 없애고, 값이 바뀌는 행만 보낸다. Cloudinary가 0건을 돌려주면 아무것도 바꾸지 않고 오류를 낸다. Revision 없는 파일명(`{dwg}`)은 최신 Revision이면서 VOID가 아닌 행에만 연결한다. dry-run 결과로 ISO 49건·Marked PID 6건이 "updated"로 나왔는데, 모두 Cloudinary에 파일이 다시 올라가 버전 경로(`v178…`→`v178…`)만 바뀐 것이다. 예전 Sync도 같은 결과를 냈을 정상 동작이다.
- 캐시: 링크 동기화는 필터 목록 캐시를 비우지 않는다(`filters=False`). 시작 시 백그라운드로 ISO 필터·통계·Support 필터를 미리 계산한다. `POST /api/cache/clear`를 추가했고, 보조 스크립트 3개는 DB 반영 뒤 `cache_notify.clear_app_cache()`로 운영 앱 캐시를 비운다(`IPCS_DRAWING_URL`, `WRITE_PASSWORD` 환경변수 사용).
- Support Type 필터: `GS`는 `(GS-%` 또는 `GS-%`로 찾는다. 여는 괄호가 빠진 값이 11건 있다(`GS-183)` 등).
- Data Health(`/api/health`, 5분 캐시, 약 5초): ISO PDF 없음 0 / VOID인데 PDF 0 / 뒤 Revision 발행일이 앞보다 빠름 4(BWF-404-1 계열 4건, C01 2026-05-15 → C01A 2025-12-30) / Support→없는 ISO 34 / Support→VOID ISO 14 / Support Type 형식 21(SPEICAL 4, SPECAIL 3, DETAIL 3, 괄호 누락 11) / 미등록 System 0(Support `ALL`은 공통 도면 P02라 제외) / P&ID 등 PDF 없음 4(Marked PID) / Support PDF 없음 20,102. 데이터 자체는 고치지 않았다.
- 발행 대장(`/api/<cat>/issue-register?from=&to=`, ISO·Support): 기간 안의 행을 New(이전 Rev 없음)/Revised/VOID로 나누고 이전 Rev를 같이 적는다. 2026-09 ISO 153건 전부 Revised.
- 수정 이력(4절 ⑦): 테이블에 `updated_at`/`updated_by` 칸이 있을 때만 기록한다(`_audit`, 칸 유무는 테이블별 1회 확인). 공용 계정이라 사람 이름은 남지 않고 `web upload`/`web sync-links`로 남는다. 칸 추가는 사용자가 SQL Editor에서 실행해야 한다 — 아래 SQL. 실행 후 앱 재시작(재배포) 전까지는 이전 판정(칸 없음)이 유지된다.

```sql
ALTER TABLE drawing.dwg_iso            ADD COLUMN IF NOT EXISTS updated_at timestamptz, ADD COLUMN IF NOT EXISTS updated_by text;
ALTER TABLE drawing.support_master     ADD COLUMN IF NOT EXISTS updated_at timestamptz, ADD COLUMN IF NOT EXISTS updated_by text;
ALTER TABLE drawing.pid_master         ADD COLUMN IF NOT EXISTS updated_at timestamptz, ADD COLUMN IF NOT EXISTS updated_by text;
ALTER TABLE drawing.valve_master       ADD COLUMN IF NOT EXISTS updated_at timestamptz, ADD COLUMN IF NOT EXISTS updated_by text;
ALTER TABLE drawing.speciality_master  ADD COLUMN IF NOT EXISTS updated_at timestamptz, ADD COLUMN IF NOT EXISTS updated_by text;
ALTER TABLE drawing.marked_pid_master  ADD COLUMN IF NOT EXISTS updated_at timestamptz, ADD COLUMN IF NOT EXISTS updated_by text;
```

## 검증
- Flask test client: 6개 종류 목록·통계·Export(머리글·행 수)·Print, 옛 ISO 경로, 특수문자 검색, Support Revision/Type 필터, 이력, Data Health·Export, 발행 대장.
- 업로드 dry-run: ISO(변경 1·개정 1·신규 1·변경없음 49로 정확히 분류), Support 50·PID 24·Marked PID 23건은 전부 변경없음. Sync dry-run 6종 모두 200. 쓰기 보호(비밀번호 없음/틀림 401, 맞음 200, GET 영향 없음).
- 브라우저(로컬 5100): 6개 탭 + Data Health 전환, Revision 이력 창, 업로드 미리보기(Confirm은 누르지 않음), 발행 대장 모달. 콘솔 오류·경고 0(favicon 404는 `data:` 아이콘으로 제거).
- 운영 DB에 실제로 쓴 것: 6개 테이블 각 1행에 기존과 같은 값으로 upsert(무변경 확인). 그 밖의 쓰기는 없다.

---

# Context Notes — 해상도 대응 (2026-09-27, ipcs-control 101ac7a와 같은 방식)

- 원인: 칸마다 큰 min-width(ISO 합계 약 1,520px, PID DWG 420px 등)를 둬서 1366px 화면에서 제목·REV·날짜가 가로 스크롤 밖으로 밀렸다.
- 실측(가로 스크롤 px, 수정 전 → 후): 1366px ISO 458→0, Support 274→0, Valve 116→0, PID 125→0. 1024px에서는 전 탭이 넘쳤으나(ISO 801, Support 617 등) 모두 0이 됐다. 1920px에서는 ISO 행 높이가 54→40으로 줄었다(제목이 한 줄에 들어감).
- 칸 폭: 번호·날짜·REV처럼 짧은 칸은 좁게 고정하고, 도면·라인 번호는 한 줄로 유지한다. 제목·비고·설명은 남는 폭에서 줄을 바꾸고, 머리글은 단어 단위로 줄바꿈한다(`word-break: keep-all`).
- 단계별 조정
  - 1600px 이하: 여백·셀 패딩 축소.
  - 1440px 이하: 사이드바를 200px로 줄이고, 보조 버튼(Export/Print/Issue Register)은 아이콘만 남긴다(`title` 속성으로 이름 표시).
  - 1280px 이하: 부제를 숨기고, Support LINE NO를 하이픈에서 줄바꿈한다.
  - 1100px 이하: 사이드바를 아이콘만 남긴 60px로 줄이고 Revision 칩을 숨긴다. Support ISO 번호도 줄바꿈한다.
- 헤더: Support의 Revision 칩 9개가 여러 줄이 되면 고정 높이(56px) 밖으로 넘쳐 TOTAL 숫자가 잘렸다. 헤더 높이를 `min-height`로 바꿔 내용에 맞춰 늘어나게 했다.
- 남은 것: 1024~1280px에서 Support 공통 도면(System ALL) 8행은 ISO DRAWING 칸의 긴 설명 때문에 행이 여러 줄로 높아진다(가로 넘침은 없음).
- 균등 배분(2026-09-28, 사용자 지적 "한쪽으로 치우침"): 짧은 칸만 px로 고정하자, 폭이 정해지지 않은 제목 칸 하나가 남는 폭을 전부 가져갔다(Valve에서 TITLE이 화면 절반, REV·DATE는 오른쪽 끝에 몰림). 모든 칸에 비율(%)을 주었다. 예) Valve·PID = NO 6 / ITEM·SYSTEM 16 / DWG 24 / TITLE 30 / REV 12 / DATE 12. 한 줄 유지 칸은 좁은 화면에서 비율보다 넓어질 수 있어, 1920/1366/1024px 모두 가로 넘침 0을 유지한다.
- Data Health 삭제(2026-09-28, 사용자 지시 "quality section은 삭제"): 사이드바 QUALITY 메뉴, 화면 코드, `/api/health`·`/api/health/export`를 모두 제거했다. 위 Data Health 실측 수치(Support→없는 ISO 34건 등)는 기록으로만 남긴다.
- Issue Register(발행 대장) 삭제(2026-09-28, 사용자 지시 "iso drawing에 issue register도 삭제"): ISO·Support가 같은 버튼·서버 코드를 썼으므로 기능 전체(툴바 버튼, 기간 모달, `/api/<cat>/issue-register`)를 제거했다.

---

# Context Notes — 최적화 6단계 (2026-09-28)

- 1) 임시 코드: 추적 파일에 debug print·TODO 없음(남은 print는 의도한 로그). gitignore된 `.playwright-mcp` 시험 파일 32개 삭제.
- 2) 코드 정리: 쓰지 않는 CSS 18규칙 제거(group-* 옛 그룹 헤더, pdf-btn, btn-danger/success, drop-zone-sub, hstat 색 3종). ISO STATUS 목록을 고정값 대신 DB 실제 값(`_safe_distinct("dwg_iso","revision")`)에서 추출 — 현재 값은 같음(C01/C01A/C01B/C01C/C03/VOID). `traceback` import를 상단으로 옮김.
- 3) 기능: 6개 종류 × 목록/통계/Export/Print + 필터 6종 + init + 특수문자 검색 + 이력 + 옛 ISO 경로 + 업로드 dry-run + Sync dry-run 6종을 호출, 실패 0.
- 4) 성능: 캐시 적중 0.00s, 캐시가 빈 첫 호출도 대부분 1초 이하(Support 통계 2.9s, Support Export 21,204행 5.3s). HTML·JSON gzip 확인. Sync는 Cloudinary 목록 조회가 대부분(ISO 39.8s, gunicorn timeout 120s 이내).
- 5) 브라우저에서 찾은 버그: 서버 재시작 직후 Support 탭에 PDF 연결률이 ISO 값(100%)으로 남아 보였다. 탭을 바꿀 때 TOTAL만 비우고 PDF·Revision 칩은 비우지 않았고, Support 통계 첫 계산에 약 3초가 걸렸기 때문이다. 탭 전환 시 모두 비우고, 서버 시작 시 Support 통계도 미리 계산하도록 했다. 수정 후 즉시 `—` → 5.2%, 콘솔 경고 0.
- 운영 장애(2026-09-28, push 87facc5 직후): 모든 API가 500을 냈다(`partially initialized module 'httpcore' ... has no attribute 'ConnectionPool'`). Render는 requirements에 버전 고정이 없어 httpx 0.28.1을 설치하는데, 0.28은 첫 연결을 만들 때 httpcore를 import한다. 이번에 추가한 시작 시 선계산 스레드와 첫 요청이 동시에 첫 연결을 만들면서 반쯤 초기화된 모듈이 남은 것으로 판단했다. 수정: `get_client()`에 잠금을 걸고, 모듈 로드 때 메인 스레드에서 첫 조회를 끝낸 뒤 선계산 스레드를 시작한다. Render와 같은 버전의 가상환경(httpx 0.28.1)에서 동시 요청 18건×3회 모두 성공했다. 수정 전 코드는 로컬에서 재현되지 않았다(타이밍 문제).
- 1차 수정(a20f1a2) 배포 뒤에는 응답이 아예 없었다(모든 경로 90초 타임아웃). 모듈 로드 중 첫 조회·스레드 시작이 gunicorn 기동을 막은 것으로 보인다(Render 로그는 확인하지 못함). 2차 수정: 시작 시 선계산 스레드와 첫 조회를 모두 없애 예전 방식(첫 요청 때 캐시 채움)으로 되돌리고, 로드 시점에 `importlib.import_module("httpcore")`만 해 동시 첫 연결 경합을 없앴다. 클라이언트 생성 잠금은 유지. 대가로 서버 재시작 직후 첫 접속의 필터·통계 계산이 1~3초 더 걸린다.

## Test Package 탭 (2026-10-08)
- Marked PID 탭을 그대로 복사했다. CATS `testpackage`, 테이블 `drawing.test_package_master`(marked_pid_master와 `LIKE … INCLUDING ALL`로 같은 구조, RLS 켬, service_role GRANT). 생성 SQL은 `scripts_tmp/test_package_master.sql`이고 사용자가 SQL Editor에서 실행한다.
- 업로드 Excel 머리글은 `NO. / SYSTEM / TEST PACKAGE / DESCRIPTION / DATE`.
- PDF 파일명이 도면번호가 아니라 계통명(`Potable_Water_System` 등)이다. Sync Links가 링크를 지우지 않도록 testpackage만 지금 링크의 파일명을 후보에 넣는다(`_link_candidates`).
- 도면번호는 Marked PID처럼 `CCGT-TP-BOP-{SYS}-XXX`로 정했다. PDF 안 번호는 PW-001, RW-001~003(한 파일에 3개), CCW-001이다. 설명은 `{계통명} System Pressure Test Package`, 발행일은 등록일(2026-10-08).
- 사용자 지시로 탭 이름을 Test Package PID로 바꾸고 번호의 XXX를 001로 바꿨다(PW-001·RW-001·CCW-001). 앞으로 업로드되는 번호도 끝이 `-XXX`면 `-001`로 바꿔 등록한다(`_parse_upload`). 업로드 머리글은 `TEST PACKAGE PID`. Marked PID와 번호가 겹치지 않게 되었지만, 파일명이 계통명이라 Sync Links는 지금 링크의 파일명을 계속 먼저 찾는다.
