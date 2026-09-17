from django.shortcuts import render, redirect, get_object_or_404 # redirect is needed for form
from django.http import HttpResponse
from django.contrib.auth import login
from django.contrib.auth.decorators import login_required
from django.contrib import messages
from django.core.exceptions import PermissionDenied
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
from .corpus_parsing import extract_word_streams
from collections import Counter

#For document uploading
from .forms import CorpusForm, DocumentForm

#For pagination
from django.core.paginator import Paginator

#After Postgres migrations
from django.db.models import Q

#For export to CSV
import csv

# Create your views here.
def home(request):
    return render(request, 'main/home.html')

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
    return render(request, 'main/dashboard.html')

@login_required
def profile(request):
    return render(request, 'main/profile.html')

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

    # Add Bootstrap classes
    for field in form.fields.values():
        field.widget.attrs['class'] = 'form-control'

    return render(request, 'registration/login.html', {'form': form})

class CustomLoginView(LoginView):
    template_name = 'registration/login.html'
    authentication_form = AuthenticationForm

    def get_form(self, form_class=None):
        form = super().get_form(form_class)

        # Add Bootstrap classes to fields
        for field in form.fields.values():
            field.widget.attrs['class'] = 'form-control'

        return form
    
@login_required
def corpus_dashboard(request):
    corpora = Corpus.objects.all()
    unassigned_count = Document.objects.filter(corpora__isnull=True).count()

    return render(request, 'main/corpus_dashboard.html', {
        'corpora': corpora,
        'unassigned_count': unassigned_count
    })


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

    return render(request, 'main/corpus_detail.html', {
        'corpus': corpus,
        'documents': corpus.documents.all()
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


def _analysis_context(request, documents):
    """Context every analysis page needs for the selector bar on top."""
    selected_ids = _resolve_selected_corpus_ids(request)

    return {
        'corpora': Corpus.objects.all(),
        'selected_corpus_ids': selected_ids,
        'selected_corpus_count': len(selected_ids),
        'document_count': documents.count()
    }


def _is_corrected_mode(request):
    return request.GET.get('corrected') == '1'


def _get_word_lists(documents, corrected):
    """One word list per document, so collocation/n-gram/KWIC windows never run
    across a document boundary."""
    lists = []

    for doc in documents:
        original_words, corrected_words = extract_word_streams(doc.content)
        lists.append(corrected_words if corrected else original_words)

    return lists


def _require_selection(request):
    """Returns a redirect when nothing is selected yet, otherwise None."""
    if not _resolve_selected_corpus_ids(request):
        messages.info(request, 'Select at least one corpus to analyze.')
        return redirect('analysis_home')

    return None


@login_required
def analysis_home(request):
    documents = _get_selected_documents(request)

    return render(request, 'main/analysis_home.html', _analysis_context(request, documents))


@login_required
def word_frequency(request):
    redirect_response = _require_selection(request)
    if redirect_response:
        return redirect_response

    documents = _get_selected_documents(request)
    corrected = _is_corrected_mode(request)

    freq = Counter()
    for words in _get_word_lists(documents, corrected):
        freq.update(words)

    context = _analysis_context(request, documents)
    context.update({
        'frequencies': freq.most_common(20),
        'corrected': corrected
    })

    return render(request, 'main/word_frequency.html', context)


@login_required
def collocations(request):
    redirect_response = _require_selection(request)
    if redirect_response:
        return redirect_response

    documents = _get_selected_documents(request)
    corrected = _is_corrected_mode(request)

    freq = Counter()
    for words in _get_word_lists(documents, corrected):
        freq.update(zip(words, words[1:]))

    context = _analysis_context(request, documents)
    context.update({
        'collocations': freq.most_common(20),
        'corrected': corrected
    })

    return render(request, 'main/collocations.html', context)

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

    freq = Counter()
    for words in _get_word_lists(documents, corrected):
        freq.update(zip(*[words[i:] for i in range(n)]))

    context = _analysis_context(request, documents)
    context.update({
        'ngrams': freq.most_common(20),
        'n': n,
        'ngram_sizes': NGRAM_SIZES,
        'corrected': corrected
    })

    return render(request, 'main/ngrams.html', context)

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

            return redirect('corpus_dashboard')
    else:
        form = DocumentForm()

    return render(request, 'main/upload_document.html', {'form': form})

def _kwic_results(documents, query, window, corrected):
    """Concordance lines across every selected document, each tagged with the
    document it came from."""
    results = []

    if not query:
        return results

    query_tokens = query.split()
    n = len(query_tokens)

    word_lists = _get_word_lists(documents, corrected)

    for doc, words in zip(documents, word_lists):
        for i in range(len(words) - n + 1):
            if words[i:i+n] == query_tokens:
                results.append({
                    "document": doc.title,
                    "left": words[max(0, i-window):i],
                    "keyword": words[i:i+n],
                    "right": words[i+n:i+n+window]
                })

    return results


@login_required
def kwic(request):
    redirect_response = _require_selection(request)
    if redirect_response:
        return redirect_response

    documents = _get_selected_documents(request)

    query = request.GET.get("q", "").strip().lower()
    window = int(request.GET.get("w", 5))
    sort = request.GET.get("sort", "center")
    corrected = _is_corrected_mode(request)

    results = _kwic_results(documents, query, window, corrected)

    # sorting
    if sort == "left":
        results.sort(key=lambda x: x["left"][-1] if x["left"] else "")
    elif sort == "right":
        results.sort(key=lambda x: x["right"][0] if x["right"] else "")

    # 🔥 PAGINATION ADDED HERE
    paginator = Paginator(results, 10)  # 10 KWIC lines per page

    page_number = request.GET.get("page")
    page_obj = paginator.get_page(page_number)

    context = _analysis_context(request, documents)
    context.update({
        "results": page_obj,   # 👈 now paginated
        "query": query,
        "window": window,
        "sort": sort,
        "corrected": corrected,
        "page_obj": page_obj   # important for template
    })

    return render(request, "main/kwic.html", context)

@login_required
def kwic_search(request):
    redirect_response = _require_selection(request)
    if redirect_response:
        return redirect_response

    documents = _get_selected_documents(request)

    word = request.GET.get('word', '').lower().strip()
    window = 5
    corrected = _is_corrected_mode(request)
    mode = Token.CORRECTED if corrected else Token.ORIGINAL

    results = []

    if word:
        # distinct() because the corpora join repeats a token row once per
        # selected corpus the document belongs to
        matches = Token.objects.filter(
            word=word,
            mode=mode,
            document__in=documents
        ).distinct()

        for match in matches:
            doc = match.document

            left = Token.objects.filter(
                document=doc,
                mode=mode,
                position__gte=match.position - window,
                position__lt=match.position
            )

            right = Token.objects.filter(
                document=doc,
                mode=mode,
                position__gt=match.position,
                position__lte=match.position + window
            )

            context = list(left) + [match] + list(right)

            results.append({
                "document": doc.title,
                "context": [t.word for t in context]
            })

    context = _analysis_context(request, documents)
    context.update({
        "word": word,
        "results": results,
        "corrected": corrected
    })

    return render(request, "main/kwic_search.html", context)

@login_required
def kwic_export_csv(request):
    redirect_response = _require_selection(request)
    if redirect_response:
        return redirect_response

    documents = _get_selected_documents(request)

    query = request.GET.get("q", "").strip().lower()
    window = int(request.GET.get("w", 5))
    corrected = _is_corrected_mode(request)

    results = _kwic_results(documents, query, window, corrected)

    # CSV response
    response = HttpResponse(content_type='text/csv')
    response['Content-Disposition'] = 'attachment; filename="kwic_export.csv"'

    writer = csv.writer(response)
    writer.writerow(["Document", "Left Context", "Keyword", "Right Context"])

    for row in results:
        writer.writerow([
            row["document"],
            " ".join(row["left"]),
            " ".join(row["keyword"]),
            " ".join(row["right"])
        ])

    return response

# def kwic(request, doc_id):
#     doc = Document.objects.get(id=doc_id)
#     documents = Document.objects.all()

#     query = request.GET.get("q", "").strip().lower()
#     window = int(request.GET.get("w", 5))
#     sort = request.GET.get("sort", "center")

#     results = []

#     if query:
#         words = re.findall(r"\b\w+\b", doc.content.lower())

#         query_tokens = query.split()  # supports phrases

#         n = len(query_tokens)

#         for i in range(len(words) - n + 1):
#             if words[i:i+n] == query_tokens:

#                 left = words[max(0, i-window):i]
#                 right = words[i+n:i+n+window]

#                 results.append({
#                     "left": left,
#                     "keyword": words[i:i+n],
#                     "right": right
#                 })

#         # Sorting options
#         if sort == "left":
#             results.sort(key=lambda x: x["left"][-1] if x["left"] else "")
#         elif sort == "right":
#             results.sort(key=lambda x: x["right"][0] if x["right"] else "")

#     return render(request, "main/kwic.html", {
#         "document": doc,
#         "documents": documents,
#         "results": results,
#         "query": query,
#         "window": window,
#         "sort": sort   # 🔥 VERY IMPORTANT (missing this causes UI mismatch)
#     })