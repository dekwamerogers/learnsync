"""
CSV parsing helpers for the self-paced ingestion pipeline.

The self-paced CSV is assignment-level: one row per learner per assignment.
Learner-level fields (name, country, payment status, etc.) repeat on every row.
"""

import csv
import logging
import re
import unicodedata
from datetime import date, datetime
from io import StringIO

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Required columns — validation fails if any of these are absent
# ---------------------------------------------------------------------------

REQUIRED_COLUMNS = [
    'Email',
    'First name',
    'Last name',
    'eHub class name',
    'Course name',
    'Course sequence number',
    'Assignment name',
    'Assignment type',
    'Is assignment accessed',
    'Is assignment submitted',
    'Is assignment passed',
    'Payment status',
]

# ---------------------------------------------------------------------------
# Canonical column name map: internal key → expected CSV header
# ---------------------------------------------------------------------------

COLUMN_MAP = {
    # Learner identity
    'email':                    'Email',
    'first_name':               'First name',
    'last_name':                'Last name',
    'gender':                   'Gender',
    'country':                  'Country of residence',
    'region':                   ('Region', 'Regions'),
    # Platform access — tuple = fallback aliases tried left-to-right
    'ehub_profile_url':         ('eHub profile URL', 'eHubb profile', 'eHub profile'),
    'lms_profile_url':          ('LMS profile URL', 'LMS profile'),
    'has_logged_into_ehub':     'Has logged into eHub',
    'has_logged_into_lms':      'Has logged into LMS',
    'has_shown_up_in_course':   'Has shown up in course',
    # Cross-enrolment
    'other_programmes_count':   ('Count of other programmes enrolled', 'No. of other programs enrolled'),
    'other_programme_names':    ('Other programmes enrolled', 'Other programs enrolled'),
    # Enrolment & activation
    'is_enrolment_activated':   ('Is enrolment activated', 'Is enrollment activated'),
    'activation_date':          'Activation date',
    'days_since_activation':    ('Days since activation', 'Time since activation (days)'),
    'first_sign_of_life_date':  'First sign of life date',
    'days_since_first_sign_of_life': ('Days since first sign of life', 'Time since sign of life (days)'),
    # Course context
    'course_sequence_number':   'Course sequence number',
    'course_name':              'Course name',
    'ehub_class_name':          'eHub class name',
    'course_status_on_lms':     ('Course status on LMS', 'Course status (LMS)'),
    'is_course_graduated':      'Is course graduated',
    'course_graduation_date':   ('Course graduation date', 'Course graduation time'),
    # Assignment detail
    'assignment_name':          'Assignment name',
    'assignment_type':          'Assignment type',
    'is_assignment_accessed':   'Is assignment accessed',
    'assignment_accessed_date': 'Assignment accessed date',
    'is_assignment_submitted':  'Is assignment submitted',
    'assignment_submitted_date':'Assignment submitted date',
    'is_assignment_passed':     'Is assignment passed',
    'passed_on_first_attempt':  ('Passed on first attempt', 'is_passed_on_first_attempt'),
    # Programme completion
    'is_programme_graduated':   ('Is programme graduated', 'Is program graduated'),
    'programme_graduation_date':('Programme graduation date', 'Program graduation date'),
    'is_graduated_on_savanna':  ('Is graduated on Savanna', 'Is graduated on savannah'),
    # Health & payment
    'health_classification':    'Learner health classification',
    'payment_status':           'Payment status',
}

NULL_DATE = date(1970, 1, 1)


# ---------------------------------------------------------------------------
# Primitive converters
# ---------------------------------------------------------------------------

# CSV cells that mean "no data" — treated as empty string for text fields.
_NULL_STRINGS = frozenset(['n/a', '#n/a', 'na', 'n.a.', 'none', 'null', 'nil', '-', '--'])

# MySQL latin1 columns only hold U+0000–U+00FF.
# mysql utf8 columns only hold U+0000–U+FFFF (3-byte max).
# We sanitize every string at parse time so no single learner's unusual
# profile text can abort the entire upload.
#
# Strategy:
#   • _str      — strips 4-byte chars (> U+FFFF) from ALL fields
#   • _name     — additionally strips non-letter/mark/space chars (math symbols,
#                 emoji within BMP, box-drawing, etc.) so even latin1 columns work.
#                 Legitimate accented letters (é, ñ, ọ, etc.) are preserved.

_4BYTE_RE = re.compile(r'[\U00010000-\U0010FFFF]', re.UNICODE)

