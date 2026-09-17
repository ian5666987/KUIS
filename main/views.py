from django.shortcuts import render, redirect, get_object_or_404 # redirect is needed for form
from django.http import HttpResponse
from django.contrib.auth import login
from django.contrib.auth.decorators import login_required
from django.contrib import messages
from django.core.exceptions import PermissionDenied
from django.urls import reverse
from django.utils.http import url_has_allowed_host_and_scheme
from functools import wraps
from .forms import RegisterForm #this is taking from the same-folder 'forms' file

#For contact
from django.core.mail import send_mail 
from django.conf import settings
from .forms import ContactForm

#For login and authentication
from django.contrib.auth.forms import AuthenticationForm
from django.contrib.auth.views import LoginView

#For corpus functionalities
from .models import Token, Document, Corpus
from .corpus_parsing import parse_document
from collections import Counter

#For document uploading
from .forms import CorpusForm, DocumentForm

#For pagination
from django.core.paginator import Paginator

#After Postgres migrations
from django.db.models import Q, Count, Sum

#For export to CSV
import csv

# Create your views here.
def home(request):
    return render(request, 'main/home.html', {
        'corpus_count': Corpus.objects.count(),
        'document_count': Document.objects.count()
    })

def about(request):
    return render(request, 'main/about.html')

# Corpus curation is restricted to admins; analysis stays open to any logged-in user.
# Pair this with @login_required on the outside: anonymous visitors get the login
# page, while a signed-in non-admin gets a plain 403 instead of a confusing bounce
# back to a login form they're already past.
def staff_required(view_func):
    @wraps(view_func)
    def wrapper(request, *args, **kwargs):
        if not (request.user.is_active and request.user.is_staff):
            raise PermissionDenied

        return view_func(request, *args, **kwargs)

    return wrapper

@login_required
def dashboard(request):
    """Entry point: what's in the collection, and what is currently selected."""
    selected_ids = _resolve_selected_corpus_ids(request)
    documents = _get_selected_documents(request)

    return render(request, 'main/dashboard.html', {
        'corpora': _annotated_corpora()[:6],
        'corpus_total': Corpus.objects.count(),
        'document_total': Document.objects.count(),
        'token_total': Document.objects.aggregate(n=Sum('token_count'))['n'] or 0,
        'selected_corpora': Corpus.objects.filter(id__in=selected_ids),
        'selected_corpus_count': len(selected_ids),
        'selected_document_count': documents.count(),
        'unassigned_count': Document.objects.filter(corpora__isnull=True).count()
    })

@login_required
def profile(request):
    return render(request, 'main/profile.html', {
        'document_count': Document.objects.filter(user=request.user).count(),
        'corpus_count': Corpus.objects.filter(created_by=request.user).count()
    })

# This is the registration method
def register(request):
    if request.method == "POST":
        form = RegisterForm(request.POST)
        if form.is_valid():
            user = form.save()
            login(request, user)  # auto login after signup
            return redirect('dashboard')
    else:
        form = RegisterForm()

    return render(request, 'registration/register.html', {"form": form})

# This is the contact method
def contact(request):
    if request.method == "POST":
        form = ContactForm(request.POST)
        if form.is_valid():
            name = form.cleaned_data['name']
            email = form.cleaned_data['email']
            message = form.cleaned_data['message']

            full_message = f"""
From: {name} <{email}>

Message:
{message}
"""

            send_mail(
                subject="New Contact Form Submission",
                message=full_message,
                from_email=settings.DEFAULT_FROM_EMAIL,
                recipient_list=[settings.DEFAULT_FROM_EMAIL],
            )

            return render(request, 'main/contact.html', {
                'form': ContactForm(),
                'success': True
            })
    else:
        form = ContactForm()

    return render(request, 'main/contact.html', {'form': form})

def custom_login(request):
    form = AuthenticationForm()

    # Add form styling classes
    for field in form.fields.values():
        field.widget.attrs['class'] = 'field'

    return render(request, 'registration/login.html', {'form': form})

class CustomLoginView(LoginView):
    template_name = 'registration/login.html'
    authentication_form = AuthenticationForm

    def get_form(self, form_class=None):
        form = super().get_form(form_class)

        # Match the field styling used everywhere else
        for field in form.fields.values():
            field.widget.attrs['class'] = 'field'

        return form


