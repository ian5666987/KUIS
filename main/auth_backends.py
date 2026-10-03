# Lets people sign in with either their username or the email on their account.

from django.contrib.auth import get_user_model
from django.contrib.auth.backends import ModelBackend
from django.db.models import Q


class UsernameOrEmailBackend(ModelBackend):
    """Authenticates against the username field or, failing that, the email.

    Django's default User model does not enforce unique emails, so an address
    shared by several accounts is rejected rather than guessed at. A username
    match always wins over an email match for the same string.
    """

    def authenticate(self, request, username=None, password=None, **kwargs):
        User = get_user_model()

        if username is None:
            username = kwargs.get(User.USERNAME_FIELD)

        if username is None or password is None:
            return None

        candidates = list(User._default_manager.filter(
            Q(**{User.USERNAME_FIELD: username}) | Q(email__iexact=username)
        )[:11])

        if len(candidates) > 1:
            named = [
                user for user in candidates
                if getattr(user, User.USERNAME_FIELD) == username
            ]
            # Anything still ambiguous is an email several accounts share: there is
            # no safe way to pick one, so treat it as a failed login.
            candidates = named if len(named) == 1 else []

        if not candidates:
            # Same work as a real check, so a missing account is not detectable
            # from how quickly the response comes back.
            User().set_password(password)
            return None

        user = candidates[0]

        if user.check_password(password) and self.user_can_authenticate(user):
            return user

        return None
