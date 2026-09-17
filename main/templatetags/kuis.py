"""Template helpers for carrying query state between forms, sort links and pages.

Analysis state lives in the query string (plus the session for corpus ids), and
several controls on a page each need to preserve everyone else's parameters.
Doing that by hand means every new filter has to be added to every form; these
tags read whatever is actually in the request instead.
"""

from django import template
from django.utils.html import format_html, format_html_join

register = template.Library()


@register.simple_tag(takes_context=True)
def hidden_params(context, exclude=''):
    """Re-emit the current query string as hidden inputs, minus `exclude`.

    Lets a form change one parameter without discarding the others (e.g. the
    text-mode toggle keeps the KWIC query and window intact).
    """
    request = context['request']
    skip = {name.strip() for name in exclude.split(',') if name.strip()}

    pairs = [
        (key, value)
        for key in request.GET
        if key not in skip
        for value in request.GET.getlist(key)
        if value != ''
    ]

    return format_html_join(
        '\n', '<input type="hidden" name="{}" value="{}">', pairs
    )


@register.simple_tag(takes_context=True)
def qs(context, **kwargs):
    """Current query string with `kwargs` applied. Empty values drop the key.

    Paging and sorting links use this so they never lose the active filters.
    """
    request = context['request']
    params = request.GET.copy()

    for key, value in kwargs.items():
        if value in (None, ''):
            params.pop(key, None)
        else:
            params[key] = value

    # Corpus ids are already in the session; keeping them out of every link
    # stops the query string growing without bound as the user navigates.
    params.pop('corpus_selection', None)
    params.pop('corpus', None)

    encoded = params.urlencode()

    return format_html('?{}', encoded) if encoded else '?'


@register.simple_tag(takes_context=True)
def sort_url(context, field, default_dir='desc'):
    """Link for a sortable column header: same column flips direction, a new
    column starts at its natural direction and returns to page 1."""
    request = context['request']
    current = request.GET.get('sort')
    current_dir = request.GET.get('dir', 'desc')

    direction = default_dir
    if current == field:
        direction = 'asc' if current_dir == 'desc' else 'desc'

    return qs(context, sort=field, dir=direction, page=None)


@register.simple_tag(takes_context=True)
def aria_sort(context, field):
    """`aria-sort` value for a column header, or empty when it isn't sorted."""
    request = context['request']

    if request.GET.get('sort') != field:
        return ''

    return 'ascending' if request.GET.get('dir', 'desc') == 'asc' else 'descending'


@register.filter
def per_million(count, total):
    """Normalised frequency, so corpora of different sizes stay comparable."""
    try:
        total = int(total)
        if total <= 0:
            return ''
        return round(int(count) * 1_000_000 / total, 1)
    except (TypeError, ValueError):
        return ''


@register.filter
def bar_width(count, maximum):
    """Percentage width for the inline magnitude bar in a frequency table."""
    try:
        maximum = int(maximum)
        if maximum <= 0:
            return 0
        return max(1, round(int(count) * 100 / maximum))
    except (TypeError, ValueError):
        return 0