def _annotated_corpora():
    """Corpora with the metadata the picker and listings show. distinct=True on
    the count because the token sums join the same M2M table."""
    return Corpus.objects.annotate(
        document_count=Count('documents', distinct=True),
        token_total=Sum('documents__token_count'),
        token_total_corrected=Sum('documents__token_count_corrected')
    ).order_by('name')


@login_required
def corpus_dashboard(request):
    query = request.GET.get('q', '').strip()
    corpora = _annotated_corpora()

    if query:
        corpora = corpora.filter(Q(name__icontains=query) | Q(description__icontains=query))

    sort = request.GET.get('sort', 'name')
    direction = request.GET.get('dir', 'asc' if sort == 'name' else 'desc')
    sort_fields = {
        'name': 'name',
        'files': 'document_count',
        'tokens': 'token_total',
        'created': 'created_at'
    }
    field = sort_fields.get(sort, 'name')
    corpora = corpora.order_by(f"{'-' if direction == 'desc' else ''}{field}")

    unassigned = Document.objects.filter(corpora__isnull=True).order_by('-uploaded_at')

    return render(request, 'main/corpus_dashboard.html', {
        'corpora': corpora,
        'corpus_total': Corpus.objects.count(),
        'query': query,
        'unassigned': unassigned,
        'unassigned_count': unassigned.count(),
        'selected_corpus_ids': _resolve_selected_corpus_ids(request),
        # The assign dialog works over everything, not just what the search left
        # on screen: any selection of files into any selection of corpora.
        'all_corpora': _annotated_corpora(),
        'all_documents': Document.objects.prefetch_related('corpora').order_by('-uploaded_at'),
        'document_total': Document.objects.count()
    })


@login_required
@staff_required
def corpus_assign(request):
    """Bulk membership from the assign dialog: any set of files into any set of
    corpora. Additive on purpose — a file keeps the corpora it already has, so
    the dialog can never silently drop a grouping someone else curated."""
    redirect_to = request.POST.get('next', '')

    if not url_has_allowed_host_and_scheme(
        redirect_to,
        allowed_hosts={request.get_host()},
        require_https=request.is_secure()
    ):
        redirect_to = reverse('corpus_dashboard')

    if request.method != 'POST':
        return redirect(redirect_to)

    documents = list(Document.objects.filter(id__in=request.POST.getlist('documents')))
    corpora = list(Corpus.objects.filter(id__in=request.POST.getlist('corpora')))

    if not documents or not corpora:
        messages.error(request, 'Pick at least one file and at least one corpus.')
        return redirect(redirect_to)

    added = 0
    for corpus in corpora:
        already = set(corpus.documents.values_list('id', flat=True))
        new_documents = [doc for doc in documents if doc.id not in already]

        if new_documents:
            corpus.documents.add(*new_documents)
            added += len(new_documents)

    file_word = 'file' if len(documents) == 1 else 'files'
    corpus_word = 'corpus' if len(corpora) == 1 else 'corpora'

    if added:
        messages.success(
            request,
            f'Added {len(documents)} {file_word} to {len(corpora)} {corpus_word}.'
        )
    else:
        messages.info(
            request,
            f'Nothing changed — {"that file" if len(documents) == 1 else "those files"} '
            f'already belonged to every corpus you picked.'
        )

    return redirect(redirect_to)


def _document_from_upload(uploaded_file, user, title=None):
    """Builds (unsaved) a Document from an uploaded file. Raises UnicodeDecodeError
    if the file isn't UTF-8, which callers turn into a form error."""
    content = uploaded_file.read().decode('utf-8')

    return Document(
        title=title or uploaded_file.name,
        content=content,
        user=user
    )


@login_required
@staff_required
def corpus_create(request):
    if request.method == 'POST':
        form = CorpusForm(request.POST, request.FILES)

        if form.is_valid():
            try:
                new_documents = [
                    _document_from_upload(f, request.user)
                    for f in request.FILES.getlist('files')
                ]
            except UnicodeDecodeError:
                form.add_error(None, 'Uploaded files must be UTF-8 encoded.')
                return render(request, 'main/corpus_form.html', {'form': form})

            corpus = form.save(commit=False)
            corpus.created_by = request.user
            corpus.save()

            for doc in new_documents:
                doc.save()  # post_save signal builds this document's Token rows

            corpus.documents.set(list(form.cleaned_data['documents']) + new_documents)

            messages.success(
                request,
                f'Corpus "{corpus.name}" created with {corpus.documents.count()} file(s).'
            )
            return redirect('corpus_detail', corpus_id=corpus.id)
    else:
        form = CorpusForm()

    return render(request, 'main/corpus_form.html', {'form': form})


