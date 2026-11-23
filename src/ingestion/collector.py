import argparse
import csv
from datetime import datetime, timezone
import logging
import os
from pathlib import Path
import tempfile
import time

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

# ================= 兼容 Windows/Linux 的文件锁 =================
try:
    import fcntl
    def lock_file(file):
        fcntl.flock(file, fcntl.LOCK_EX)
except ImportError:
    import msvcrt
    def lock_file(file):
        msvcrt.locking(file.fileno(), msvcrt.LK_LOCK, 1)
# ===============================================================

FIELDS = ['post_id', 'title', 'text', 'source', 'created_at', 'collected_at', 'url']
DEFAULT_OUTPUT = Path(__file__).resolve().parents[2] / 'data/raw/raw_data.csv'
BASE_URL = 'https://hacker-news.firebaseio.com/v0'
LOG = logging.getLogger(__name__)


def _session():
    session = requests.Session()
    retry = Retry(total=3, backoff_factor=1, status_forcelist=[429, 500, 502, 503, 504], allowed_methods=['GET'])
    session.mount('https://', HTTPAdapter(max_retries=retry))
    return session


def _get(session, endpoint):
    response = session.get(f'{BASE_URL}/{endpoint}.json', timeout=(5, 20))
    response.raise_for_status()
    return response.json()


def fetch_new_posts(seen_ids=None, limit=500, *, session=None):
    """Return raw dictionaries; skip known IDs. No persistence or state mutation.

    Item failures are logged and retried on the next poll while still in newstories.
    Feed failures propagate to the caller.
    """
    if not 1 <= limit <= 500:
        raise ValueError('limit must be between 1 and 500')
    seen = {str(i) for i in (seen_ids or ())}
    owned = session is None
    session = session or _session()
    try:
        ids = _get(session, 'newstories')
        if not isinstance(ids, list) or any(type(i) is not int or i <= 0 for i in ids):
            raise ValueError('Invalid newstories response')
        posts = []
        for post_id in ids[:limit]:
            if str(post_id) in seen:
                continue
            try:
                item = _get(session, f'item/{post_id}')
                if not isinstance(item, dict) or item.get('deleted') or item.get('dead') or item.get('type') != 'story':
                    continue
                if item.get('id') != post_id or not isinstance(item.get('title'), str) or not item['title'].strip():
                    raise ValueError('Missing/mismatched id or title')
                stamp = item.get('time')
                if type(stamp) not in (int, float) or stamp < 0:
                    raise ValueError('Invalid creation time')
                created = datetime.fromtimestamp(stamp, timezone.utc).isoformat()
                text = item.get('text') or ''
                url = item.get('url') or f'https://news.ycombinator.com/item?id={post_id}'
                if not isinstance(text, str) or not isinstance(url, str):
                    raise ValueError('Invalid text or URL')
                posts.append(dict(zip(FIELDS, [str(post_id), item['title'], text, 'hackernews', created, datetime.now(timezone.utc).isoformat(), url])))
                seen.add(str(post_id))
            except (requests.RequestException, ValueError, OverflowError, OSError) as exc:
                LOG.warning('Skipping item %s: %s', post_id, exc)
        return posts
    finally:
        if owned:
            session.close()


def collect_data(output_path=DEFAULT_OUTPUT, limit=500, *, session=None):
    """Atomically save new rows; return only the newly committed rows.

    A sidecar advisory lock serializes cooperating collectors, including reads.
    A failed write never marks a post collected. Existing invalid CSV is rejected.
    """
    path = Path(output_path).resolve()
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(str(path) + '.lock', 'a') as lock:
        lock_file(lock)  # <--- 这里换成了兼容函数
        rows = []
        if path.exists():
            with path.open(encoding='utf-8', newline='') as handle:
                reader = csv.DictReader(handle)
                if reader.fieldnames != FIELDS:
                    raise ValueError('Existing CSV schema does not match raw schema')
                rows = list(reader)
                ids = [r['post_id'] for r in rows]
                if len(set(ids)) != len(ids) or any(not r['post_id'] or None in r or any(v is None for v in r.values()) for r in rows):
                    raise ValueError('Existing CSV contains malformed or duplicate rows')
        new = fetch_new_posts({r['post_id'] for r in rows}, limit, session=session)
        if new or not path.exists():
            temporary = None
            try:
                with tempfile.NamedTemporaryFile(mode='w', encoding='utf-8', newline='', dir=path.parent, delete=False) as handle:
                    temporary = handle.name
                    writer = csv.DictWriter(handle, fieldnames=FIELDS)
                    writer.writeheader()
                    writer.writerows(rows + new)
                    handle.flush()
                    os.fsync(handle.fileno())
                os.replace(temporary, path)
            finally:
                if temporary and os.path.exists(temporary):
                    os.unlink(temporary)
        LOG.info('Saved %d new posts to %s', len(new), path)
        return new


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument('--limit', type=int, default=500)
    parser.add_argument('--watch', action='store_true')
    parser.add_argument('--interval', type=float, default=300)
    args = parser.parse_args()
    if not 1 <= args.limit <= 500 or not 0 < args.interval < float('inf'):
        parser.error('limit must be 1..500; interval must be positive and finite')
    logging.basicConfig(level=logging.INFO, format='%(asctime)s %(levelname)s %(message)s')
    try:
        while True:
            try:
                collect_data(args.output, args.limit)
            except (requests.RequestException, ValueError, OSError):
                LOG.exception('Collection failed')
                if not args.watch:
                    return 1
            if not args.watch:
                return 0
            time.sleep(args.interval)
    except KeyboardInterrupt:
        return 0


if __name__ == '__main__':
    raise SystemExit(main())