# Unicode categories kept in name fields:
#   L* = letters (Latin, Arabic, CJK, Cyrillic, …)
#   M* = combining marks / diacritics
#   Zs = space separator
#   Pd = dash / hyphen
#   apostrophe / full stop are also common in names
_NAME_SAFE_CATS = frozenset(['Lu', 'Ll', 'Lt', 'Lm', 'Lo', 'Mn', 'Mc', 'Me', 'Zs', 'Pd'])
_NAME_SAFE_CHARS = frozenset("'.'’")   # straight + curly apostrophe, period


def _strip_4byte(s: str) -> str:
    return _4BYTE_RE.sub('', s)


def _sanitize_name(s: str) -> str:
    """Strip non-letter/mark/space chars that break latin1/utf8 columns.

    Keeps all genuine Unicode letters and diacritics (including accented African
    names like Aminé, Ọlá, Kofi).  Removes mathematical script letters (ℴ, 𝒮),
    emoji, box-drawing characters, and other symbols.
    """
    s = _strip_4byte(s)
    # Fast path: plain ASCII letters/spaces/hyphens/apostrophes are always safe
    # and str.isalpha() is far cheaper than unicodedata.category() per char.
    if all(c.isalpha() or c in " -'.’‘" for c in s):
        return s
    return ''.join(
        c for c in s
        if unicodedata.category(c) in _NAME_SAFE_CATS or c in _NAME_SAFE_CHARS
    )


def _str(val) -> str:
    if val is None:
        return ''
    return _strip_4byte(str(val).strip())


def _text(val) -> str:
    """Like _str but converts null-ish placeholders to empty string."""
    s = _str(val)
    return '' if s.lower() in _NULL_STRINGS else s


def _bool(val) -> bool:
    return _str(val).lower() in ('yes', 'true', '1')


def parse_date(val) -> date | None:
    """Return None for blank values and the 1970-01-01 epoch sentinel."""
    if val is None:
        return None
    if isinstance(val, datetime):
        d = val.date()
    elif isinstance(val, date):
        d = val
    else:
        raw = _str(val)
        if not raw:
            return None
        # Fast path: YYYY-MM-DD is the standard eHub export format.
        # date.fromisoformat() is ~10× faster than strptime.
        if len(raw) == 10 and raw[4] == '-' and raw[7] == '-':
            try:
                d = date.fromisoformat(raw)
                return None if d == NULL_DATE else d
            except ValueError:
                pass
        for fmt in (
            '%Y-%m-%d %H:%M:%S', '%Y-%m-%d %H:%M',
            '%Y-%m-%d', '%d/%m/%Y', '%m/%d/%Y',
        ):
            try:
                d = datetime.strptime(raw, fmt).date()
                break
            except ValueError:
                continue
        else:
            return None
    return None if d == NULL_DATE else d


def _int(val, default=0) -> int:
    try:
        return int(float(_str(val)))
    except (TypeError, ValueError):
        return default


_SEQ_NUM_RE = re.compile(r'(\d+)')


def _seq(val) -> int:
    """Parse a course sequence number, handling formats like '1', 'C#1', '2.0'."""
    s = _str(val)
    m = _SEQ_NUM_RE.search(s)
    return int(m.group(1)) if m else 0


def clean_email(raw: str) -> str:
    return _str(raw).lower()


def normalise_payment_status(raw: str) -> str:
    """Map free-text payment status from CSV to a PaymentStatus choice key."""
    v = _str(raw).lower()
    if not v:
        return 'unknown'
    if any(k in v for k in ('compliant', 'up to date', 'paid')):
        return 'compliant'
    if any(k in v for k in ('due soon', 'upcoming')):
        return 'due_soon'
    if 'grace' in v:
        return 'grace_period'
    if any(k in v for k in ('overdue', 'late', 'arrears', 'outstanding')):
        return 'overdue'
    return 'unknown'


def normalise_assignment_type(raw: str) -> str:
    v = _str(raw).lower()
    if v == 'milestone':
        return 'milestone'
    if v == 'test':
        return 'test'
    return 'other'


# ---------------------------------------------------------------------------
# File loading
# ---------------------------------------------------------------------------

def iter_csv(source) -> tuple[list[str], 'Iterator[dict]']:
    """
    Stream CSV rows lazily from bytes or a binary file-like object.

    Returns (headers, row_iterator) where row_iterator yields one raw row dict
    at a time — never materialises the full file as a list.  This keeps peak
    memory proportional to the largest single row rather than the whole file.

    source can be:
      - bytes               — decoded to StringIO
      - a binary file-like  — wrapped with TextIOWrapper (must support seek)
    """
    import io as _io
    if isinstance(source, (bytes, bytearray)):
        text = source.decode('utf-8-sig')
        reader = csv.reader(StringIO(text))
    else:
        source.seek(0)
        wrapper = _io.TextIOWrapper(source, encoding='utf-8-sig', newline='')
        reader = csv.reader(wrapper)

    try:
        raw_header = next(reader)
    except StopIteration:
        return [], iter([])

    headers = [_str(h) for h in raw_header]

    def _row_gen():
        for raw in reader:
            if any(raw):
                padded = raw + [''] * max(0, len(headers) - len(raw))
                yield dict(zip(headers, padded))

    return headers, _row_gen()