@login_required
def corpus_detail(request, corpus_id):
    corpus = get_object_or_404(Corpus, id=corpus_id)

    documents = corpus.documents.all().prefetch_related('corpora')

    sort = request.GET.get('sort', 'title')
    direction = request.GET.get('dir', 'asc' if sort == 'title' else 'desc')
    sort_fields = {'title': 'title', 'uploaded': 'uploaded_at', 'tokens': 'token_count'}
    field = sort_fields.get(sort, 'title')
    documents = documents.order_by(f"{'-' if direction == 'desc' else ''}{field}")

    totals = corpus.documents.aggregate(
        tokens=Sum('token_count'),
        tokens_corrected=Sum('token_count_corrected')
    )

    return render(request, 'main/corpus_detail.html', {
        'corpus': corpus,
        'documents': documents,
        'document_count': corpus.documents.count(),
        'token_total': totals['tokens'] or 0,
        'token_total_corrected': totals['tokens_corrected'] or 0,
        'is_selected': corpus.id in _resolve_selected_corpus_ids(request)
    })


@login_required
@staff_required
def corpus_edit(request, corpus_id):
    corpus = get_object_or_404(Corpus, id=corpus_id)

    if request.method == 'POST':
        form = CorpusForm(request.POST, request.FILES, instance=corpus)

        if form.is_valid():
            try:
                new_documents = [
                    _document_from_upload(f, request.user)
                    for f in request.FILES.getlist('files')
                ]
            except UnicodeDecodeError:
                form.add_error(None, 'Uploaded files must be UTF-8 encoded.')
                return render(request, 'main/corpus_form.html', {
                    'form': form,
                    'corpus': corpus
                })

            corpus = form.save()

            for doc in new_documents:
                doc.save()

            corpus.documents.set(list(form.cleaned_data['documents']) + new_documents)

            messages.success(request, f'Corpus "{corpus.name}" updated.')
            return redirect('corpus_detail', corpus_id=corpus.id)
    else:
        form = CorpusForm(instance=corpus)

    return render(request, 'main/corpus_form.html', {
        'form': form,
        'corpus': corpus
    })


@login_required
@staff_required
def corpus_delete(request, corpus_id):
    corpus = get_object_or_404(Corpus, id=corpus_id)

    if request.method == 'POST':
        name = corpus.name
        corpus.delete()  # only the corpus and its M2M rows; documents survive

        messages.success(request, f'Corpus "{name}" deleted. Its files were kept.')
        return redirect('corpus_dashboard')

    return render(request, 'main/corpus_confirm_delete.html', {'corpus': corpus})


# --- Analysis: corpus selection is remembered across features ---------------

SELECTED_CORPORA_SESSION_KEY = 'selected_corpus_ids'


def _resolve_selected_corpus_ids(request):
    """The selector form wins when it was submitted, otherwise fall back to
    whatever the session already remembers."""
    if request.GET.get('corpus_selection'):
        ids = [int(c) for c in request.GET.getlist('corpus') if c.isdigit()]
    else:
        ids = request.session.get(SELECTED_CORPORA_SESSION_KEY, [])

    # Drop ids of corpora that have since been deleted, so the checkboxes and
    # the session can't disagree. Applies to submitted ids too, not just the
    # remembered ones, since a stale tab can post an id that no longer exists.
    existing = set(Corpus.objects.filter(id__in=ids).values_list('id', flat=True))
    live_ids = [i for i in ids if i in existing]

    if live_ids != request.session.get(SELECTED_CORPORA_SESSION_KEY):
        request.session[SELECTED_CORPORA_SESSION_KEY] = live_ids

    return live_ids


def _get_selected_documents(request):
    ids = _resolve_selected_corpus_ids(request)

    if not ids:
        return Document.objects.none()

    # distinct() keeps a document that sits in several selected corpora from
    # being counted more than once
    return Document.objects.filter(corpora__id__in=ids).distinct()


