import csv
import io

from django.contrib.auth.decorators import login_required
from django.db import transaction
from django.shortcuts import render

from selfpaced.models import Course, Programme


@login_required
def upload_programme_structure(request):
    result = None
    parse_error = None

    if request.method == 'POST' and request.FILES.get('file'):
        f = request.FILES['file']
        try:
            content = f.read().decode('utf-8-sig')  # strip BOM if Excel-exported
            result = _process_csv(content)
        except Exception as exc:
            parse_error = str(exc)

    return render(request, 'selfpaced/admin/programme_structure.html', {
        'result': result,
        'parse_error': parse_error,
    })


def _process_csv(content):
    reader = csv.DictReader(io.StringIO(content))

    rows = []
    for i, row in enumerate(reader, start=2):  # 2 = first data row
        try:
            rows.append({
                'prog': row['Program Abbreviation'].strip(),
                'seq': int(row['Course Sequence'].strip()),
                'code': row['Short Course Abbreviation'].strip(),
                'name': row['Short Course Name'].strip(),
                'prereq': row['Prerequisites'].strip(),
            })
        except (KeyError, ValueError) as exc:
            raise ValueError(f'Row {i}: {exc}') from exc

    programmes_created = 0
    programmes_updated = 0
    courses_created = 0
    courses_updated = 0
    prerequisites_resolved = 0
    warnings = []
    programme_summaries = []  # [{code, created, course_count, grad_count}]

    with transaction.atomic():
        # ── Pass 1: upsert programmes and courses ──────────────────────────
        # prog_code -> {seq -> Course}
        seq_maps = {}
        prog_objects = {}
        prog_created_flags = {}  # pc -> bool (True = newly created)

        for row in rows:
            pc = row['prog']
            if pc not in prog_objects:
                # Case-insensitive match: AiCE in CSV must not create a duplicate of AICE
                existing = Programme.objects.filter(code__iexact=pc).first()
                if existing:
                    prog = existing
                    created = False
                    programmes_updated += 1
                else:
                    prog = Programme.objects.create(code=pc, name=pc)
                    created = True
                    programmes_created += 1
                prog_objects[pc] = prog
                prog_created_flags[pc] = created
                seq_maps[pc] = {}

            course, created = Course.objects.update_or_create(
                programme=prog_objects[pc],
                sequence_number=row['seq'],
                defaults={
                    'code': row['code'],
                    'full_name': row['name'],
                    'is_active': True,
                },
            )
            if created:
                courses_created += 1
            else:
                courses_updated += 1
            seq_maps[pc][row['seq']] = course

        # ── Pass 2: resolve prerequisites within each programme ────────────
        for pc, seq_map in seq_maps.items():
            # code -> Course lookup for this programme
            code_map = {c.code: c for c in seq_map.values()}

            for row in rows:
                if row['prog'] != pc:
                    continue
                prereq_code = row['prereq']
                course = seq_map[row['seq']]

                if prereq_code in ('n/a', 'N/A', '', '-'):
                    if course.prerequisite_course_id is not None:
                        course.prerequisite_course = None
                        course.save(update_fields=['prerequisite_course'])
                    continue

                prereq = code_map.get(prereq_code)
                if prereq is None:
                    warnings.append(
                        f'{pc} seq {row["seq"]} ({row["code"]}): '
                        f'prerequisite "{prereq_code}" not found in this programme — skipped'
                    )
                    continue

                if course.prerequisite_course_id != prereq.pk:
                    course.prerequisite_course = prereq
                    course.save(update_fields=['prerequisite_course'])
                prerequisites_resolved += 1

        # ── Pass 3: update total_courses_for_graduation per programme ──────
        for pc, prog in prog_objects.items():
            courses_qs = Course.objects.filter(programme=prog, is_active=True)
            total = courses_qs.count()
            # Graduation excludes WALX (the shared prerequisite course)
            grad_count = courses_qs.exclude(code='WALX').count()
            if prog.total_courses_for_graduation != grad_count:
                prog.total_courses_for_graduation = grad_count
                prog.save(update_fields=['total_courses_for_graduation'])
            programme_summaries.append({
                'code': pc,
                'pk': prog.pk,
                'created': prog_created_flags.get(pc, False),
                'total_courses': total,
                'grad_courses': grad_count,
            })

        # ── Pass 4: mark shared modules ─────────────────────────────────────
        # A course code is "shared" when it appears in 2+ programmes in the
        # full catalogue (not just this upload). This covers PF-1…PF-5, which
        # are embedded in CC, DA, DS, and GD at different sequence positions.
        # The engine's _propagate_shared_module_credits() step uses this flag
        # to mirror completions across all enrolments that contain the same code.
        from django.db.models import Count as _Count
        shared_codes = set(
            Course.objects
            .values('code')
            .annotate(prog_count=_Count('programme', distinct=True))
            .filter(prog_count__gt=1)
            .exclude(code='')          # ignore courses with no code set
            .values_list('code', flat=True)
        )
        shared_updated = 0
        if shared_codes:
            shared_updated = Course.objects.filter(
                code__in=shared_codes, is_shared_module=False
            ).update(is_shared_module=True)
        # Reset any codes that were previously shared but are no longer
        # (handles catalogue restructuring where a course is moved to one programme).
        Course.objects.exclude(code__in=shared_codes).filter(
            is_shared_module=True
        ).update(is_shared_module=False)

        # ── Pass 4: ensure standalone WALX Programme exists ───────────────
        # eHub generates separate WALX rows per learner regardless of which
        # real programme they belong to. We need a WALX Programme to receive them.
        walx_seqs = sorted({
            row['seq'] for row in rows if row['code'] == 'WALX'
        })
        if walx_seqs:
            walx_prog, walx_created = Programme.objects.get_or_create(
                code='WALX',
                defaults={
                    'name': 'Welcome to ALX',
                    'awards_credentials': True,   # badges allowed
                    'awards_certificate': False,   # no graduation cert
                    'is_active': True,
                    'is_prerequisite': True,
                },
            )
            if not walx_created and not walx_prog.is_prerequisite:
                walx_prog.is_prerequisite = True
                walx_prog.save(update_fields=['is_prerequisite'])
            if walx_created:
                programmes_created += 1
            else:
                programmes_updated += 1
            # Ensure a WALX course exists for each sequence number seen in the CSV
            for seq in walx_seqs:
                walx_course, c_created = Course.objects.get_or_create(
                    programme=walx_prog,
                    sequence_number=seq,
                    defaults={
                        'code': 'WALX',
                        'full_name': 'Welcome to ALX',
                        'is_active': True,
                    },
                )
                if c_created:
                    courses_created += 1
            programme_summaries.append({
                'code': 'WALX',
                'pk': walx_prog.pk,
                'created': walx_created,
                'total_courses': Course.objects.filter(programme=walx_prog, is_active=True).count(),
                'grad_courses': 0,
            })

    return {
        'programmes_created': programmes_created,
        'programmes_updated': programmes_updated,
        'courses_created': courses_created,
        'courses_updated': courses_updated,
        'prerequisites_resolved': prerequisites_resolved,
        'shared_modules_marked': shared_updated,
        'shared_codes': sorted(shared_codes),
        'warnings': warnings,
        'summaries': programme_summaries,
    }
