"""
LearnSync — one-shot deployment setup script.

Run via cPanel Python App panel → Execute Python Script:
    /home/USERNAME/APPROOT/setup.py

Or after the first run, for updates only:
    /home/USERNAME/APPROOT/setup.py --no-static --no-superuser
"""
import os
import sys
import argparse
import subprocess

# ── Config ────────────────────────────────────────────────────────────────────
SUPERUSER_USERNAME = 'admin'
SUPERUSER_EMAIL    = 'dekwamerogers@gmail.com'
SUPERUSER_PASSWORD = 'CHANGE_ME_AFTER_FIRST_LOGIN'   # ← change before uploading
# ─────────────────────────────────────────────────────────────────────────────

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)

os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'lce_platform.settings')


def header(msg):
    print(f'\n{"─" * 60}')
    print(f'  {msg}')
    print(f'{"─" * 60}')


def step(label, fn):
    print(f'\n▶  {label}')
    try:
        fn()
        print(f'   ✓ done')
    except SystemExit as e:
        if e.code == 0:
            print(f'   ✓ done')
        else:
            print(f'   ✗ failed (exit {e.code})')
            raise
    except Exception as e:
        print(f'   ✗ error: {e}')
        raise


def run_pip():
    """Install dependencies from requirements.txt."""
    req = os.path.join(HERE, 'requirements.txt')
    result = subprocess.run(
        [sys.executable, '-m', 'pip', 'install', '-r', req, '-q'],
        capture_output=True, text=True
    )
    if result.returncode != 0:
        print(result.stderr)
        raise RuntimeError('pip install failed')
    if result.stdout.strip():
        print(f'   {result.stdout.strip()}')


def run_migrations():
    from django.core.management import call_command
    call_command('migrate', '--no-input', verbosity=1)


def run_collectstatic():
    from django.core.management import call_command
    call_command('collectstatic', '--no-input', verbosity=1)


def create_superuser():
    from django.contrib.auth import get_user_model
    User = get_user_model()
    if User.objects.filter(username=SUPERUSER_USERNAME).exists():
        print(f'   ℹ  Superuser "{SUPERUSER_USERNAME}" already exists — skipped')
        return
    User.objects.create_superuser(
        username=SUPERUSER_USERNAME,
        email=SUPERUSER_EMAIL,
        password=SUPERUSER_PASSWORD,
    )
    print(f'   ✓ Superuser "{SUPERUSER_USERNAME}" created')
    print(f'   ⚠  Log in and change the password immediately!')


def run_checks():
    from django.core.management import call_command
    call_command('check', '--deploy', verbosity=1)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description='LearnSync deployment setup')
    parser.add_argument('--no-pip',        action='store_true', help='Skip pip install')
    parser.add_argument('--no-migrate',    action='store_true', help='Skip migrations')
    parser.add_argument('--no-static',     action='store_true', help='Skip collectstatic')
    parser.add_argument('--no-superuser',  action='store_true', help='Skip superuser creation')
    parser.add_argument('--no-check',      action='store_true', help='Skip deploy checks')
    args = parser.parse_args()

    header('LearnSync — Deployment Setup')

    if not args.no_pip:
        step('Installing dependencies (pip)', run_pip)

    # Django must be set up before any management commands
    import django
    django.setup()

    if not args.no_migrate:
        step('Running migrations', run_migrations)

    if not args.no_static:
        step('Collecting static files', run_collectstatic)

    if not args.no_superuser:
        step('Creating superuser', create_superuser)

    if not args.no_check:
        step('Running deployment checks', run_checks)

    header('Setup complete')
    print()
    print('  Next steps:')
    print('  1. Restart the app in cPanel → Setup Python App → Restart')
    print('  2. Visit your site and log in')
    print(f'  3. Change the superuser password for "{SUPERUSER_USERNAME}" immediately')
    print()
