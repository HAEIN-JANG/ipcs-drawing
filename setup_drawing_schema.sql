-- ============================================================
-- IPCS Drawing Control System - New Supabase Schema Setup
-- 새 프로젝트(hijang0909 / wsvqeoufppcoeclbfbgz)에서 실행
-- Supabase SQL Editor에 붙여넣고 실행하세요.
-- ============================================================

-- 1. drawing 스키마 생성
CREATE SCHEMA IF NOT EXISTS drawing;

-- 2. dwg_iso 테이블 생성 (ISO 도면 전체 revision)
CREATE TABLE IF NOT EXISTS drawing.dwg_iso (
    id            bigint,
    drawing_no    text         NOT NULL,
    line_no       text,
    system        text,
    area          text,
    bore          text,
    title         text,
    revision      text,
    issued_date   text,
    file_link     text,
    CONSTRAINT dwg_iso_pkey PRIMARY KEY (id),
    CONSTRAINT dwg_iso_drawing_no_revision_key UNIQUE (drawing_no, revision)
);

-- 3. support_master 테이블 생성 (지지대 도면 전체 revision)
CREATE TABLE IF NOT EXISTS drawing.support_master (
    id              bigint,
    system          text,
    support_drawing text         NOT NULL,
    type            text,
    iso_drawing     text,
    line_no         text,
    l1              text,
    l2              text,
    l3              text,
    l4              text,
    revision        text,
    issued_date     text,
    file_link       text,
    CONSTRAINT support_master_pkey PRIMARY KEY (id),
    CONSTRAINT support_master_support_drawing_revision_key UNIQUE (support_drawing, revision)
);

-- 4. dwg_latest VIEW (drawing_no별 최신 revision)
CREATE OR REPLACE VIEW drawing.dwg_latest AS
SELECT DISTINCT ON (drawing_no) *
FROM drawing.dwg_iso
ORDER BY drawing_no, revision DESC;

-- 5. support_latest VIEW (support_drawing별 최신 revision)
CREATE OR REPLACE VIEW drawing.support_latest AS
SELECT DISTINCT ON (support_drawing) *
FROM drawing.support_master
ORDER BY support_drawing, revision DESC;

-- 6. anon 역할에 권한 부여
GRANT USAGE ON SCHEMA drawing TO anon, authenticated;
GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA drawing TO anon, authenticated;
GRANT SELECT ON ALL SEQUENCES IN SCHEMA drawing TO anon, authenticated;

-- 완료 확인
SELECT 'drawing schema setup complete' AS status;
