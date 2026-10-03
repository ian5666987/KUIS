from django.conf import settings
from django.contrib.auth.models import User
from django.core.management.base import BaseCommand, CommandError


class Command(BaseCommand):
    help = (
        "Creates the bootstrap admin and non-admin users described by the "
        "SEED_ADMIN_EMAIL/SEED_ADMIN_PASSWORD and SEED_USER_EMAIL/SEED_USER_PASSWORD "
        "entries in .env. Safe to run repeatedly: an existing user is left alone "
        "unless --update is passed."
    )

    def add_arguments(self, parser):
        parser.add_argument(
            '--update',
            action='store_true',
            help="Reset the password and permissions of users that already exist.",
        )

    def handle(self, *args, **options):
        accounts = [
            (
                settings.SEED_ADMIN_EMAIL,
                settings.SEED_ADMIN_PASSWORD,
                'admin',
                'SEED_ADMIN_EMAIL/SEED_ADMIN_PASSWORD',
                {'is_staff': True, 'is_superuser': True},
            ),
            (
                settings.SEED_USER_EMAIL,
                settings.SEED_USER_PASSWORD,
                'non-admin',
                'SEED_USER_EMAIL/SEED_USER_PASSWORD',
                {'is_staff': False, 'is_superuser': False},
            ),
        ]

        if not any(email.strip() and password for email, password, _, _, _ in accounts):
            raise CommandError(
                "Set SEED_ADMIN_EMAIL/SEED_ADMIN_PASSWORD and/or "
                "SEED_USER_EMAIL/SEED_USER_PASSWORD in .env."
            )

        for email, password, label, env_names, flags in accounts:
            email = email.strip()
            if not email or not password:
                self.stdout.write(f"Skipping {label} user: no {env_names} set.")
                continue
            self._seed_user(email, password, label, flags, options['update'])

    def _seed_user(self, email, password, label, flags, update):
        # The project uses Django's default username-based auth, so an email
        # address becomes the username's local part (user@x.com -> user).
        username = email.split('@')[0] if '@' in email else email

        user = User.objects.filter(username=username).first()

        if user is None:
            user = User.objects.create_user(username=username, email=email, password=password)
            for field, value in flags.items():
                setattr(user, field, value)
            user.save()
            self.stdout.write(self.style.SUCCESS(
                f"Created {label} user '{username}' ({email})."
            ))
            return

        if not update:
            self.stdout.write(
                f"User '{username}' already exists; nothing to do. "
                f"Use --update to reset its password and permissions."
            )
            return

        user.email = email
        user.set_password(password)
        for field, value in flags.items():
            setattr(user, field, value)
        user.save()
        self.stdout.write(self.style.SUCCESS(
            f"Updated {label} user '{username}' ({email})."
        ))
