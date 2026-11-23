import sqlite3
import json
import os
from pathlib import Path
from datetime import datetime, timedelta, timezone

# Use one canonical database for the project pipeline.
DB_PATH = Path(os.environ.get('APP_DB')
               or (Path(__file__).resolve().parents[2] / 'data' / 'app.db'))
SCHEMA_PATH = Path(__file__).parent / 'schema.sql'


def get_conn():
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row

    if SCHEMA_PATH.exists():
        with SCHEMA_PATH.open('r', encoding='utf-8') as f:
            conn.executescript(f.read())
    return conn


# 就是下面这行，把 list[dict] 改成了 list
def save_posts(raw_rows: list, clean_results: list):
    if not raw_rows:
        return

    conn = get_conn()
    now_str = datetime.now(timezone.utc).isoformat()

    try:
        with conn:
            for raw, clean in zip(raw_rows, clean_results):
                conn.execute('''
                    INSERT OR IGNORE INTO raw_posts 
                    (post_id, title, text, source, created_at, collected_at, url)
                    VALUES (?, ?, ?, ?, ?, ?, ?)
                ''', (
                    raw['post_id'], raw['title'], raw['text'], raw['source'],
                    raw['created_at'], raw['collected_at'], raw['url']
                ))

                conn.execute('''
                    INSERT OR IGNORE INTO clean_posts
                    (post_id, title_clean, text_clean, tokens, created_at, inserted_at)
                    VALUES (?, ?, ?, ?, ?, ?)
                ''', (
                    raw['post_id'], clean['title_clean'], clean['text_clean'],
                    json.dumps(clean['tokens'], ensure_ascii=False),
                    raw['created_at'], now_str
                ))
    finally:
        conn.close()

def get_recent_posts(hours=24):
    """
    用于趋势检测：获取最近 N 小时的帖子，用于词频统计。
    返回：list[dict]，包含 post_id, title_clean, tokens, created_at
    注意：tokens 已经自动帮你从 JSON 字符串转成了 Python 列表。

    Data clock fix: anchor "now" on MAX(clean_posts.created_at), NOT on
    datetime('now'). The delivered DB is a static snapshot whose newest post
    is older than the machine clock, so the original wall-clock query
    returned ZERO rows. The project uses MAX(created_at) as its baseline.
    """
    conn = get_conn()
    try:
        row = conn.execute('SELECT MAX(created_at) FROM clean_posts').fetchone()
        latest = row[0] if row else None
        if not latest:
            return []
        now = datetime.fromisoformat(latest)
        if now.tzinfo is None:
            now = now.replace(tzinfo=timezone.utc)
        cutoff = (now.astimezone(timezone.utc) - timedelta(hours=hours)).strftime(
            '%Y-%m-%dT%H:%M:%S+00:00')
        # ISO 8601 UTC is fixed-width, so string comparison == time comparison
        # and idx_clean_created stays usable.
        cursor = conn.execute(
            '''SELECT post_id, title_clean, tokens, created_at
               FROM clean_posts WHERE created_at >= ? ORDER BY created_at''',
            (cutoff,))
        rows = []
        for r in cursor.fetchall():
            data = dict(r)
            if data['tokens']:
                try:
                    data['tokens'] = json.loads(data['tokens'])
                except (TypeError, ValueError):
                    data['tokens'] = []
            rows.append(data)
        return rows
    finally:
        conn.close()

def get_posts_with_text(limit=100):
    """
    给 4 号（情感分析）用：获取带有正文的帖子原文，用于 VADER 或 LLM 打分。
    返回：list[dict]，包含 post_id, title_clean, text_clean, created_at
    """
    conn = get_conn()
    query = '''
        SELECT post_id, title_clean, text_clean, created_at 
        FROM clean_posts 
        WHERE text_clean IS NOT NULL AND text_clean != ''
        ORDER BY created_at DESC LIMIT ?
    '''
    cursor = conn.execute(query, (limit,))
    rows = [dict(row) for row in cursor.fetchall()]
    conn.close()
    return rows