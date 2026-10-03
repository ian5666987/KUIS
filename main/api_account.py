"""
Account endpoints for the Next.js frontend (architecture plan §6, KUIS-FE
rebuild): self-registration and the contact form, both previously
server-rendered-only, plus a small profile-stats endpoint mirroring what
main/templates/main/profile.html shows.

Register and contact reuse main/forms.py's RegisterForm/ContactForm
directly rather than reimplementing their validation (password rules,
unique-email check) in a DRF serializer — these are public, shared form
classes (not views.py's own underscore-prefixed internals), so importing
them here carries none of the "reaching into a different module's private
helpers" risk main/api_corpus.py's module docstring warns about for that
module's own duplicated helpers.
"""

from django.conf import settings
from django.core.mail import send_mail
from rest_framework import status
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from .api_auth import KUISTokenObtainPairSerializer
from .forms import ContactForm, RegisterForm
from .models import Corpus, Document


def _normalize_form_errors(form):
    """Django's Form.errors uses '__all__' for non-field errors; DRF's own
    convention (already established by CorpusWriteSerializer) is
    'non_field_errors' — normalized here so KUIS-FE's error handling
    doesn't need two different shapes for form-backed vs serializer-backed
    endpoints."""
    errors = dict(form.errors)
    if "__all__" in errors:
        errors["non_field_errors"] = errors.pop("__all__")
    return errors


class RegisterView(APIView):
    """POST /api/auth/register/ — mirrors main/views.py::register exactly
    (same form, same validation). Returns the same {access, refresh} token
    pair shape /api/auth/token/ does, so KUIS-FE can auto-login on success
    exactly like the session-cookie register view does today."""

    def post(self, request):
        form = RegisterForm(data=request.data)

        if not form.is_valid():
            return Response(_normalize_form_errors(form), status=status.HTTP_400_BAD_REQUEST)

        user = form.save()
        token = KUISTokenObtainPairSerializer.get_token(user)

        return Response(
            {"access": str(token.access_token), "refresh": str(token)},
            status=status.HTTP_201_CREATED,
        )


class ContactView(APIView):
    """POST /api/contact/ — mirrors main/views.py::contact exactly: same
    validation via ContactForm, same outgoing email."""

    def post(self, request):
        form = ContactForm(data=request.data)

        if not form.is_valid():
            return Response(_normalize_form_errors(form), status=status.HTTP_400_BAD_REQUEST)

        name = form.cleaned_data["name"]
        email = form.cleaned_data["email"]
        message = form.cleaned_data["message"]

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

        return Response(status=status.HTTP_204_NO_CONTENT)


class ProfileView(APIView):
    """GET /api/profile/ — mirrors main/templates/main/profile.html: the
    signed-in user's username/email/access level plus the two counts that
    template shows (files they uploaded, corpora they created)."""

    permission_classes = [IsAuthenticated]

    def get(self, request):
        user = request.user
        return Response({
            "username": user.username,
            "email": user.email,
            "is_staff": user.is_staff,
            "documents_uploaded": Document.objects.filter(user=user).count(),
            "corpora_created": Corpus.objects.filter(created_by=user).count(),
        })