def load_csv(content: bytes) -> tuple[list[str], list[dict]]:
    """Parse CSV bytes into (headers, list-of-dicts). Kept for callers that need a full list."""
    headers, row_iter = iter_csv(content)
    return headers, list(row_iter)


# ---------------------------------------------------------------------------
# Validation
# ---------------------------------------------------------------------------

def validate_columns(headers: list[str]) -> list[str]:
    """Return a list of error strings. Empty list means validation passed."""
    errors = []
    for col in REQUIRED_COLUMNS:
        if col not in headers:
            errors.append(f'Missing required column: "{col}"')
    return errors


# ---------------------------------------------------------------------------
# Row accessor
# ---------------------------------------------------------------------------

def get(row: dict, key: str):
    """Retrieve a value from a row using the internal key.
    COLUMN_MAP values may be a string or a tuple of fallback aliases."""
    col = COLUMN_MAP.get(key, key)
    if isinstance(col, tuple):
        for alias in col:
            if alias in row:
                return row[alias]
        return ''
    return row.get(col, '')


def parse_other_programme_names(raw: str) -> list[str]:
    """Split a comma-separated list of programme names, filtering blanks and nulls."""
    if not raw:
        return []
    return [
        name.strip()
        for name in raw.split(',')
        if name.strip() and name.strip().lower() not in _NULL_STRINGS
    ]


def row_to_dict(row: dict) -> dict:
    """
    Convert a raw CSV row dict into a cleaned, typed dict using internal keys.
    All date fields are None-safe. All bool fields are normalised.
    """
    return {
        'email':                    clean_email(get(row, 'email')),
        'first_name':               _sanitize_name(_text(get(row, 'first_name'))),
        'last_name':                _sanitize_name(_text(get(row, 'last_name'))),
        'gender':                   _sanitize_name(_text(get(row, 'gender'))),
        'country':                  _sanitize_name(_text(get(row, 'country'))),
        'region':                   _sanitize_name(_text(get(row, 'region'))),
        'ehub_profile_url':         _str(get(row, 'ehub_profile_url')),
        'lms_profile_url':          _str(get(row, 'lms_profile_url')),
        'has_logged_into_ehub':     _bool(get(row, 'has_logged_into_ehub')),
        'has_logged_into_lms':      _bool(get(row, 'has_logged_into_lms')),
        'has_shown_up_in_course':   _bool(get(row, 'has_shown_up_in_course')),
        'other_programmes_count':   _int(get(row, 'other_programmes_count')),
        'other_programme_names':    _text(get(row, 'other_programme_names')),
        'is_enrolment_activated':   _bool(get(row, 'is_enrolment_activated')),
        'activation_date':          parse_date(get(row, 'activation_date')),
        'first_sign_of_life_date':  parse_date(get(row, 'first_sign_of_life_date')),
        'course_sequence_number':   _seq(get(row, 'course_sequence_number')),
        'course_name':              _text(get(row, 'course_name')),
        'ehub_class_name':          _str(get(row, 'ehub_class_name')),
        'course_status_on_lms':     _text(get(row, 'course_status_on_lms')).lower(),
        'is_course_graduated':      _bool(get(row, 'is_course_graduated')),
        'course_graduation_date':   parse_date(get(row, 'course_graduation_date')),
        'assignment_name':          _text(get(row, 'assignment_name')),
        'assignment_type':          normalise_assignment_type(get(row, 'assignment_type')),
        'is_assignment_accessed':   _bool(get(row, 'is_assignment_accessed')),
        'assignment_accessed_date': parse_date(get(row, 'assignment_accessed_date')),
        'is_assignment_submitted':  _bool(get(row, 'is_assignment_submitted')),
        'assignment_submitted_date':parse_date(get(row, 'assignment_submitted_date')),
        'is_assignment_passed':     _bool(get(row, 'is_assignment_passed')),
        'passed_on_first_attempt':  _bool(get(row, 'passed_on_first_attempt')),
        'is_programme_graduated':   _bool(get(row, 'is_programme_graduated')),
        'programme_graduation_date':parse_date(get(row, 'programme_graduation_date')),
        'is_graduated_on_savanna':  _bool(get(row, 'is_graduated_on_savanna')),
        'payment_status':           normalise_payment_status(get(row, 'payment_status')),
    }
