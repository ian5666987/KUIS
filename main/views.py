from django.shortcuts import render, redirect # redirect is needed for form
from django.http import HttpResponse
from django.contrib.auth import login
from django.contrib.auth.decorators import login_required
from .forms import RegisterForm #this is taking from the same-folder 'forms' file

#For contact
from django.core.mail import send_mail 
from django.conf import settings
from .forms import ContactForm

#For login and authentication
from django.contrib.auth.forms import AuthenticationForm
from django.contrib.auth.views import LoginView

#For corpus functionalities
from .models import Token, Document
import re
from collections import Counter

#For document uploading
from .forms import DocumentForm

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

def contact(request):
    return render(request, 'main/contact.html')

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
    documents = Document.objects.all()
    return render(request, 'main/corpus_dashboard.html', {
        'documents': documents
    })

@login_required
def word_frequency(request, doc_id):
    doc = Document.objects.get(id=doc_id)

    words = re.findall(r'\w+', doc.content.lower())
    freq = Counter(words)

    most_common = freq.most_common(20)

    return render(request, 'main/word_frequency.html', {
        'document': doc,
        'frequencies': most_common
    })

@login_required
def collocations(request, doc_id):
    doc = Document.objects.get(id=doc_id)

    words = re.findall(r'\w+', doc.content.lower())

    pairs = zip(words, words[1:])
    freq = Counter(pairs)

    most_common = freq.most_common(20)

    return render(request, 'main/collocations.html', {
        'document': doc,
        'collocations': most_common
    })

@login_required
def ngrams(request, doc_id, n=3):
    doc = Document.objects.get(id=doc_id)

    words = re.findall(r'\w+', doc.content.lower())

    ngrams_list = zip(*[words[i:] for i in range(n)])
    freq = Counter(ngrams_list)

    most_common = freq.most_common(20)

    return render(request, 'main/ngrams.html', {
        'document': doc,
        'ngrams': most_common,
        'n': n
    })

@login_required
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
                    content = uploaded_file.read().decode('utf-8')
                except UnicodeDecodeError:
                    form.add_error('file', 'File must be UTF-8 encoded.')
                    return render(request, 'main/upload_document.html', {'form': form})

                doc.content = content

            doc.save()

            return redirect('corpus_dashboard')
    else:
        form = DocumentForm()

    return render(request, 'main/upload_document.html', {'form': form})

@login_required
def kwic_legacy(request, doc_id):
    doc = Document.objects.get(id=doc_id)
    documents = Document.objects.all()

    query = request.GET.get("q", "").strip().lower()
    window = int(request.GET.get("w", 5))
    sort = request.GET.get("sort", "center")

    results = []

    if query:
        words = re.findall(r"\b\w+\b", doc.content.lower())
        query_tokens = query.split()
        n = len(query_tokens)

        for i in range(len(words) - n + 1):
            if words[i:i+n] == query_tokens:
                left = words[max(0, i-window):i]
                right = words[i+n:i+n+window]

                results.append({
                    "left": left,
                    "keyword": words[i:i+n],
                    "right": right
                })

        # sorting
        if sort == "left":
            results.sort(key=lambda x: x["left"][-1] if x["left"] else "")
        elif sort == "right":
            results.sort(key=lambda x: x["right"][0] if x["right"] else "")

    # 🔥 PAGINATION ADDED HERE
    paginator = Paginator(results, 10)  # 10 KWIC lines per page

    page_number = request.GET.get("page")
    page_obj = paginator.get_page(page_number)

    return render(request, "main/kwic_legacy.html", {
        "document": doc,
        "documents": documents,
        "results": page_obj,   # 👈 now paginated
        "query": query,
        "window": window,
        "sort": sort,
        "page_obj": page_obj   # important for template
    })

@login_required
def kwic_token(request, doc_id=None):
    word = request.GET.get('word', '').lower().strip()
    window = 5  # context size

    results = []

    # doc = Document.objects.get(id=doc_id)

    # if not word:
    #     return render(request, 'kwic.html', {'results': []})

    if word:
        # 1. Find all matching tokens (FAST - indexed)
        if doc_id:
            matches = Token.objects.filter(document_id=doc_id, word=word)
        else:
            matches = Token.objects.filter(word=word)

        for match in matches:
            doc = match.document

            # 2. Get surrounding tokens (FAST indexed query)
            left = Token.objects.filter(
                document=doc,
                position__gte=match.position - window,
                position__lt=match.position
            )

            right = Token.objects.filter(
                document=doc,
                position__gt=match.position,
                position__lte=match.position + window
            )

            context = list(left) + [match] + list(right)

            context_words = [t.word for t in context]

            center_index = len(left)

            context_words[center_index] = f"[{context_words[center_index].upper()}]"

            results.append({
                "document": doc.title,
                "context": context_words,
                "position": match.position
            })

        return render(request, 'main/kwic.html', {
            'document': doc,
            'word': word,
            'results': results
        })
    
def kwic_search(request):
    word = request.GET.get('word', '').lower().strip()
    window = 5

    results = []

    if word:
        matches = Token.objects.filter(word=word)

        for match in matches:
            doc = match.document

            left = Token.objects.filter(
                document=doc,
                position__gte=match.position - window,
                position__lt=match.position
            )

            right = Token.objects.filter(
                document=doc,
                position__gt=match.position,
                position__lte=match.position + window
            )

            context = list(left) + [match] + list(right)

            results.append({
                "document": doc.title,
                "context": [t.word for t in context]
            })

    return render(request, "main/kwic_search.html", {
        "word": word,
        "results": results
    })    

@login_required
def kwic_export_csv(request, doc_id):
    doc = Document.objects.get(id=doc_id)

    query = request.GET.get("q", "").strip().lower()
    window = int(request.GET.get("w", 5))

    results = []

    if query:
        words = re.findall(r"\b\w+\b", doc.content.lower())
        query_tokens = query.split()
        n = len(query_tokens)

        for i in range(len(words) - n + 1):
            if words[i:i+n] == query_tokens:
                left = words[max(0, i-window):i]
                right = words[i+n:i+n+window]

                results.append([
                    " ".join(left),
                    " ".join(words[i:i+n]),
                    " ".join(right)
                ])

    # CSV response
    response = HttpResponse(content_type='text/csv')
    response['Content-Disposition'] = f'attachment; filename="kwic_{doc.title}.csv"'

    writer = csv.writer(response)
    writer.writerow(["Left Context", "Keyword", "Right Context"])

    for row in results:
        writer.writerow(row)

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