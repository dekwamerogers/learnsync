"""
Merge one Programme into another, transferring courses and enrolments.

Usage:
    python manage.py merge_programme <source_code> <target_code>

Example (fix the AiCE / AICE duplicate):
    python manage.py merge_programme AiCE AICE
"""

from django.core.management.base import BaseCommand, CommandError
from django.db import transaction

from selfpaced.models import Course, Enrolment, Programme


class Command(BaseCommand):
    help = 'Merge source programme into target, then delete source.'

    def add_arguments(self, parser):
        parser.add_argument('source_code', help='Programme code to merge FROM (will be deleted)')
        parser.add_argument('target_code', help='Programme code to merge INTO (kept)')

    def handle(self, *args, **options):
        src_code = options['source_code']
        tgt_code = options['target_code']

        try:
            src = Programme.objects.get(code=src_code)
        except Programme.DoesNotExist:
            raise CommandError(f'Source programme "{src_code}" not found.')

        try:
            tgt = Programme.objects.get(code=tgt_code)
        except Programme.DoesNotExist:
            raise CommandError(f'Target programme "{tgt_code}" not found.')

        with transaction.atomic():
            src_courses = list(Course.objects.filter(programme=src).order_by('sequence_number'))
            tgt_course_map = {
                c.sequence_number: c
                for c in Course.objects.filter(programme=tgt)
            }

            # Build mapping: src course PK -> tgt course (for prerequisite re-pointing)
            src_to_tgt = {}
            courses_moved = 0
            courses_merged = 0

            for sc in src_courses:
                if sc.sequence_number in tgt_course_map:
                    # Target already has this slot — update target with src data, map old pk
                    tc = tgt_course_map[sc.sequence_number]
                    tc.code = sc.code
                    tc.full_name = sc.full_name
                    tc.is_active = sc.is_active
                    if sc.expected_duration_days is not None:
                        tc.expected_duration_days = sc.expected_duration_days
                    tc.save(update_fields=['code', 'full_name', 'is_active', 'expected_duration_days'])
                    src_to_tgt[sc.pk] = tc
                    courses_merged += 1
                else:
                    # No conflict — move course directly to target
                    sc.programme = tgt
                    sc.save(update_fields=['programme'])
                    src_to_tgt[sc.pk] = sc
                    courses_moved += 1

            # Re-point prerequisites: any tgt course whose prerequisite was a src course
            for sc_pk, tc in src_to_tgt.items():
                # Find tgt courses whose prerequisite pointed to a src course
                for candidate in Course.objects.filter(
                    programme=tgt, prerequisite_course_id__in=src_to_tgt.keys()
                ):
                    old_prereq_pk = candidate.prerequisite_course_id
                    new_prereq = src_to_tgt.get(old_prereq_pk)
                    if new_prereq and candidate.prerequisite_course_id != new_prereq.pk:
                        candidate.prerequisite_course = new_prereq
                        candidate.save(update_fields=['prerequisite_course'])

            # Delete src courses that were merged (not moved)
            merged_src_pks = [
                sc.pk for sc in src_courses
                if sc.sequence_number in tgt_course_map
            ]
            Course.objects.filter(pk__in=merged_src_pks).delete()

            # Move any enrolments from src to tgt (programme structure upload
            # doesn't create enrolments, but handle it defensively)
            enrolments_moved = 0
            for enrolment in Enrolment.objects.filter(programme=src):
                if not Enrolment.objects.filter(
                    learner=enrolment.learner, programme=tgt
                ).exists():
                    enrolment.programme = tgt
                    enrolment.save(update_fields=['programme'])
                    enrolments_moved += 1
                else:
                    self.stdout.write(
                        self.style.WARNING(
                            f'  Skipped enrolment for {enrolment.learner_id} '
                            f'— already enrolled in {tgt_code}'
                        )
                    )

            # Delete source programme (all its courses should be gone or moved)
            remaining = Course.objects.filter(programme=src).count()
            if remaining:
                raise CommandError(
                    f'{remaining} course(s) still attached to "{src_code}" — aborting.'
                )
            src.delete()

        self.stdout.write(self.style.SUCCESS(
            f'Merged "{src_code}" → "{tgt_code}": '
            f'{courses_moved} courses moved, {courses_merged} merged, '
            f'{enrolments_moved} enrolments moved. '
            f'"{src_code}" deleted.'
        ))
