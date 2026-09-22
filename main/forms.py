# This file is added to facility various forms in the application

from django import forms
from django.contrib.auth.forms import AuthenticationForm, UserCreationForm
from django.contrib.auth.models import User


class LoginForm(AuthenticationForm):
    """Sign-in accepts either identifier, so the field is labelled and sized for
    both: the inherited one caps at the 150-character username limit."""

    error_messages = {
        **AuthenticationForm.error_messages,
        'invalid_login': "That username or email and password don't match an account.",
    }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)

        self.fields['username'].label = "Username or email"
        self.fields['username'].max_length = 254
        self.fields['username'].widget.attrs.update({
            'maxlength': 254,
            'autocomplete': 'username',
        })

class RegisterForm(UserCreationForm):
    email = forms.EmailField(
        required=True,
        widget=forms.EmailInput(attrs={'class': 'field'})
    )

    class Meta:
        model = User
        fields = ["username", "email", "password1", "password2"]

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)

        for field in self.fields.values():
            field.widget.attrs['class'] = 'field'

    def clean_email(self):
        # Emails are a sign-in identifier, so they have to pick out one account.
        email = self.cleaned_data['email']

        if User.objects.filter(email__iexact=email).exists():
            raise forms.ValidationError("An account already uses this email address.")

        return email

class ContactForm(forms.Form):
    name = forms.CharField(
        max_length=100,
        widget=forms.TextInput(attrs={'class': 'field'})
    )
    email = forms.EmailField(
        widget=forms.EmailInput(attrs={'class': 'field'})
    )
    message = forms.CharField(
        widget=forms.Textarea(attrs={'class': 'field', 'rows': 4})
    )

from .models import Corpus, Document

class CorpusForm(forms.ModelForm):
    documents = forms.ModelMultipleChoiceField(
        queryset=Document.objects.all(),
        required=False,
        widget=forms.CheckboxSelectMultiple
    )

    class Meta:
        model = Corpus
        fields = ['name', 'description', 'documents']

        widgets = {
            'name': forms.TextInput(attrs={'class': 'field'}),
            'description': forms.Textarea(attrs={'class': 'field', 'rows': 3}),
        }

    def clean(self):
        cleaned_data = super().clean()
        documents = cleaned_data.get('documents')
        # New files arrive as request.FILES.getlist('files'), handled in the view,
        # because Django's FileField only ever cleans a single file.
        uploaded_files = self.files.getlist('files') if self.files else []

        if not documents and not uploaded_files:
            raise forms.ValidationError(
                "Select at least one existing file or upload a new one."
            )

        return cleaned_data

class DocumentForm(forms.ModelForm):
    file = forms.FileField(required=False)

    class Meta:
        model = Document
        fields = ['title', 'content']

        widgets = {
            'title': forms.TextInput(attrs={'class': 'field'}),
            'content': forms.Textarea(attrs={'class': 'field', 'rows': 6}),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields['content'].required = False  # So that one that provides a file need not to fill the text to upload

    def clean(self):
        cleaned_data = super().clean()
        content = cleaned_data.get('content')
        file = self.files.get('file')

        if not content and not file:
            raise forms.ValidationError("You must provide either text or a file.")

        return cleaned_data        