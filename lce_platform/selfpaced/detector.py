"""
Programme and course detection from eHub class name and course name fields.

Two eHub class name formats are handled:

  Format A — programme code + course sequence embedded:
    {PROGRAMME_CODE}-{COURSE_SEQ}_{anything}
    e.g.  VA-1_C#1   AICE-2_rolling   CS-3_C#14

  Format B — programme code only, course sequence is in a separate CSV column:
    {PROGRAMME_CODE}_{anything}
    e.g.  WALX_C#1   SPD_cohort2

Detection order:
  1. Exact registry match on ehub_class_name.
  2. Format A regex  →  extract programme + course, auto-create Programme if new.
  3. Format B prefix →  extract programme only, auto-create Programme if new.
                        Engine resolves Course via the row's Course sequence number.
  4. Course-name prefix registry lookup.
  5. Return (None, None)  →  caller creates a FlaggedRow.
"""

import logging
import re

logger = logging.getLogger(__name__)

# Format A: PROGRAMME_CODE - COURSE_SEQ _ anything  (e.g. AICE-2_rolling)
_EHUB_PATTERN = re.compile(r'^([A-Z][A-Z0-9]*)-(\d+)_', re.IGNORECASE)

# Format B: PROGRAMME_CODE _ anything  (e.g. WALX_C#1)
_EHUB_PREFIX_PATTERN = re.compile(r'^([A-Z][A-Z0-9]*)_', re.IGNORECASE)


def parse_ehub_class_name(ehub_class_name: str) -> tuple[str | None, int | None]:
    """
    Extract (programme_code, course_seq_number) from an eHub class name string.
    Returns (None, None) if the string does not match the expected pattern.
    """
    m = _EHUB_PATTERN.match(ehub_class_name.strip())
    if not m:
        return None, None
    return m.group(1).upper(), int(m.group(2))


def detect_programme_and_course(ehub_class_name: str, course_name: str):
    """
    Return (Programme, Course) by looking up the ProgrammeIdentifierRegistry.

    Resolution order:
      1. Exact match on ehub_class_name raw_pattern.
      2. Parsed programme_code + course_seq lookup against Programme.code and Course.sequence_number.
      3. Course-name prefix lookup in registry.

    Returns (programme, course) where course may be None if only the programme
    was resolved (caller then searches Course by sequence_number).

    Returns (None, None) if nothing matched — caller creates a FlaggedRow.
    """
    from selfpaced.models import Course, Programme, ProgrammeIdentifierRegistry

    # --- Step 1: exact registry match on eHub class name ---
    try:
        entry = ProgrammeIdentifierRegistry.objects.select_related(
            'programme', 'course'
        ).get(raw_pattern=ehub_class_name.strip(), pattern_type='ehub_class_name')
        return entry.programme, entry.course
    except ProgrammeIdentifierRegistry.DoesNotExist:
        pass

    # --- Step 2: parse the pattern and look up (or create) by code + sequence ---
    programme_code, course_seq = parse_ehub_class_name(ehub_class_name)
    if programme_code and course_seq is not None:
        # Case-insensitive lookup first so AiCE in CSV resolves to existing AICE record.
        programme = (
            Programme.objects.filter(code__iexact=programme_code).first()
            or Programme.objects.filter(ehub_code__iexact=programme_code).first()
        )
        if not programme:
            programme = Programme.objects.create(
                code=programme_code.upper(),
                name=programme_code.upper(),
                is_active=True,
            )
            logger.info('Auto-created Programme %s from eHub class name %r',
                        programme_code.upper(), ehub_class_name)

        course = Course.objects.filter(
            programme=programme,
            sequence_number=course_seq,
            is_active=True,
        ).first()
        if course:
            # Auto-register so future uploads skip the regex lookup
            ProgrammeIdentifierRegistry.objects.get_or_create(
                raw_pattern=ehub_class_name.strip(),
                defaults={
                    'pattern_type': 'ehub_class_name',
                    'programme': programme,
                    'course': course,
                },
            )
            return programme, course
        # Programme exists but course not yet in catalogue — engine creates it
        return programme, None

    # --- Step 3: Format B — PROGRAMME_anything (e.g. WALX_C#1, SPD_cohort2) ---
    # Course sequence is NOT embedded; caller resolves it from the row's column.
    m_b = _EHUB_PREFIX_PATTERN.match(ehub_class_name.strip())
    if m_b:
        programme_code_b = m_b.group(1).upper()
        # Case-insensitive lookup so CSV variants (e.g. AiCE) match existing records.
        programme = (
            Programme.objects.filter(code__iexact=programme_code_b).first()
            or Programme.objects.filter(ehub_code__iexact=programme_code_b).first()
        )
        if not programme:
            programme = Programme.objects.create(
                code=programme_code_b,
                name=programme_code_b,
                is_active=True,
            )
            logger.info('Auto-created Programme %s from eHub class name %r (Format B)',
                        programme_code_b, ehub_class_name)
        return programme, None

    # --- Step 4: course name prefix registry lookup ---
    for entry in ProgrammeIdentifierRegistry.objects.filter(
        pattern_type='course_name_prefix'
    ).select_related('programme', 'course'):
        if course_name.startswith(entry.raw_pattern):
            return entry.programme, entry.course

    logger.debug(
        'No programme/course match for eHub class name=%r course_name=%r',
        ehub_class_name, course_name,
    )
    return None, None