def _analysis_context(request, documents, feature=None):
    """Context every analysis page needs for the selector bar on top."""
    selected_ids = _resolve_selected_corpus_ids(request)

    corrected = _is_corrected_mode(request)
    totals = documents.aggregate(
        tokens=Sum('token_count'),
        tokens_corrected=Sum('token_count_corrected')
    )

    return {
        'corpora': _annotated_corpora(),
        'selected_corpus_ids': selected_ids,
        'selected_corpora': Corpus.objects.filter(id__in=selected_ids),
        'selected_corpus_count': len(selected_ids),
        'corpus_total': Corpus.objects.count(),
        'document_count': documents.count(),
        # Shown in the context bar so the size of what's being analysed is
        # always on screen, in the mode actually being read.
        'selection_token_total': (
            totals['tokens_corrected'] if corrected else totals['tokens']
        ) or 0,
        'feature': feature,
        'corrected': corrected
    }


def _is_corrected_mode(request):
    return request.GET.get('corrected') == '1'


def _get_word_lists(documents, corrected):
    """One word list per document, so collocation/n-gram/KWIC windows never run
    across a document boundary. Also reports which documents fell back to flat
    tokenization, so the page can say so instead of silently degrading."""
    lists = []
    unstructured = []

    for doc in documents:
        original_words, corrected_words, structured = parse_document(doc.content)
        lists.append(corrected_words if corrected else original_words)

        if not structured:
            unstructured.append(doc.title)

    return lists, unstructured


def _require_selection(request):
    """Returns a redirect when nothing is selected yet, otherwise None."""
    if not _resolve_selected_corpus_ids(request):
        messages.info(request, 'Select at least one corpus to analyze.')
        return redirect('analysis_home')

    return None


# --- Shared result pipeline: filter -> sort -> paginate --------------------

PER_PAGE_CHOICES = [25, 50, 100, 250]
DEFAULT_PER_PAGE = 50

MATCH_MODES = [
    ('contains', 'contains'),
    ('starts', 'starts with'),
    ('ends', 'ends with'),
    ('exact', 'is exactly')
]


def _get_per_page(request):
    try:
        per_page = int(request.GET.get('per_page', DEFAULT_PER_PAGE))
    except ValueError:
        return DEFAULT_PER_PAGE

    return per_page if per_page in PER_PAGE_CHOICES else DEFAULT_PER_PAGE


def _matches(label, term, mode):
    if mode == 'starts':
        return label.startswith(term)
    if mode == 'ends':
        return label.endswith(term)
    if mode == 'exact':
        return label == term

    return term in label


def _rank_counter(request, counter, total_tokens):
    """Turns a Counter into the filtered, sorted, paginated shape every
    frequency-style table renders from. Keys may be strings or word tuples."""
    term = request.GET.get('q', '').strip().lower()
    match_mode = request.GET.get('match', 'contains')
    sort = request.GET.get('sort', 'count')
    direction = request.GET.get('dir', 'asc' if sort == 'item' else 'desc')

    rows = [
        (key if isinstance(key, str) else ' '.join(key), count)
        for key, count in counter.items()
    ]

    if term:
        rows = [row for row in rows if _matches(row[0], term, match_mode)]

    # Two passes so ties inside a count ordering stay alphabetical rather than
    # arbitrary — the same query then always renders in the same order.
    rows.sort(key=lambda row: row[0])

    if sort != 'item':
        rows.sort(key=lambda row: row[1], reverse=direction != 'asc')
    elif direction == 'desc':
        rows.reverse()

    max_count = max((count for _, count in rows), default=0)

    paginator = Paginator(rows, _get_per_page(request))
    page_obj = paginator.get_page(request.GET.get('page'))

    return {
        'rows': page_obj.object_list,
        'page_obj': page_obj,
        'paginator': paginator,
        'result_count': len(rows),
        'type_count': len(counter),
        'token_total': total_tokens,
        'max_count': max_count,
        'query': term,
        'match_mode': match_mode,
        'match_modes': MATCH_MODES,
        'sort': sort,
        'dir': direction,
        'per_page': _get_per_page(request),
        'per_page_choices': PER_PAGE_CHOICES,
        'is_filtered': bool(term)
    }


def _sorted_rows_for_export(request, counter):
    """Same ordering as the on-screen table, without pagination."""
    result = _rank_counter(request, counter, 0)

    rows = [
        (key if isinstance(key, str) else ' '.join(key), count)
        for key, count in counter.items()
    ]
    term = result['query']

    if term:
        rows = [row for row in rows if _matches(row[0], term, result['match_mode'])]

    rows.sort(key=lambda row: row[0])

    if result['sort'] != 'item':
        rows.sort(key=lambda row: row[1], reverse=result['dir'] != 'asc')
    elif result['dir'] == 'desc':
        rows.reverse()

    return rows


