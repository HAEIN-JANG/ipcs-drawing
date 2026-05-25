-- support_master 테이블에 누락된 컬럼을 추가합니다.
-- Supabase SQL Editor에서 실행해 주세요.

ALTER TABLE construction.support_master ADD COLUMN IF NOT EXISTS type text;
ALTER TABLE construction.support_master ADD COLUMN IF NOT EXISTS issued_date text;
ALTER TABLE construction.support_master ADD COLUMN IF NOT EXISTS l1 text;
ALTER TABLE construction.support_master ADD COLUMN IF NOT EXISTS l2 text;
ALTER TABLE construction.support_master ADD COLUMN IF NOT EXISTS l3 text;
ALTER TABLE construction.support_master ADD COLUMN IF NOT EXISTS l4 text;
ALTER TABLE construction.support_master ADD COLUMN IF NOT EXISTS file_link text;

-- 중복 방지 및 검색 최적화를 위해 유니크 인덱스를 추가합니다.
CREATE UNIQUE INDEX IF NOT EXISTS idx_support_master_drawing_rev_unique ON construction.support_master (support_drawing, revision);
