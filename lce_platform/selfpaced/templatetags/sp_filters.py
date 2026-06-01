from datetime import date
from urllib.parse import quote_plus

from django import template

register = template.Library()


@register.filter
def qparams_except(request_get, key):
    """Return URL-encoded query string with `key` removed, trailing & included when non-empty.
    Usage: ?{{ request.GET|qparams_except:'page' }}page=2
    """
    params = request_get.copy()
    params.pop(key, None)
    encoded = params.urlencode()
    return encoded + '&' if encoded else ''


@register.filter
def urlencode_val(value):
    """URL-encode a single string value for use in a query string."""
    return quote_plus(str(value))


@register.filter
def timeago(value):
    """Convert a date to a compact relative string: '3d ago', '2w ago', '4mo ago'."""
    if not value:
        return '—'
    try:
        days = (date.today() - value).days
    except TypeError:
        return '—'
    if days <= 0:
        return 'today'
    if days == 1:
        return 'yesterday'
    if days < 7:
        return f'{days}d ago'
    if days < 30:
        return f'{days // 7}w ago'
    if days < 365:
        months = max(1, round(days / 30.4))
        return f'{months}mo ago'
    years = max(1, round(days / 365.25))
    return f'{years}yr ago'


@register.filter
def completed_courses(course_enrolments):
    """Count CourseEnrolments with status='completed'."""
    return sum(1 for ce in course_enrolments if ce.status == 'completed')


@register.filter
def ap_submitted(assignment_progress_qs):
    """Count submitted AssignmentProgress records."""
    return sum(1 for ap in assignment_progress_qs if ap.is_submitted)


@register.filter
def ap_passed(assignment_progress_qs):
    """Count passed AssignmentProgress records."""
    return sum(1 for ap in assignment_progress_qs if ap.is_passed)


@register.filter
def get_item(d, key):
    """Dictionary/dict-like access: {{ mydict|get_item:key }}"""
    if d is None:
        return None
    try:
        return d.get(key)
    except AttributeError:
        return None


@register.filter
def get_attr(obj, attr):
    """Object attribute access: {{ obj|get_attr:'field_name' }}"""
    if obj is None:
        return None
    val = getattr(obj, attr, None)
    # Return empty string rather than None for numeric fields so templates can distinguish
    return val


@register.filter
def humanpace(value):
    """Convert a float pace (courses/week) to a human-readable string.
    0.33 → '1 every 3 weeks' | 1.0 → '1/week' | 1.5 → '1.5/week'
    """
    if value is None:
        return '—'
    if value == 0:
        return '0/week'
    if value >= 1:
        if value == int(value):
            n = int(value)
            return f'{n}/week'
        return f'{value:.1f}/week'
    # pace < 1 — express as "1 every N weeks"
    weeks = max(2, round(1 / value))
    return f'1 every {weeks}w'


@register.filter
def ap_pass_pct(assignment_progress_qs):
    """Compute pass % from an AssignmentProgress iterable. Returns int or None."""
    aps = list(assignment_progress_qs)
    submitted = [ap for ap in aps if ap.is_submitted]
    if not submitted:
        return None
    passed = sum(1 for ap in submitted if ap.is_passed)
    return round(passed / len(submitted) * 100)


@register.filter
def abs_val(value):
    """Return the absolute value of a number."""
    try:
        return abs(value)
    except (TypeError, ValueError):
        return value
