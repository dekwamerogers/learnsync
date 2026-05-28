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


def bulk_detect(ehub_course_hints: dict[str, str]) -> dict[str, tuple]:
    """
    Batch-resolve a {ehub_class_name: course_name_hint} dict to
    {ehub_class_name: (Programme, Course | None)}.

    Uses 4 bulk queries instead of up to 5 per class name, so it scales
    to large CSVs without N+1 slowdowns:
      1. Bulk registry lookup for all ehub_class_names
      2. Bulk Programme lookup for unmatched codes
      3. Bulk Course lookup for resolved (programme_id, seq) pairs
      4. Bulk registry insert for newly-matched pairs
      5. Full registry scan for course_name_prefix entries (once only)

    Auto-creates Programme records for brand-new programme codes (same as
    detect_programme_and_course does one-by-one).
    """
    from selfpaced.models import Course, Programme, ProgrammeIdentifierRegistry

    if not ehub_course_hints:
        return {}

    results: dict[str, tuple] = {}
    unresolved: set[str] = set(ehub_course_hints.keys())

    # ── Step 1: bulk exact registry match ───────────────────────────────
    registry_map: dict[str, tuple] = {
        entry.raw_pattern: (entry.programme, entry.course)
        for entry in ProgrammeIdentifierRegistry.objects.filter(
            pattern_type='ehub_class_name',
            raw_pattern__in=unresolved,
        ).select_related('programme', 'course')
    }
    for ehub, pair in registry_map.items():
        results[ehub] = pair
    unresolved -= set(registry_map.keys())

    if not unresolved:
        return results

    # ── Step 2: parse unresolved → programme codes and bulk-fetch Programmes ─
    # Format A: PROGRAMME_CODE-SEQ_anything
    # Format B: PROGRAMME_CODE_anything
    parsed_codes: dict[str, tuple] = {}   # ehub -> (code, seq | None)
    for ehub in unresolved:
        m_a = _EHUB_PATTERN.match(ehub.strip())
        if m_a:
            parsed_codes[ehub] = (m_a.group(1).upper(), int(m_a.group(2)))
            continue
        m_b = _EHUB_PREFIX_PATTERN.match(ehub.strip())
        if m_b:
            parsed_codes[ehub] = (m_b.group(1).upper(), None)

    # All codes are already upper-cased from the regex parse above.
    # prog_by_code keys are normalised to uppercase throughout.
    needed_codes = {code for code, _ in parsed_codes.values()}
    prog_by_code: dict[str, object] = {}  # uppercase code -> Programme
    if needed_codes:
        # Primary lookup by Programme.code (case-sensitive — codes are stored uppercase)
        for p in Programme.objects.filter(code__in=needed_codes):
            prog_by_code[p.code.upper()] = p
        # Fallback 1: case-insensitive code lookup (handles 'AiCE' → 'AICE' etc.)
        missing = needed_codes - set(prog_by_code.keys())
        if missing:
            for p in Programme.objects.filter(
                code__iregex=r'^(' + '|'.join(re.escape(c) for c in missing) + r')$'
            ):
                prog_by_code[p.code.upper()] = p
        # Fallback 2: match against Programme.ehub_code column
        still_missing = needed_codes - set(prog_by_code.keys())
        if still_missing:
            for p in Programme.objects.filter(
                ehub_code__iregex=r'^(' + '|'.join(re.escape(c) for c in still_missing) + r')$'
            ):
                # Store under the CSV code so lookups below find the right key.
                for csv_code in still_missing:
                    if p.ehub_code and p.ehub_code.upper() == csv_code:
                        prog_by_code[csv_code] = p

    # Auto-create programmes for brand-new codes (code not in DB at all)
    for ehub, (code, seq) in parsed_codes.items():
        if code not in prog_by_code:
            prog = Programme.objects.create(
                code=code, name=code, is_active=True,
            )
            prog_by_code[code] = prog
            logger.info('Auto-created Programme %s from eHub class name %r', code, ehub)

    # ── Step 3: bulk Course lookup for Format-A entries ─────────────────
    format_a: dict[str, tuple] = {
        ehub: (parsed_codes[ehub][0], parsed_codes[ehub][1])
        for ehub in unresolved
        if ehub in parsed_codes and parsed_codes[ehub][1] is not None
    }
    if format_a:
        pairs = {
            (prog_by_code[code].pk, seq)
            for code, seq in format_a.values()
            if code in prog_by_code
        }
        course_map: dict[tuple, object] = {
            (c.programme_id, c.sequence_number): c
            for c in Course.objects.filter(
                programme_id__in={p for p, _ in pairs},
                sequence_number__in={s for _, s in pairs},
                is_active=True,
            )
        }
    else:
        course_map = {}

    # ── Step 4: assign Format-A and Format-B results; queue registry inserts ─
    to_register: list = []
    for ehub in list(unresolved):
        if ehub not in parsed_codes:
            continue
        code, seq = parsed_codes[ehub]
        prog = prog_by_code.get(code)
        if prog is None:
            continue
        if seq is not None:
            # Format A
            course = course_map.get((prog.pk, seq))
            results[ehub] = (prog, course)
            if course:
                to_register.append(ProgrammeIdentifierRegistry(
                    raw_pattern=ehub.strip(),
                    pattern_type='ehub_class_name',
                    programme=prog,
                    course=course,
                ))
        else:
            # Format B — caller resolves course via sequence column
            results[ehub] = (prog, None)
        unresolved.discard(ehub)

    # Bulk-register newly matched Format-A pairs (ignore duplicates)
    if to_register:
        ProgrammeIdentifierRegistry.objects.bulk_create(
            to_register, ignore_conflicts=True, batch_size=500,
        )

    # ── Step 5: course-name prefix lookup for anything still unresolved ──
    if unresolved:
        prefix_entries = list(
            ProgrammeIdentifierRegistry.objects.filter(
                pattern_type='course_name_prefix'
            ).select_related('programme', 'course')
        )
        for ehub in list(unresolved):
            hint = ehub_course_hints.get(ehub, '')
            for entry in prefix_entries:
                if hint.startswith(entry.raw_pattern):
                    results[ehub] = (entry.programme, entry.course)
                    unresolved.discard(ehub)
                    break

    # Anything still unresolved → (None, None)
    for ehub in unresolved:
        logger.debug('No match for eHub class name %r', ehub)
        results[ehub] = (None, None)

    return results