def _csv_response(filename, header, rows):
    response = HttpResponse(content_type='text/csv')
    response['Content-Disposition'] = f'attachment; filename="{filename}"'

    writer = csv.writer(response)
    writer.writerow(header)
    writer.writerows(rows)

    return response


def _count_words(documents, corrected, size=1):
    """Word counts (size=1), adjacent-pair counts (size=2 as tuples) or n-gram
    counts, folded per document so windows never cross a file boundary."""
    freq = Counter()
    total = 0
    word_lists, unstructured = _get_word_lists(documents, corrected)

    for words in word_lists:
        total += len(words)

        if size == 1:
            freq.update(words)
        else:
            freq.update(zip(*[words[i:] for i in range(size)]))

    return freq, total, unstructured


@login_required
def analysis_home(request):
    documents = _get_selected_documents(request)
    context = _analysis_context(request, documents, feature='home')

    totals = documents.aggregate(
        tokens=Sum('token_count'),
        tokens_corrected=Sum('token_count_corrected')
    )
    context.update({
        'token_total': totals['tokens'] or 0,
        'token_total_corrected': totals['tokens_corrected'] or 0
    })

    return render(request, 'main/analysis_home.html', context)


@login_required
def word_frequency(request):
    redirect_response = _require_selection(request)
    if redirect_response:
        return redirect_response

    documents = _get_selected_documents(request)
    corrected = _is_corrected_mode(request)

    freq, total, unstructured = _count_words(documents, corrected)

    context = _analysis_context(request, documents, feature='word_frequency')
    context.update(_rank_counter(request, freq, total))
    context['unstructured'] = unstructured

    return render(request, 'main/word_frequency.html', context)


@login_required
def word_frequency_export_csv(request):
    redirect_response = _require_selection(request)
    if redirect_response:
        return redirect_response

    documents = _get_selected_documents(request)
    freq, total, _ = _count_words(documents, _is_corrected_mode(request))
    rows = _sorted_rows_for_export(request, freq)

    return _csv_response(
        'kuis_word_frequency.csv',
        ['Word', 'Frequency', 'Per million tokens'],
        [(word, count, round(count * 1_000_000 / total, 1) if total else '')
         for word, count in rows]
    )


@login_required
def collocations(request):
    redirect_response = _require_selection(request)
    if redirect_response:
        return redirect_response

    documents = _get_selected_documents(request)
    corrected = _is_corrected_mode(request)

    freq, total, unstructured = _count_words(documents, corrected, size=2)

    context = _analysis_context(request, documents, feature='collocations')
    context.update(_rank_counter(request, freq, total))
    context['unstructured'] = unstructured

    return render(request, 'main/collocations.html', context)


@login_required
def collocations_export_csv(request):
    redirect_response = _require_selection(request)
    if redirect_response:
        return redirect_response

    documents = _get_selected_documents(request)
    freq, total, _ = _count_words(documents, _is_corrected_mode(request), size=2)
    rows = _sorted_rows_for_export(request, freq)

    return _csv_response(
        'kuis_collocations.csv',
        ['Word pair', 'Frequency', 'Per million tokens'],
        [(pair, count, round(count * 1_000_000 / total, 1) if total else '')
         for pair, count in rows]
    )


NGRAM_SIZES = [2, 3, 4, 5]


def _get_ngram_size(request):
    try:
        n = int(request.GET.get('n', 3))
    except ValueError:
        n = 3

    if n not in NGRAM_SIZES:
        n = 3

    return n


@login_required
def ngrams(request):
    redirect_response = _require_selection(request)
    if redirect_response:
        return redirect_response

    documents = _get_selected_documents(request)
    corrected = _is_corrected_mode(request)
    n = _get_ngram_size(request)

    freq, total, unstructured = _count_words(documents, corrected, size=n)

    context = _analysis_context(request, documents, feature='ngrams')
    context.update(_rank_counter(request, freq, total))
    context.update({
        'n': n,
        'ngram_sizes': NGRAM_SIZES,
        'unstructured': unstructured
    })

    return render(request, 'main/ngrams.html', context)


