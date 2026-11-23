import re
import html
import sys
from pathlib import Path

# Member 5 (PM) integration: use the group-wide stop list so cleaning and
# Member 3's word counting agree. Inline fallback keeps this module standalone.
_ROOT = Path(__file__).resolve().parents[2]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))
try:
    from text_quality import STOP_WORDS, is_content_word  # type: ignore
except ImportError:                                      # pragma: no cover
    STOP_WORDS = set("a an the and or in on at to for of is are was were be been being it this that".split())
    def is_content_word(w):
        return isinstance(w, str) and len(w.strip()) > 1 and w.strip().lower() not in STOP_WORDS

URL_RE = re.compile(r'https?://\S+')
TAG_RE = re.compile(r'<[^>]+>')
SPACE_RE = re.compile(r'\s+')
WORD_RE = re.compile(r"[a-zA-Z]+|[\u4e00-\u9fff]")


def clean_text(raw: str) -> str:
    if not raw:
        return ""
    text = TAG_RE.sub(' ', raw)
    text = html.unescape(text)
    text = URL_RE.sub(' ', text)
    return SPACE_RE.sub(' ', text).strip()


# 就是下面这行，把 list[str] 改成 list
def tokenize(text: str) -> list:
    words = WORD_RE.findall(text.lower())
    return [w for w in words if is_content_word(w)]


def process_row(raw: dict) -> dict:
    t_clean = clean_text(raw.get('title', ''))
    c_clean = clean_text(raw.get('text', ''))
    tokens = tokenize(t_clean + " " + c_clean)

    return {
        'title_clean': t_clean,
        'text_clean': c_clean,
        'tokens': tokens
    }