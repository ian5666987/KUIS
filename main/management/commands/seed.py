from django.conf import settings
from django.contrib.auth.models import User
from django.core.management.base import BaseCommand, CommandError


ADMIN = 'admin'
STAFF = 'staff'


class Command(BaseCommand):
    help = (
        "Creates the bootstrap user described by the SEED_USER, SEED_PASSWORD and "
        "SEED_USER_TYPE entries in .env. Safe to run repeatedly: an existing user is "
        "left alone unless --update is passed."
    )

    def add_arguments(self, parser):
        parser.add_argument(
            '--update',
            action='store_true',
            help="Reset the password and permissions of the user if it already exists.",
        )

    def handle(self, *args, **options):
        email = settings.SEED_USER.strip()
        password = settings.SEED_PASSWORD
        user_type = settings.SEED_USER_TYPE.strip().lower()

        if not email or not password:
            raise CommandError("SEED_USER and SEED_PASSWORD must be set in .env.")

        # The project uses Django's default username-based auth, so an email address
        # in SEED_USER becomes the username's local part (admin@admin.com -> admin).
        username = email.split('@')[0] if '@' in email else email

        is_superuser = user_type == ADMIN
        is_staff = user_type in (ADMIN, STAFF)

        user = User.objects.filter(username=username).first()

        if user is None:
            create = User.objects.create_superuser if is_superuser else User.objects.create_user
            user = create(username=username, email=email, password=password)
            user.is_staff = is_staff
            user.save(update_fields=['is_staff'])
            self.stdout.write(self.style.SUCCESS(
                f"Created {user_type} user '{username}' ({email})."
            ))
            return

        if not options['update']:
            self.stdout.write(
                f"User '{username}' already exists; nothing to do. "
                f"Use --update to reset its password and permissions."
            )
            return

        user.email = email
        user.is_staff = is_staff
        user.is_superuser = is_superuser
        user.set_password(password)
        user.save()
        self.stdout.write(self.style.SUCCESS(
            f"Updated {user_type} user '{username}' ({email})."
        ))
