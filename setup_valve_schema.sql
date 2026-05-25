-- valve_master 테이블 생성 및 권한 부여
-- Supabase SQL Editor에서 실행

CREATE TABLE IF NOT EXISTS drawing.valve_master (
    id          bigint,
    drawing_no  text         NOT NULL,
    valve       text,
    size        text,
    title       text,
    vendor      text,
    body        text,
    class       text,
    connection  text,
    revision    text,
    issued_date text,
    file_link   text         DEFAULT '',
    CONSTRAINT valve_master_pkey PRIMARY KEY (id),
    CONSTRAINT valve_master_drawing_no_revision_key UNIQUE (drawing_no, revision)
);

GRANT SELECT, INSERT, UPDATE, DELETE ON drawing.valve_master TO anon, authenticated;

SELECT 'valve_master created' AS status;