@login_required
def ngrams_export_csv(request):
    redirect_response = _require_selection(request)
    if redirect_response:
        return redirect_response

    documents = _get_selected_documents(request)
    n = _get_ngram_size(request)
    freq, total, _ = _count_words(documents, _is_corrected_mode(request), size=n)
    rows = _sorted_rows_for_export(request, freq)

    return _csv_response(
        f'kuis_{n}grams.csv',
        [f'{n}-gram', 'Frequency', 'Per million tokens'],
        [(gram, count, round(count * 1_000_000 / total, 1) if total else '')
         for gram, count in rows]
    )


@login_required
@staff_required
def upload_document(request):
    if request.method == 'POST':
        form = DocumentForm(request.POST, request.FILES)

        if form.is_valid():
            doc = form.save(commit=False)

            # Assign user
            doc.user = request.user

            # Handle file upload
            uploaded_file = request.FILES.get('file')

            if uploaded_file:
                try:
                    doc.content = _document_from_upload(
                        uploaded_file, request.user, title=doc.title
                    ).content
                except UnicodeDecodeError:
                    form.add_error('file', 'File must be UTF-8 encoded.')
                    return render(request, 'main/upload_document.html', {'form': form})

            doc.save()

            messages.success(
                request,
                f'"{doc.title}" uploaded. Add it to a corpus to include it in analyses.'
            )
            return redirect('corpus_dashboard')
    else:
        form = DocumentForm()

    return render(request, 'main/upload_document.html', {'form': form})


# --- Concordance -----------------------------------------------------------

KWIC_WINDOW_MIN = 1
KWIC_WINDOW_MAX = 15
KWIC_WINDOW_DEFAULT = 5


def _get_window(request):
    """Returns (window, error). An out-of-range window is clamped and reported
    rather than silently accepted or blowing up on a bad query string."""
    raw = request.GET.get('w', KWIC_WINDOW_DEFAULT)

    try:
        window = int(raw)
    except (TypeError, ValueError):
        return KWIC_WINDOW_DEFAULT, f'"{raw}" is not a valid context size — using {KWIC_WINDOW_DEFAULT}.'

    if window < KWIC_WINDOW_MIN or window > KWIC_WINDOW_MAX:
        clamped = min(max(window, KWIC_WINDOW_MIN), KWIC_WINDOW_MAX)
        return clamped, (
            f'Context size must be between {KWIC_WINDOW_MIN} and {KWIC_WINDOW_MAX} '
            f'words — using {clamped}.'
        )

    return window, None


def _kwic_results(documents, query, window, corrected):
    """Concordance lines across every selected document, each tagged with the
    document it came from."""
    results = []

    if not query:
        return results, []

    query_tokens = query.split()
    n = len(query_tokens)

    word_lists, unstructured = _get_word_lists(documents, corrected)

    for doc, words in zip(documents, word_lists):
        for i in range(len(words) - n + 1):
            if words[i:i+n] == query_tokens:
                results.append({
                    "document": doc.title,
                    "left": words[max(0, i-window):i],
                    "keyword": words[i:i+n],
                    "right": words[i+n:i+n+window]
                })

    return results, unstructured


def _sort_kwic(results, sort):
    """Concordance sorting. 'center' keeps corpus order, which is what a reader
    wants when checking passages; left/right sorting is for spotting patterns."""
    if sort == "left":
        results.sort(key=lambda x: x["left"][-1] if x["left"] else "")
    elif sort == "right":
        results.sort(key=lambda x: x["right"][0] if x["right"] else "")
    elif sort == "document":
        results.sort(key=lambda x: x["document"])

    return results


KWIC_SORTS = [
    ('center', 'Corpus order'),
    ('left', 'Word to the left'),
    ('right', 'Word to the right'),
    ('document', 'File name')
]


@login_required
def kwic(request):
    redirect_response = _require_selection(request)
    if redirect_response:
        return redirect_response

    documents = _get_selected_documents(request)

    query = request.GET.get("q", "").strip().lower()
    window, window_error = _get_window(request)
    sort = request.GET.get("sort", "center")
    corrected = _is_corrected_mode(request)

    results, unstructured = _kwic_results(documents, query, window, corrected)
    _sort_kwic(results, sort)

    paginator = Paginator(results, _get_per_page(request))
    page_obj = paginator.get_page(request.GET.get("page"))

    context = _analysis_context(request, documents, feature='kwic')
    context.update({
        "results": page_obj,
        "page_obj": page_obj,
        "paginator": paginator,
        "result_count": len(results),
        "query": query,
        "window": window,
        "window_error": window_error,
        "window_min": KWIC_WINDOW_MIN,
        "window_max": KWIC_WINDOW_MAX,
        "sort": sort,
        "kwic_sorts": KWIC_SORTS,
        "per_page": _get_per_page(request),
        "per_page_choices": PER_PAGE_CHOICES,
        "unstructured": unstructured
    })

    return render(request, "main/kwic.html", context)


