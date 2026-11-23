-- 原始层：直接存 1 号抓下来的数据
CREATE TABLE IF NOT EXISTS raw_posts (
    post_id      TEXT PRIMARY KEY,
    title        TEXT NOT NULL,
    text         TEXT,
    source       TEXT NOT NULL,
    created_at   TEXT NOT NULL,
    collected_at TEXT NOT NULL,
    url          TEXT NOT NULL
);

-- 清洗层：给 3号/4号 用
CREATE TABLE IF NOT EXISTS clean_posts (
    post_id      TEXT PRIMARY KEY REFERENCES raw_posts(post_id),
    title_clean  TEXT NOT NULL,
    text_clean   TEXT,
    tokens       TEXT,  -- 存 JSON 数组字符串
    created_at   TEXT NOT NULL,
    inserted_at  TEXT NOT NULL
);

-- 建个时间索引，方便 3号 查最近 24 小时的数据
CREATE INDEX IF NOT EXISTS idx_clean_created ON clean_posts(created_at);