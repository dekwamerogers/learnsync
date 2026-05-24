from django.core.management.base import BaseCommand

from selfpaced.engine import recompute_health


class Command(BaseCommand):
    help = 'Recompute health status for all enrolments from current assignment data (no re-upload needed)'

    def add_arguments(self, parser):
        parser.add_argument(
            '--programme',
            metavar='CODE',
            help='Limit to a specific programme code (e.g. WALX)',
        )

    def handle(self, *args, **options):
        programme_code = options.get('programme')
        self.stdout.write(
            f'Recomputing health'
            + (f' for {programme_code.upper()}' if programme_code else ' for all enrolments')
            + '…'
        )
        result = recompute_health(programme_code)
        msg = f'Done — {result["updated"]} enrolment(s) updated.'
        if result['errors']:
            msg += f' {result["errors"]} error(s):'
            self.stdout.write(self.style.WARNING(msg))
            for detail in result['error_detail']:
                self.stderr.write(f'  {detail}')
        else:
            self.stdout.write(self.style.SUCCESS(msg))
