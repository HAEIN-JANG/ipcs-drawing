-- dwg_iso에 remark 컬럼 추가 + dwg_latest 뷰 갱신 (SELECT * 뷰는 컬럼 추가 시 재생성 필요)
ALTER TABLE drawing.dwg_iso ADD COLUMN IF NOT EXISTS remark text;

CREATE OR REPLACE VIEW drawing.dwg_latest AS
SELECT DISTINCT ON (drawing_no) *
FROM drawing.dwg_iso
ORDER BY drawing_no, revision DESC;

-- support_master에 remark 컬럼 추가 + support_latest 뷰 갱신
-- clamp_height가 나중에 추가되어 테이블 실제 컬럼 순서가 뷰와 달라졌으므로
-- CREATE OR REPLACE 대신 DROP 후 재생성 (뷰 재생성 시 권한 재부여 필요)
ALTER TABLE drawing.support_master ADD COLUMN IF NOT EXISTS remark text;

DROP VIEW IF EXISTS drawing.support_latest;

CREATE VIEW drawing.support_latest AS
SELECT DISTINCT ON (support_drawing) *
FROM drawing.support_master
ORDER BY support_drawing, revision DESC;

GRANT SELECT ON drawing.support_latest TO anon, authenticated;