@login_required
def kwic_export_csv(request):
    redirect_response = _require_selection(request)
    if redirect_response:
        return redirect_response

    documents = _get_selected_documents(request)

    query = request.GET.get("q", "").strip().lower()
    window, _ = _get_window(request)
    corrected = _is_corrected_mode(request)

    results, _unstructured = _kwic_results(documents, query, window, corrected)
    _sort_kwic(results, request.GET.get("sort", "center"))

    return _csv_response(
        'kuis_concordance.csv',
        ["Document", "Left context", "Keyword", "Right context"],
        [
            (
                row["document"],
                " ".join(row["left"]),
                " ".join(row["keyword"]),
                " ".join(row["right"])
            )
            for row in results
        ]
    )


def _word_index_matches(documents, word, mode):
    """(document_id, position) for every hit, in corpus order. Cheap to page
    over: nothing is hydrated until a page is actually rendered."""
    if not word:
        return []

    doc_ids = list(documents.values_list('id', flat=True))

    return list(
        Token.objects
        .filter(word=word, mode=mode, document_id__in=doc_ids)
        .order_by('document_id', 'position')
        .values_list('document_id', 'position')
    )


def _hydrate_word_index(refs, mode, window):
    """Builds concordance lines for the given hits with one query per document,
    rather than two per hit."""
    if not refs:
        return []

    doc_ids = {doc_id for doc_id, _ in refs}
    titles = dict(Document.objects.filter(id__in=doc_ids).values_list('id', 'title'))

    words_by_doc = {
        doc_id: list(
            Token.objects
            .filter(document_id=doc_id, mode=mode)
            .order_by('position')
            .values_list('word', flat=True)
        )
        for doc_id in doc_ids
    }

    rows = []

    for doc_id, position in refs:
        words = words_by_doc[doc_id]
        rows.append({
            'document': titles.get(doc_id, ''),
            'left': words[max(0, position - window):position],
            'keyword': words[position:position + 1],
            'right': words[position + 1:position + 1 + window]
        })

    return rows


@login_required
def kwic_search(request):
    redirect_response = _require_selection(request)
    if redirect_response:
        return redirect_response

    documents = _get_selected_documents(request)

    word = request.GET.get('word', '').lower().strip()
    window, window_error = _get_window(request)
    corrected = _is_corrected_mode(request)
    mode = Token.CORRECTED if corrected else Token.ORIGINAL

    refs = _word_index_matches(documents, word, mode)

    paginator = Paginator(refs, _get_per_page(request))
    page_obj = paginator.get_page(request.GET.get('page'))

    context = _analysis_context(request, documents, feature='kwic_search')
    context.update({
        "word": word,
        "results": _hydrate_word_index(list(page_obj.object_list), mode, window),
        "page_obj": page_obj,
        "paginator": paginator,
        "result_count": len(refs),
        "window": window,
        "window_error": window_error,
        "window_min": KWIC_WINDOW_MIN,
        "window_max": KWIC_WINDOW_MAX,
        "per_page": _get_per_page(request),
        "per_page_choices": PER_PAGE_CHOICES
    })

    return render(request, "main/kwic_search.html", context)


@login_required
def kwic_search_export_csv(request):
    redirect_response = _require_selection(request)
    if redirect_response:
        return redirect_response

    documents = _get_selected_documents(request)

    word = request.GET.get('word', '').lower().strip()
    window, _ = _get_window(request)
    mode = Token.CORRECTED if _is_corrected_mode(request) else Token.ORIGINAL

    rows = _hydrate_word_index(_word_index_matches(documents, word, mode), mode, window)

    return _csv_response(
        'kuis_word_index.csv',
        ["Document", "Left context", "Keyword", "Right context"],
        [
            (
                row["document"],
                " ".join(row["left"]),
                " ".join(row["keyword"]),
                " ".join(row["right"])
            )
            for row in rows
        ]
    )
