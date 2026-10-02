"""Smoke tests for the Corpus Explorer interface.

They exercise the parts of the redesign that are easy to break silently: every
page rendering, the corpus selection surviving navigation, the text-mode toggle
changing what is counted, and the table controls (filter, sort, paginate,
export) agreeing with each other.
"""

import json
from io import StringIO

from django.contrib.auth.models import User
from django.core import mail
from django.core.files.uploadedfile import SimpleUploadedFile
from django.contrib.messages import get_messages
from django.core.management import call_command
from django.test import TestCase
from django.urls import reverse

from .models import Corpus, Document, UserProfile
from worker.tasks.indexing import index_document

CORPUS_XML = """<document>
  <header><textfile>sample</textfile><lang>indonesian</lang></header>
  <body>
    saya suka makan nasi
    <segment id='1' features='eror;leksikal' Correction='tetapi'>tapi</segment>
    saya tidak suka nasi goreng
  </body>
</document>"""

PLAIN_TEXT = "saya suka nasi dan saya suka teh"

# A <segment> nested inside another <segment> — the data's way of
# representing overlapping errors (docs/error-analytics-plan.md), modeled
# after the real pattern in info/KUIS2023FUA201-Eror.xml. Outer segment
# uses capital 'Correction' (the one rule the corrected token stream has
# always honored); nested segment has no parent= attribute, mirroring real
# sample data where the explicit attribute and structural nesting diverge.
NESTED_CORPUS_XML = """<document>
  <header><textfile>t</textfile><lang>indonesian</lang></header>
  <body>
saya suka <segment id='3' features='eror;gramatikal;frasa-nomina;urtfn' Correction='nasi goreng'>goreng <segment id='1' features='eror;ejaan;ejk'>nasi</segment></segment> sekali
  </body>
</document>"""

# Same nested shape, but the outer segment's correction is spelled with a
# lowercase attribute — real data uses both castings on sibling segments.
# The token corrected-stream only ever reads capital 'Correction' (an
# already-known, deliberately-unchanged quirk); ErrorAnnotation.correction_
# text must still resolve it, since that's display text, not token
# generation.
LOWERCASE_CORRECTION_XML = """<document>
  <header><textfile>t</textfile><lang>indonesian</lang></header>
  <body>
Sayang <segment id='3' features='eror;gramatikal;frasa-nomina;urtfn' correction='Tuti sayang'>tuti</segment>
  </body>
</document>"""


class ExplorerTestCase(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.user = User.objects.create_user('researcher', password='pw-for-tests-1')
        cls.staff = User.objects.create_user('curator', password='pw-for-tests-1', is_staff=True)

        cls.annotated = Document.objects.create(title='annotated.xml', content=CORPUS_XML)
        cls.plain = Document.objects.create(title='plain.txt', content=PLAIN_TEXT)
        # Tokenization is no longer automatic on save (architecture plan
        # §5 removed the post_save signal) — .delay() runs synchronously
        # here because CELERY_TASK_ALWAYS_EAGER=1 is set for this test run
        # (see docs/ONBOARDING.md's test command), exercising the real
        # indexing task rather than a hand-rolled test-only setup path.
        index_document.delay(cls.annotated.id)
        index_document.delay(cls.plain.id)
        cls.annotated.refresh_from_db()
        cls.plain.refresh_from_db()

        cls.corpus = Corpus.objects.create(name='Written 2023', description='Essays')
        cls.corpus.documents.set([cls.annotated, cls.plain])

        cls.other = Corpus.objects.create(name='Spoken 2024')
        cls.other.documents.set([cls.plain])   # overlaps on purpose

    def setUp(self):
        self.client.force_login(self.user)

    def select(self, *corpora):
        """Apply a corpus selection the way the context bar does."""
        return self.client.get(
            reverse('analysis_home'),
            {'corpus_selection': '1', 'corpus': [c.id for c in corpora]}
        )


class PagesRenderTests(ExplorerTestCase):
    def test_public_and_account_pages_render(self):
        self.client.logout()

        for name in ['home', 'about', 'contact', 'login', 'register']:
            with self.subTest(page=name):
                self.assertEqual(self.client.get(reverse(name)).status_code, 200)

    def test_researcher_pages_render(self):
        self.select(self.corpus)

        for name in ['dashboard', 'profile', 'corpus_dashboard', 'analysis_home',
                     'word_frequency', 'collocations', 'ngrams', 'kwic', 'kwic_search']:
            with self.subTest(page=name):
                self.assertEqual(self.client.get(reverse(name)).status_code, 200)

        self.assertEqual(
            self.client.get(reverse('corpus_detail', args=[self.corpus.id])).status_code, 200
        )

    def test_curation_pages_are_staff_only(self):
        for name in ['corpus_create', 'upload_document']:
            with self.subTest(page=name):
                self.assertEqual(self.client.get(reverse(name)).status_code, 403)

        self.client.force_login(self.staff)

        for name in ['corpus_create', 'upload_document']:
            with self.subTest(page=name, role='staff'):
                self.assertEqual(self.client.get(reverse(name)).status_code, 200)


class ThemeTests(ExplorerTestCase):
    def test_theme_switch_renders_and_defaults_to_light(self):
        html = self.client.get(reverse('dashboard')).content.decode()

        # No data-theme attribute on <html> means the light :root tokens stand;
        # dark is only ever applied by the reader's stored choice.
        self.assertIn('data-theme-switch', html)
        self.assertIn('<button type="button" data-theme="light" aria-pressed="true">', html)
        self.assertNotIn('<html lang="en" data-theme', html)


class SelectionTests(ExplorerTestCase):
    def test_selection_survives_navigation_without_url_params(self):
        self.select(self.corpus)

        response = self.client.get(reverse('word_frequency'))

        self.assertEqual(response.context['selected_corpus_count'], 1)
        self.assertEqual(response.context['document_count'], 2)

    def test_selection_can_be_cleared(self):
        self.select(self.corpus)
        self.client.get(reverse('analysis_home'), {'corpus_selection': '1'})

        # With nothing selected, features bounce back to the hub rather than
        # silently reporting an analysis of nothing.
        self.assertRedirects(self.client.get(reverse('kwic')), reverse('analysis_home'))

    def test_overlapping_corpora_count_each_file_once(self):
        self.select(self.corpus, self.other)

        response = self.client.get(reverse('word_frequency'))

        self.assertEqual(response.context['document_count'], 2)


class TextModeTests(ExplorerTestCase):
    def test_corrected_mode_substitutes_the_correction(self):
        self.select(self.corpus)

        original = self.client.get(reverse('word_frequency'), {'q': 'tapi', 'match': 'exact'})
        corrected = self.client.get(
            reverse('word_frequency'), {'q': 'tetapi', 'match': 'exact', 'corrected': '1'}
        )

        self.assertEqual(list(original.context['rows']), [('tapi', 1)])
        self.assertEqual(list(corrected.context['rows']), [('tetapi', 1)])

    def test_unannotated_files_are_reported_in_corrected_mode(self):
        self.select(self.corpus)

        response = self.client.get(reverse('word_frequency'), {'corrected': '1'})

        self.assertIn('plain.txt', response.context['unstructured'])
        self.assertContains(response, 'carry no segment annotations')


class ResultTableTests(ExplorerTestCase):
    def setUp(self):
        super().setUp()
        self.select(self.corpus)

    def test_filter_narrows_rows_and_reports_the_total(self):
        response = self.client.get(reverse('word_frequency'), {'q': 'nasi', 'match': 'exact'})

        self.assertTrue(response.context['is_filtered'])
        self.assertEqual(response.context['result_count'], 1)
        self.assertLess(response.context['result_count'], response.context['type_count'])

    def test_sorting_by_item_is_alphabetical(self):
        response = self.client.get(reverse('word_frequency'), {'sort': 'item', 'dir': 'asc'})

        words = [word for word, _ in response.context['rows']]
        self.assertEqual(words, sorted(words))

    def test_default_sort_is_frequency_descending(self):
        response = self.client.get(reverse('word_frequency'))

        counts = [count for _, count in response.context['rows']]
        self.assertEqual(counts, sorted(counts, reverse=True))

    def test_pagination_splits_the_result_set(self):
        response = self.client.get(reverse('word_frequency'), {'per_page': '25', 'page': '1'})

        self.assertEqual(response.context['page_obj'].number, 1)
        self.assertEqual(response.context['per_page'], 25)

    def test_ngram_size_changes_the_rows(self):
        response = self.client.get(reverse('ngrams'), {'n': '4'})

        self.assertEqual(response.context['n'], 4)
        self.assertTrue(all(len(item.split()) == 4 for item, _ in response.context['rows']))

    def test_invalid_ngram_size_falls_back_instead_of_failing(self):
        response = self.client.get(reverse('ngrams'), {'n': 'nine'})

        self.assertEqual(response.context['n'], 3)


class ConcordanceTests(ExplorerTestCase):
    def setUp(self):
        super().setUp()
        self.select(self.corpus)

    def test_phrase_search_returns_lines_with_context(self):
        response = self.client.get(reverse('kwic'), {'q': 'suka nasi'})

        # Once in each file; lines are never joined across a file boundary.
        self.assertEqual(response.context['result_count'], 2)
        line = response.context['results'][0]
        self.assertEqual(line['keyword'], ['suka', 'nasi'])
        self.assertIn('saya', line['left'])

    def test_out_of_range_window_is_clamped_and_explained(self):
        response = self.client.get(reverse('kwic'), {'q': 'nasi', 'w': '99'})

        self.assertEqual(response.context['window'], 10)
        self.assertIsNotNone(response.context['window_error'])
        self.assertContains(response, 'Context size must be between')

    def test_empty_query_shows_a_prompt_not_an_error(self):
        response = self.client.get(reverse('kwic'))

        self.assertContains(response, 'Enter a search term')

    def test_word_index_returns_the_same_shape_as_kwic(self):
        response = self.client.get(reverse('kwic_search'), {'word': 'nasi'})

        self.assertEqual(response.context['result_count'], 3)
        self.assertEqual(response.context['results'][0]['keyword'], ['nasi'])


class ExportTests(ExplorerTestCase):
    def setUp(self):
        super().setUp()
        self.select(self.corpus)

    def test_every_analysis_exports_csv(self):
        exports = {
            'word_frequency_export_csv': {},
            'collocations_export_csv': {},
            'ngrams_export_csv': {'n': '2'},
            'kwic_export_csv': {'q': 'nasi'},
            'kwic_search_export_csv': {'word': 'nasi'}
        }

        for name, params in exports.items():
            with self.subTest(export=name):
                response = self.client.get(reverse(name), params)

                self.assertEqual(response.status_code, 200)
                self.assertEqual(response['Content-Type'], 'text/csv')
                self.assertIn('attachment;', response['Content-Disposition'])

    def test_export_applies_the_same_filter_as_the_table(self):
        response = self.client.get(
            reverse('word_frequency_export_csv'), {'q': 'nasi', 'match': 'exact'}
        )

        body = response.content.decode()
        self.assertEqual(len(body.strip().splitlines()), 2)   # header + one row
        self.assertIn('nasi', body)


class LoginIdentifierTests(TestCase):
    """Sign-in accepts the username or the email on the account."""

    @classmethod
    def setUpTestData(cls):
        cls.password = 'pw-for-tests-1'
        cls.user = User.objects.create_user(
            'researcher', email='Researcher@Example.com', password=cls.password
        )

    def post_login(self, identifier, password=None):
        return self.client.post(reverse('login'), {
            'username': identifier,
            'password': self.password if password is None else password,
        })

    def assertLoggedInAs(self, user):
        session_user = self.client.session.get('_auth_user_id')
        self.assertEqual(session_user, str(user.pk))

    def test_username_still_works(self):
        self.post_login('researcher')
        self.assertLoggedInAs(self.user)

    def test_email_is_accepted(self):
        self.post_login('Researcher@Example.com')
        self.assertLoggedInAs(self.user)

    def test_email_match_ignores_case(self):
        self.post_login('researcher@example.com')
        self.assertLoggedInAs(self.user)

    def test_wrong_password_is_rejected(self):
        response = self.post_login('researcher@example.com', password='not-the-password')
        self.assertIsNone(self.client.session.get('_auth_user_id'))
        self.assertContains(response, 'don', status_code=200)

    def test_unknown_identifier_is_rejected(self):
        self.post_login('nobody@example.com')
        self.assertIsNone(self.client.session.get('_auth_user_id'))

    def test_a_username_beats_someone_elses_email(self):
        # One account is named after the address another account uses as its email.
        namesake = User.objects.create_user('shared@example.com', password=self.password)
        User.objects.create_user('other', email='shared@example.com', password=self.password)

        self.post_login('shared@example.com')
        self.assertLoggedInAs(namesake)

    def test_an_email_shared_by_two_accounts_is_refused(self):
        # Legacy rows can still share an address; there is no safe way to pick one.
        User.objects.filter(pk=self.user.pk).update(email='shared@example.com')
        User.objects.create_user('duplicate', email='shared@example.com', password=self.password)

        self.post_login('shared@example.com')
        self.assertIsNone(self.client.session.get('_auth_user_id'))

    def test_registration_rejects_an_email_already_in_use(self):
        response = self.client.post(reverse('register'), {
            'username': 'newcomer',
            'email': 'researcher@example.com',
            'password1': 'pw-for-tests-1',
            'password2': 'pw-for-tests-1',
        })

        self.assertEqual(response.status_code, 200)
        self.assertFalse(User.objects.filter(username='newcomer').exists())


class JWTAuthTests(TestCase):
    """The additive JWT endpoints for KUIS-FE / FastAPI (architecture plan
    §3, main/api_auth.py). The existing session-cookie login above must stay
    unaffected by any of this — see test_session_login_is_unaffected."""

    @classmethod
    def setUpTestData(cls):
        cls.password = 'pw-for-tests-1'
        cls.user = User.objects.create_user(
            'researcher', email='researcher@example.com', password=cls.password, is_staff=True
        )

    def obtain_tokens(self, username='researcher', password=None):
        response = self.client.post(
            reverse('token_obtain_pair'),
            data=json.dumps({'username': username, 'password': password or self.password}),
            content_type='application/json',
        )
        return response, (response.json() if response.status_code == 200 else None)

    def test_obtain_token_rejects_wrong_password(self):
        response, _ = self.obtain_tokens(password='not-the-password')
        self.assertEqual(response.status_code, 401)

    def test_obtain_token_accepts_username_or_email(self):
        # UsernameOrEmailBackend (main/auth_backends.py) runs unmodified
        # under TokenObtainPairView — same identifier rules as session login.
        by_username, _ = self.obtain_tokens('researcher')
        by_email, _ = self.obtain_tokens('researcher@example.com')

        self.assertEqual(by_username.status_code, 200)
        self.assertEqual(by_email.status_code, 200)

    def test_access_token_carries_expected_claims(self):
        import jwt as pyjwt
        from django.conf import settings

        _, body = self.obtain_tokens()
        claims = pyjwt.decode(body['access'], options={'verify_signature': False})

        self.assertEqual(claims['token_type'], 'access')
        self.assertEqual(claims['user_id'], str(self.user.pk))
        self.assertEqual(claims['username'], 'researcher')
        self.assertEqual(claims['email'], 'researcher@example.com')
        self.assertIs(claims['is_staff'], True)
        # FastAPI's dataplane/core/security.py verifies with this same
        # signing key — the only secret this repo shares with the FastAPI
        # process. A stale/mismatched JWT_SECRET would surface here first.
        self.assertTrue(settings.SIMPLE_JWT['SIGNING_KEY'])

    def test_refresh_rotates_and_blacklists_the_old_token(self):
        _, body = self.obtain_tokens()

        first_refresh = self.client.post(
            reverse('token_refresh'),
            data=json.dumps({'refresh': body['refresh']}),
            content_type='application/json',
        )
        self.assertEqual(first_refresh.status_code, 200)
        self.assertIn('refresh', first_refresh.json())  # ROTATE_REFRESH_TOKENS=True

        # Reusing the now-rotated-away original refresh token must fail —
        # BLACKLIST_AFTER_ROTATION=True. KUIS-FE's refresh route handler
        # depends on this to know when to fall back to a real re-login.
        reused = self.client.post(
            reverse('token_refresh'),
            data=json.dumps({'refresh': body['refresh']}),
            content_type='application/json',
        )
        self.assertEqual(reused.status_code, 401)

    def test_blacklist_prevents_further_refresh(self):
        _, body = self.obtain_tokens()

        blacklist = self.client.post(
            reverse('token_blacklist'),
            data=json.dumps({'refresh': body['refresh']}),
            content_type='application/json',
        )
        self.assertEqual(blacklist.status_code, 200)

        refresh_after_logout = self.client.post(
            reverse('token_refresh'),
            data=json.dumps({'refresh': body['refresh']}),
            content_type='application/json',
        )
        self.assertEqual(refresh_after_logout.status_code, 401)

    def test_session_login_is_unaffected(self):
        """Adding rest_framework/SimpleJWT must not touch the server-rendered
        pages' own session-cookie auth (config/urls.py's accounts/login/)."""
        response = self.client.post(reverse('login'), {
            'username': 'researcher',
            'password': self.password,
        })
        self.assertEqual(response.status_code, 302)
        self.assertEqual(self.client.session.get('_auth_user_id'), str(self.user.pk))


class CorpusVisibilityTests(ExplorerTestCase):
    """Corpus.is_public — the admin-only public/private sharing toggle
    (main/models.py). Both live surfaces enforce the same rule
    (docs/ONBOARDING.md §1: server-rendered pages and the API are both live
    at once), so this covers corpus_dashboard/corpus_detail *and*
    api_corpus.py's CorpusListView/CorpusDetailView side by side."""

    @classmethod
    def setUpTestData(cls):
        super().setUpTestData()
        cls.private = Corpus.objects.create(name='Internal drafts', is_public=False)
        cls.private.documents.set([cls.plain])

    def auth_headers(self, username='researcher'):
        response = self.client.post(
            reverse('token_obtain_pair'),
            data=json.dumps({'username': username, 'password': 'pw-for-tests-1'}),
            content_type='application/json',
        )
        access = response.json()['access']
        return {'HTTP_AUTHORIZATION': f'Bearer {access}'}

    def test_a_corpus_defaults_to_public(self):
        # self.corpus (ExplorerTestCase.setUpTestData) is created without
        # passing is_public at all — existing corpora keep today's
        # behavior (visible to everyone) unless an admin opts them out.
        self.assertTrue(self.corpus.is_public)

    # --- server-rendered corpus_dashboard / corpus_detail -----------------

    def test_non_staff_does_not_see_private_corpus_in_dashboard(self):
        response = self.client.get(reverse('corpus_dashboard'))
        self.assertNotContains(response, 'Internal drafts')

    def test_staff_sees_private_corpus_in_dashboard_with_a_badge(self):
        self.client.force_login(self.staff)
        response = self.client.get(reverse('corpus_dashboard'))
        self.assertContains(response, 'Internal drafts')
        self.assertContains(response, 'Private')

    def test_non_staff_gets_404_for_private_corpus_detail(self):
        response = self.client.get(reverse('corpus_detail', args=[self.private.id]))
        self.assertEqual(response.status_code, 404)

    def test_staff_can_view_private_corpus_detail(self):
        self.client.force_login(self.staff)
        response = self.client.get(reverse('corpus_detail', args=[self.private.id]))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Private')

    def test_non_staff_can_still_view_a_public_corpus_detail(self):
        response = self.client.get(reverse('corpus_detail', args=[self.corpus.id]))
        self.assertEqual(response.status_code, 200)

    def test_corpus_form_includes_the_public_toggle(self):
        self.client.force_login(self.staff)
        response = self.client.get(reverse('corpus_create'))
        self.assertContains(response, 'name="is_public"')

    # --- API ----------------------------------------------------------------

    def test_api_list_excludes_private_corpus_for_non_staff(self):
        response = self.client.get(reverse('api_corpus_list'), **self.auth_headers('researcher'))
        names = {row['name'] for row in response.json()}
        self.assertNotIn('Internal drafts', names)

    def test_api_list_includes_private_corpus_for_staff(self):
        response = self.client.get(reverse('api_corpus_list'), **self.auth_headers('curator'))
        names = {row['name'] for row in response.json()}
        self.assertIn('Internal drafts', names)

    def test_api_detail_404s_for_non_staff_on_private_corpus(self):
        response = self.client.get(
            reverse('api_corpus_detail', args=[self.private.id]),
            **self.auth_headers('researcher'),
        )
        self.assertEqual(response.status_code, 404)

    def test_api_detail_works_for_staff_on_private_corpus(self):
        response = self.client.get(
            reverse('api_corpus_detail', args=[self.private.id]),
            **self.auth_headers('curator'),
        )
        self.assertEqual(response.status_code, 200)
        self.assertFalse(response.json()['is_public'])

    def test_staff_can_create_a_private_corpus_via_api(self):
        response = self.client.post(
            reverse('api_corpus_list'),
            data=json.dumps({
                'name': 'New private corpus',
                'document_ids': [self.plain.id],
                'is_public': False,
            }),
            content_type='application/json',
            **self.auth_headers('curator'),
        )
        self.assertEqual(response.status_code, 201)
        self.assertFalse(response.json()['is_public'])
        self.assertFalse(Corpus.objects.get(name='New private corpus').is_public)

    def test_created_corpus_defaults_to_public_when_omitted_via_api(self):
        response = self.client.post(
            reverse('api_corpus_list'),
            data=json.dumps({'name': 'Default visibility corpus', 'document_ids': [self.plain.id]}),
            content_type='application/json',
            **self.auth_headers('curator'),
        )
        self.assertEqual(response.status_code, 201)
        self.assertTrue(response.json()['is_public'])

    def test_staff_can_flip_a_corpus_to_private_via_api_patch(self):
        response = self.client.patch(
            reverse('api_corpus_detail', args=[self.corpus.id]),
            data=json.dumps({
                'name': self.corpus.name,
                'document_ids': [self.plain.id],
                'is_public': False,
            }),
            content_type='application/json',
            **self.auth_headers('curator'),
        )
        self.assertEqual(response.status_code, 200)
        self.corpus.refresh_from_db()
        self.assertFalse(self.corpus.is_public)


class CorpusApiTests(ExplorerTestCase):
    """GET /api/corpora/ (main/api_corpus.py) — Phase 3's corpus picker data
    source, the first JSON view of "Corpus Meta" alongside the existing
    server-rendered corpus_dashboard."""

    def auth_headers(self):
        response = self.client.post(
            reverse('token_obtain_pair'),
            data=json.dumps({'username': 'researcher', 'password': 'pw-for-tests-1'}),
            content_type='application/json',
        )
        access = response.json()['access']
        return {'HTTP_AUTHORIZATION': f'Bearer {access}'}

    def test_a_session_cookie_alone_does_not_authenticate_the_api(self):
        # ExplorerTestCase.setUp already force_login's self.user — every
        # test here starts with a valid session cookie. This endpoint's
        # DEFAULT_AUTHENTICATION_CLASSES is JWTAuthentication only (see
        # config/settings.py's REST_FRAMEWORK), so that session must NOT be
        # enough on its own; a real bearer token is required.
        response = self.client.get(reverse('api_corpus_list'))
        self.assertEqual(response.status_code, 401)

    def test_lists_corpora_with_document_counts(self):
        response = self.client.get(reverse('api_corpus_list'), **self.auth_headers())
        self.assertEqual(response.status_code, 200)

        by_name = {row['name']: row for row in response.json()}
        self.assertEqual(by_name['Written 2023']['document_count'], 2)
        self.assertEqual(by_name['Spoken 2024']['document_count'], 1)

    def test_token_totals_are_zero_not_null_for_a_corpus_with_no_documents(self):
        # Sum() over no rows is NULL, not 0 — main/api_corpus.py's
        # SerializerMethodFields exist specifically to coerce this so
        # KUIS-FE never has to null-check a count.
        Corpus.objects.create(name='Empty corpus')

        response = self.client.get(reverse('api_corpus_list'), **self.auth_headers())
        empty = next(row for row in response.json() if row['name'] == 'Empty corpus')

        self.assertEqual(empty['document_count'], 0)
        self.assertEqual(empty['token_total'], 0)
        self.assertEqual(empty['token_total_corrected'], 0)


class DocumentIngestTests(TestCase):
    """main/document_ingest.py (docs/content-hash-dedup.md) — the hash
    formula used to detect duplicate uploads before a Document row is ever
    created, and the get_or_create wrapper around it."""

    def test_bom_and_crlf_variants_hash_identically(self):
        from .document_ingest import compute_content_hash

        plain = compute_content_hash("saya suka teh")
        with_bom_and_crlf = compute_content_hash("﻿saya suka teh\r\n")
        self.assertEqual(plain, with_bom_and_crlf)

    def test_trailing_whitespace_does_not_change_the_hash(self):
        from .document_ingest import compute_content_hash

        self.assertEqual(
            compute_content_hash("saya suka teh"),
            compute_content_hash("  saya suka teh   \n\n"),
        )

    def test_xml_attribute_order_does_not_change_the_hash(self):
        from .document_ingest import compute_content_hash

        reordered = CORPUS_XML.replace(
            "<segment id='1' features='eror;leksikal' Correction='tetapi'>",
            "<segment Correction='tetapi' features='eror;leksikal' id='1'>",
        )
        self.assertNotEqual(CORPUS_XML, reordered)  # sanity: the fixtures do differ
        self.assertEqual(compute_content_hash(CORPUS_XML), compute_content_hash(reordered))

    def test_meaningfully_different_plain_text_hashes_differently(self):
        from .document_ingest import compute_content_hash

        self.assertNotEqual(
            compute_content_hash("saya suka teh"),
            compute_content_hash("saya suka kopi"),
        )

    def test_get_or_create_document_reuses_existing_content(self):
        from .document_ingest import get_or_create_document

        first, created_first = get_or_create_document(title='a.txt', content='saya suka teh', user=None)
        self.assertTrue(created_first)

        second, created_second = get_or_create_document(title='b.txt', content='saya suka teh', user=None)
        self.assertFalse(created_second)
        self.assertEqual(second.id, first.id)
        self.assertEqual(second.title, 'a.txt')  # dedup hit never renames the existing document


class WorkerTaskTests(TestCase):
    """worker/tasks/indexing.py + aggregates.py (architecture plan §5) —
    the async replacement for the old post_save signal. Called directly
    (not .delay()) in most of these since the point is testing the task
    BODY; test_runs_synchronously_end_to_end_via_delay below is the one
    test that goes through .delay() itself, confirming CELERY_TASK_ALWAYS_
    EAGER actually does what every other test in this file assumes."""

    def test_index_document_builds_tokens_and_marks_ready(self):
        doc = Document.objects.create(title='t.xml', content=CORPUS_XML)
        self.assertEqual(doc.status, Document.STATUS_READY)  # model default, not yet indexed
        self.assertEqual(doc.tokens.count(), 0)

        index_document(doc.id)

        doc.refresh_from_db()
        self.assertEqual(doc.status, Document.STATUS_READY)
        self.assertEqual(doc.token_count, 10)  # saya suka makan nasi tapi saya tidak suka nasi goreng
        self.assertTrue(doc.tokens.filter(mode='original').exists())
        self.assertIsNotNone(doc.content_hash)
        self.assertEqual(doc.content_hash, doc.tokenized_hash)
        self.assertEqual(doc.tokenizer_version, 1)

    def test_index_document_uses_the_shared_hash_formula(self):
        from .document_ingest import compute_content_hash

        doc = Document.objects.create(title='t.xml', content=CORPUS_XML)
        index_document(doc.id)

        doc.refresh_from_db()
        self.assertEqual(doc.content_hash, compute_content_hash(CORPUS_XML))

    def test_index_document_resolves_word_type_correctly(self):
        doc = Document.objects.create(title='t.xml', content=CORPUS_XML)
        index_document(doc.id)

        first_token = doc.tokens.filter(mode='original', position=0).get()
        self.assertEqual(first_token.word_type.form, 'saya')

        # Corrected stream substitutes the segment's Correction attribute —
        # same rule main/corpus_parsing.py has always implemented, now
        # reached through word_type instead of a `word` column.
        corrected_forms = list(
            doc.tokens.filter(mode='corrected').order_by('position').values_list('word_type__form', flat=True)
        )
        self.assertIn('tetapi', corrected_forms)
        self.assertNotIn('tapi', corrected_forms)

    def test_reindexing_replaces_rather_than_duplicates_tokens(self):
        doc = Document.objects.create(title='t.xml', content=CORPUS_XML)
        index_document(doc.id)
        first_count = doc.tokens.count()

        index_document(doc.id)  # same content, run again

        self.assertEqual(doc.tokens.count(), first_count)

    def test_aggregate_tasks_populate_document_word_freq_and_ngram(self):
        from .models import DocumentNgram, DocumentWordFreq

        doc = Document.objects.create(title='t.xml', content=CORPUS_XML)
        index_document(doc.id)  # chains both aggregate tasks internally

        word_freqs = DocumentWordFreq.objects.filter(document=doc, mode='original')
        self.assertTrue(word_freqs.exists())
        saya_freq = word_freqs.get(word_type__form='saya')
        self.assertEqual(saya_freq.count, 2)  # "saya" appears twice in CORPUS_XML's original stream

        ngrams = DocumentNgram.objects.filter(document=doc, mode='original', n=2)
        self.assertTrue(ngrams.exists())

    def test_runs_synchronously_end_to_end_via_delay(self):
        # This is the one test in the file that goes through .delay() itself
        # rather than calling the task body directly — every OTHER test
        # fixture in this file (ExplorerTestCase.setUpTestData) relies on
        # .delay() behaving synchronously under CELERY_TASK_ALWAYS_EAGER=1,
        # so this pins that assumption explicitly instead of leaving it
        # implicit.
        from .models import DocumentWordFreq

        doc = Document.objects.create(title='t.xml', content=CORPUS_XML)
        index_document.delay(doc.id)

        doc.refresh_from_db()
        self.assertEqual(doc.status, Document.STATUS_READY)
        self.assertTrue(doc.tokens.exists())
        self.assertTrue(DocumentWordFreq.objects.filter(document=doc).exists())


class CorpusCrudApiTests(ExplorerTestCase):
    """Write endpoints for main/api_corpus.py (architecture plan §6, Phase
    6) — POST/PATCH/DELETE /api/corpora/, POST /api/corpora/assign/. Read
    parity is already covered by CorpusApiTests above; this covers the RBAC
    gate (admin-only writes — same rule as main/views.py::staff_required)
    and the same validation main/forms.py::CorpusForm already enforces."""

    def auth_headers(self, username='researcher'):
        response = self.client.post(
            reverse('token_obtain_pair'),
            data=json.dumps({'username': username, 'password': 'pw-for-tests-1'}),
            content_type='application/json',
        )
        access = response.json()['access']
        return {'HTTP_AUTHORIZATION': f'Bearer {access}'}

    def test_anonymous_cannot_create_a_corpus(self):
        response = self.client.post(
            reverse('api_corpus_list'),
            data=json.dumps({'name': 'New corpus', 'document_ids': [self.plain.id]}),
            content_type='application/json',
        )
        self.assertEqual(response.status_code, 401)

    def test_non_staff_cannot_create_a_corpus(self):
        response = self.client.post(
            reverse('api_corpus_list'),
            data=json.dumps({'name': 'New corpus', 'document_ids': [self.plain.id]}),
            content_type='application/json',
            **self.auth_headers('researcher'),
        )
        self.assertEqual(response.status_code, 403)
        self.assertFalse(Corpus.objects.filter(name='New corpus').exists())

    def test_staff_can_create_a_corpus_with_existing_documents(self):
        response = self.client.post(
            reverse('api_corpus_list'),
            data=json.dumps({'name': 'New corpus', 'document_ids': [self.plain.id]}),
            content_type='application/json',
            **self.auth_headers('curator'),
        )
        self.assertEqual(response.status_code, 201)

        body = response.json()
        self.assertEqual(body['name'], 'New corpus')
        self.assertEqual(body['document_count'], 1)
        self.assertEqual([d['id'] for d in body['documents']], [self.plain.id])

    def test_create_rejects_duplicate_name(self):
        response = self.client.post(
            reverse('api_corpus_list'),
            data=json.dumps({'name': self.corpus.name, 'document_ids': [self.plain.id]}),
            content_type='application/json',
            **self.auth_headers('curator'),
        )
        self.assertEqual(response.status_code, 400)
        self.assertIn('name', response.json())

    def test_create_requires_at_least_one_document_or_file(self):
        response = self.client.post(
            reverse('api_corpus_list'),
            data=json.dumps({'name': 'Empty attempt'}),
            content_type='application/json',
            **self.auth_headers('curator'),
        )
        self.assertEqual(response.status_code, 400)
        self.assertIn('non_field_errors', response.json())

    def test_create_with_uploaded_file_indexes_it(self):
        upload = SimpleUploadedFile('new.txt', b'saya suka teh', content_type='text/plain')
        response = self.client.post(
            reverse('api_corpus_list'),
            data={'name': 'File corpus', 'files': [upload]},
            **self.auth_headers('curator'),
        )
        self.assertEqual(response.status_code, 201)
        self.assertEqual(response.json()['document_count'], 1)

        new_doc = Document.objects.get(title='new.txt')
        # CELERY_TASK_ALWAYS_EAGER=1 — .delay() already ran synchronously.
        self.assertEqual(new_doc.status, Document.STATUS_READY)
        self.assertTrue(new_doc.tokens.exists())

    def test_create_rejects_non_utf8_file(self):
        upload = SimpleUploadedFile('bad.txt', b'\xff\xfe', content_type='text/plain')
        response = self.client.post(
            reverse('api_corpus_list'),
            data={'name': 'Bad file corpus', 'files': [upload]},
            **self.auth_headers('curator'),
        )
        self.assertEqual(response.status_code, 400)
        self.assertIn('files', response.json())
        self.assertFalse(Corpus.objects.filter(name='Bad file corpus').exists())

    def test_create_with_duplicate_file_reuses_existing_document(self):
        before_count = Document.objects.count()
        upload = SimpleUploadedFile('replay.txt', PLAIN_TEXT.encode('utf-8'), content_type='text/plain')
        response = self.client.post(
            reverse('api_corpus_list'),
            data={'name': 'Reuse corpus', 'files': [upload]},
            **self.auth_headers('curator'),
        )
        self.assertEqual(response.status_code, 201)
        body = response.json()
        self.assertEqual(body['deduplicated_count'], 1)
        self.assertEqual([d['id'] for d in body['documents']], [self.plain.id])
        self.assertEqual(Document.objects.count(), before_count)  # no new row

    def test_create_with_mixed_duplicate_and_new_files(self):
        before_count = Document.objects.count()
        duplicate = SimpleUploadedFile('replay.txt', PLAIN_TEXT.encode('utf-8'), content_type='text/plain')
        new = SimpleUploadedFile('fresh.txt', b'saya suka kopi', content_type='text/plain')
        response = self.client.post(
            reverse('api_corpus_list'),
            data={'name': 'Mixed corpus', 'files': [duplicate, new]},
            **self.auth_headers('curator'),
        )
        self.assertEqual(response.status_code, 201)
        body = response.json()
        self.assertEqual(body['deduplicated_count'], 1)
        self.assertEqual(body['document_count'], 2)
        self.assertEqual(Document.objects.count(), before_count + 1)  # exactly one new row

    def test_any_authenticated_user_can_retrieve_corpus_detail(self):
        response = self.client.get(
            reverse('api_corpus_detail', args=[self.corpus.id]),
            **self.auth_headers('researcher'),
        )
        self.assertEqual(response.status_code, 200)

        body = response.json()
        self.assertEqual(body['document_count'], 2)
        self.assertEqual({d['id'] for d in body['documents']}, {self.annotated.id, self.plain.id})

    def test_staff_can_update_a_corpus_replacing_membership(self):
        response = self.client.patch(
            reverse('api_corpus_detail', args=[self.corpus.id]),
            data=json.dumps({
                'name': self.corpus.name,
                'description': 'updated',
                'document_ids': [self.plain.id],  # drops self.annotated
            }),
            content_type='application/json',
            **self.auth_headers('curator'),
        )
        self.assertEqual(response.status_code, 200)

        self.corpus.refresh_from_db()
        self.assertEqual(list(self.corpus.documents.values_list('id', flat=True)), [self.plain.id])
        self.assertEqual(self.corpus.description, 'updated')

    def test_non_staff_cannot_update_a_corpus(self):
        response = self.client.patch(
            reverse('api_corpus_detail', args=[self.corpus.id]),
            data=json.dumps({'name': self.corpus.name, 'document_ids': [self.plain.id]}),
            content_type='application/json',
            **self.auth_headers('researcher'),
        )
        self.assertEqual(response.status_code, 403)
        self.assertEqual(self.corpus.documents.count(), 2)  # unchanged

    def test_staff_can_delete_a_corpus_but_documents_survive(self):
        corpus_id = self.other.id
        response = self.client.delete(
            reverse('api_corpus_detail', args=[corpus_id]),
            **self.auth_headers('curator'),
        )
        self.assertEqual(response.status_code, 204)
        self.assertFalse(Corpus.objects.filter(id=corpus_id).exists())
        self.assertTrue(Document.objects.filter(id=self.plain.id).exists())

    def test_non_staff_cannot_delete_a_corpus(self):
        response = self.client.delete(
            reverse('api_corpus_detail', args=[self.other.id]),
            **self.auth_headers('researcher'),
        )
        self.assertEqual(response.status_code, 403)
        self.assertTrue(Corpus.objects.filter(id=self.other.id).exists())

    def test_assign_is_additive_and_staff_only(self):
        # self.plain already belongs to both self.corpus and self.other;
        # self.annotated belongs only to self.corpus.
        response = self.client.post(
            reverse('api_corpus_assign'),
            data=json.dumps({'document_ids': [self.annotated.id], 'corpus_ids': [self.other.id]}),
            content_type='application/json',
            **self.auth_headers('curator'),
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()['added'], 1)
        self.assertIn(self.annotated, self.other.documents.all())
        self.assertIn(self.plain, self.corpus.documents.all())  # unrelated membership untouched

    def test_non_staff_cannot_assign(self):
        response = self.client.post(
            reverse('api_corpus_assign'),
            data=json.dumps({'document_ids': [self.annotated.id], 'corpus_ids': [self.other.id]}),
            content_type='application/json',
            **self.auth_headers('researcher'),
        )
        self.assertEqual(response.status_code, 403)
        self.assertNotIn(self.annotated, self.other.documents.all())


class DocumentApiTests(ExplorerTestCase):
    """main/api_documents.py (architecture plan §6, Phase 6) — document
    listing + upload over the API."""

    def auth_headers(self, username='researcher'):
        response = self.client.post(
            reverse('token_obtain_pair'),
            data=json.dumps({'username': username, 'password': 'pw-for-tests-1'}),
            content_type='application/json',
        )
        access = response.json()['access']
        return {'HTTP_AUTHORIZATION': f'Bearer {access}'}

    def test_any_authenticated_user_can_list_documents(self):
        response = self.client.get(reverse('api_document_list'), **self.auth_headers('researcher'))
        self.assertEqual(response.status_code, 200)

        titles = {row['title'] for row in response.json()}
        self.assertIn('annotated.xml', titles)

    def test_unassigned_filter(self):
        Document.objects.create(title='loose.txt', content='saya suka teh')
        response = self.client.get(
            reverse('api_document_list'), {'unassigned': 'true'}, **self.auth_headers('researcher')
        )

        titles = {row['title'] for row in response.json()}
        self.assertIn('loose.txt', titles)
        self.assertNotIn('annotated.xml', titles)  # belongs to self.corpus

    def test_non_staff_cannot_upload(self):
        response = self.client.post(
            reverse('api_document_upload'),
            data=json.dumps({'title': 'New doc', 'content': 'saya suka teh'}),
            content_type='application/json',
            **self.auth_headers('researcher'),
        )
        self.assertEqual(response.status_code, 403)
        self.assertFalse(Document.objects.filter(title='New doc').exists())

    def test_staff_can_upload_pasted_content_and_it_gets_indexed(self):
        response = self.client.post(
            reverse('api_document_upload'),
            data=json.dumps({'title': 'New doc', 'content': 'saya suka teh'}),
            content_type='application/json',
            **self.auth_headers('curator'),
        )
        self.assertEqual(response.status_code, 201)

        doc = Document.objects.get(title='New doc')
        self.assertEqual(doc.status, Document.STATUS_READY)  # CELERY_TASK_ALWAYS_EAGER=1
        self.assertFalse(doc.corpora.exists())  # not attached to any corpus, matching upload_document

    def test_upload_requires_content_or_file(self):
        response = self.client.post(
            reverse('api_document_upload'),
            data=json.dumps({'title': 'Empty doc'}),
            content_type='application/json',
            **self.auth_headers('curator'),
        )
        self.assertEqual(response.status_code, 400)

    def test_upload_rejects_non_utf8_file(self):
        upload = SimpleUploadedFile('bad.txt', b'\xff\xfe', content_type='text/plain')
        response = self.client.post(
            reverse('api_document_upload'),
            data={'title': 'Bad file', 'file': upload},
            **self.auth_headers('curator'),
        )
        self.assertEqual(response.status_code, 400)
        self.assertIn('file', response.json())
        self.assertFalse(Document.objects.filter(title='Bad file').exists())

    def test_uploading_duplicate_file_content_reuses_existing_document(self):
        before_count = Document.objects.count()
        upload = SimpleUploadedFile('replay.txt', PLAIN_TEXT.encode('utf-8'), content_type='text/plain')
        response = self.client.post(
            reverse('api_document_upload'),
            data={'title': 'replay.txt', 'file': upload},
            **self.auth_headers('curator'),
        )
        self.assertEqual(response.status_code, 200)  # not 201 — no new row created
        body = response.json()
        self.assertTrue(body['duplicate'])
        self.assertEqual(body['id'], self.plain.id)
        self.assertEqual(Document.objects.count(), before_count)

    def test_uploading_duplicate_pasted_content_reuses_existing_document(self):
        before_count = Document.objects.count()
        response = self.client.post(
            reverse('api_document_upload'),
            data=json.dumps({'title': 'Different title', 'content': PLAIN_TEXT}),
            content_type='application/json',
            **self.auth_headers('curator'),
        )
        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertTrue(body['duplicate'])
        self.assertEqual(body['id'], self.plain.id)
        self.assertEqual(body['title'], 'plain.txt')  # existing title wins, not the new upload's
        self.assertEqual(Document.objects.count(), before_count)

    def test_duplicate_upload_does_not_change_existing_corpus_membership(self):
        before_corpora = set(self.plain.corpora.values_list('id', flat=True))
        self.client.post(
            reverse('api_document_upload'),
            data=json.dumps({'title': 'Different title', 'content': PLAIN_TEXT}),
            content_type='application/json',
            **self.auth_headers('curator'),
        )
        self.plain.refresh_from_db()
        self.assertEqual(set(self.plain.corpora.values_list('id', flat=True)), before_corpora)


class HtmlUploadDedupTests(ExplorerTestCase):
    """Server-rendered corpus_create/corpus_edit/upload_document
    (docs/content-hash-dedup.md) — confirms the HTML views reuse existing
    Document rows on duplicate content, the same as their JSON API
    equivalents in CorpusCrudApiTests/DocumentApiTests."""

    def setUp(self):
        self.client.force_login(self.staff)

    def test_corpus_create_reuses_duplicate_file_content(self):
        before_count = Document.objects.count()
        upload = SimpleUploadedFile('replay.txt', PLAIN_TEXT.encode('utf-8'), content_type='text/plain')
        response = self.client.post(reverse('corpus_create'), {
            'name': 'HTML reuse corpus',
            'description': '',
            'files': [upload],
        })
        self.assertEqual(response.status_code, 302)
        self.assertEqual(Document.objects.count(), before_count)

        corpus = Corpus.objects.get(name='HTML reuse corpus')
        self.assertEqual([d.id for d in corpus.documents.all()], [self.plain.id])

        messages_text = ' '.join(str(m) for m in get_messages(response.wsgi_request))
        self.assertIn('already existed', messages_text)

    def test_corpus_edit_reuses_duplicate_file_content(self):
        before_count = Document.objects.count()
        upload = SimpleUploadedFile('replay.txt', PLAIN_TEXT.encode('utf-8'), content_type='text/plain')
        response = self.client.post(reverse('corpus_edit', args=[self.other.id]), {
            'name': self.other.name,
            'description': '',
            'files': [upload],
        })
        self.assertEqual(response.status_code, 302)
        self.assertEqual(Document.objects.count(), before_count)

    def test_upload_document_reuses_duplicate_content(self):
        before_count = Document.objects.count()
        response = self.client.post(reverse('upload_document'), {
            'title': 'Different title',
            'content': PLAIN_TEXT,
        })
        self.assertEqual(response.status_code, 302)
        self.assertEqual(Document.objects.count(), before_count)

        messages_text = ' '.join(str(m) for m in get_messages(response.wsgi_request))
        self.assertIn('already exists', messages_text)


class AccountApiTests(TestCase):
    """main/api_account.py (architecture plan §6, KUIS-FE rebuild) —
    self-registration and the contact form, both previously
    server-rendered-only. Both reuse main/forms.py's RegisterForm/
    ContactForm directly, so these tests mostly pin the API-specific parts
    (response shape, status codes, error-key normalization) rather than
    re-testing validation rules the forms already own."""

    def test_register_creates_user_and_returns_tokens(self):
        response = self.client.post(
            reverse('api_register'),
            data=json.dumps({
                'username': 'newresearcher',
                'email': 'newresearcher@example.com',
                'password1': 'Xk9mQpLv2zR7',
                'password2': 'Xk9mQpLv2zR7',
            }),
            content_type='application/json',
        )
        self.assertEqual(response.status_code, 201)

        body = response.json()
        self.assertIn('access', body)
        self.assertIn('refresh', body)
        self.assertTrue(User.objects.filter(username='newresearcher').exists())

    def test_register_rejects_duplicate_email(self):
        User.objects.create_user('existing', email='taken@example.com', password='pw-for-tests-1')

        response = self.client.post(
            reverse('api_register'),
            data=json.dumps({
                'username': 'someoneelse',
                'email': 'taken@example.com',
                'password1': 'Xk9mQpLv2zR7',
                'password2': 'Xk9mQpLv2zR7',
            }),
            content_type='application/json',
        )
        self.assertEqual(response.status_code, 400)
        self.assertIn('email', response.json())
        self.assertFalse(User.objects.filter(username='someoneelse').exists())

    def test_register_rejects_mismatched_passwords(self):
        response = self.client.post(
            reverse('api_register'),
            data=json.dumps({
                'username': 'mismatched',
                'email': 'mismatched@example.com',
                'password1': 'Xk9mQpLv2zR7',
                'password2': 'DifferentPass9',
            }),
            content_type='application/json',
        )
        self.assertEqual(response.status_code, 400)
        # Django's UserCreationForm reports this on password2, not '__all__'
        # — no normalization needed, just confirming the field key survives.
        self.assertIn('password2', response.json())
        self.assertFalse(User.objects.filter(username='mismatched').exists())

    def test_contact_sends_email(self):
        response = self.client.post(
            reverse('api_contact'),
            data=json.dumps({
                'name': 'A visitor',
                'email': 'visitor@example.com',
                'message': 'Hello there',
            }),
            content_type='application/json',
        )
        self.assertEqual(response.status_code, 204)
        self.assertEqual(len(mail.outbox), 1)
        self.assertIn('A visitor', mail.outbox[0].body)
        self.assertIn('Hello there', mail.outbox[0].body)

    def test_contact_requires_all_fields(self):
        response = self.client.post(
            reverse('api_contact'),
            data=json.dumps({'name': 'A visitor'}),
            content_type='application/json',
        )
        self.assertEqual(response.status_code, 400)
        self.assertEqual(len(mail.outbox), 0)


class BackfillContentHashCommandTests(TestCase):
    """main/management/commands/backfill_content_hash.py
    (docs/content-hash-dedup.md) — recomputes content_hash/tokenized_hash
    under the new canonicalized formula for documents hashed (or never
    hashed) under the old one, without touching Token/aggregate rows."""

    def test_recomputes_hash_with_new_formula(self):
        from .document_ingest import compute_content_hash

        doc = Document.objects.create(
            title='t.txt', content='saya suka teh',
            content_hash='stale-placeholder', tokenized_hash='stale-placeholder',
        )

        call_command('backfill_content_hash', stdout=StringIO())

        doc.refresh_from_db()
        expected = compute_content_hash('saya suka teh')
        self.assertEqual(doc.content_hash, expected)
        self.assertEqual(doc.tokenized_hash, expected)

    def test_never_backfills_tokenized_hash_for_a_never_indexed_document(self):
        doc = Document.objects.create(title='t.txt', content='saya suka teh')
        self.assertIsNone(doc.tokenized_hash)

        call_command('backfill_content_hash', stdout=StringIO())

        doc.refresh_from_db()
        self.assertIsNotNone(doc.content_hash)
        self.assertIsNone(doc.tokenized_hash)  # never indexed — nothing to keep in lockstep

    def test_reports_but_does_not_merge_pre_existing_duplicates(self):
        first = Document.objects.create(
            title='dup-a.txt', content='saya suka teh', content_hash='stale-a',
        )
        second = Document.objects.create(
            title='dup-b.txt', content='saya suka teh', content_hash='stale-b',
        )

        out = StringIO()
        call_command('backfill_content_hash', stdout=out)

        first.refresh_from_db()
        second.refresh_from_db()
        self.assertEqual(first.content_hash, 'stale-a')  # left untouched
        self.assertEqual(second.content_hash, 'stale-b')  # left untouched

        output = out.getvalue()
        self.assertIn('dup-a.txt', output)
        self.assertIn('dup-b.txt', output)
        self.assertIn('duplicate-content group', output)


class ProfileApiTests(TestCase):
    """GET /api/profile/ (main/api_account.py) — mirrors profile.html."""

    def setUp(self):
        self.user = User.objects.create_user(
            'researcher', email='researcher@example.com', password='pw-for-tests-1'
        )

    def auth_headers(self):
        response = self.client.post(
            reverse('token_obtain_pair'),
            data=json.dumps({'username': 'researcher', 'password': 'pw-for-tests-1'}),
            content_type='application/json',
        )
        access = response.json()['access']
        return {'HTTP_AUTHORIZATION': f'Bearer {access}'}

    def test_anonymous_cannot_view_profile(self):
        response = self.client.get(reverse('api_profile'))
        self.assertEqual(response.status_code, 401)

    def test_profile_reports_own_counts_only(self):
        other = User.objects.create_user('other', password='pw-for-tests-1')

        Document.objects.create(title='mine.xml', content='saya suka teh', user=self.user)
        Document.objects.create(title='mine2.xml', content='saya suka teh', user=self.user)
        Document.objects.create(title='theirs.xml', content='saya suka teh', user=other)

        Corpus.objects.create(name='Mine', created_by=self.user)
        Corpus.objects.create(name='Theirs', created_by=other)

        response = self.client.get(reverse('api_profile'), **self.auth_headers())
        self.assertEqual(response.status_code, 200)

        body = response.json()
        self.assertEqual(body['username'], 'researcher')
        self.assertEqual(body['email'], 'researcher@example.com')
        self.assertFalse(body['is_staff'])
        self.assertEqual(body['documents_uploaded'], 2)
        self.assertEqual(body['corpora_created'], 1)


class CorpusParsingTests(TestCase):
    """main/corpus_parsing.py's nested-segment fix + extract_error_annotations
    (docs/error-analytics-plan.md). The pre-fix bug: only direct children of
    <body> were visited, so a <segment> nested inside another <segment> — the
    data's way of representing overlapping errors — contributed nothing to
    either stream. NESTED_CORPUS_XML/LOWERCASE_CORRECTION_XML are modeled on
    the real pattern in info/KUIS2023FUA201-Eror.xml (cross-checked directly
    in LoadErrorTaxonomyCommandTests below)."""

    def test_nested_segment_text_reaches_the_original_stream(self):
        from .corpus_parsing import extract_word_streams

        original_words, corrected_words = extract_word_streams(NESTED_CORPUS_XML)

        self.assertEqual(original_words, ['saya', 'suka', 'goreng', 'nasi', 'sekali'])
        self.assertEqual(corrected_words, ['saya', 'suka', 'nasi', 'goreng', 'sekali'])

    def test_corrected_stream_stays_capital_correction_only(self):
        # Pins the pre-existing, deliberately-unchanged quirk (decision 1,
        # docs/error-analytics-plan.md): a segment correction spelled with
        # a lowercase attribute never reaches the token corrected-stream,
        # even though extract_error_annotations resolves it fine below for
        # display (correction_text is a separate concern from tokens).
        from .corpus_parsing import extract_word_streams

        original_words, corrected_words = extract_word_streams(LOWERCASE_CORRECTION_XML)

        self.assertEqual(original_words, ['sayang', 'tuti'])
        self.assertEqual(corrected_words, ['sayang'])

    def test_nested_annotation_span_is_contained_in_outer_span(self):
        from .corpus_parsing import extract_error_annotations

        annotations = extract_error_annotations(NESTED_CORPUS_XML)
        self.assertEqual(len(annotations), 2)

        inner = next(a for a in annotations if a['source_segment_id'] == '1')
        outer = next(a for a in annotations if a['source_segment_id'] == '3')

        self.assertEqual((outer['start_position'], outer['end_position']), (2, 4))
        self.assertEqual(outer['original_text'], 'goreng nasi')
        self.assertEqual(outer['correction_text'], 'nasi goreng')
        self.assertEqual(outer['raw_features'], 'eror;gramatikal;frasa-nomina;urtfn')

        self.assertEqual((inner['start_position'], inner['end_position']), (3, 4))
        self.assertEqual(inner['original_text'], 'nasi')
        self.assertEqual(inner['raw_features'], 'eror;ejaan;ejk')

        # Strictly contained, not just overlapping — position-consistent
        # with the original-stream fix above by construction.
        self.assertGreaterEqual(inner['start_position'], outer['start_position'])
        self.assertLessEqual(inner['end_position'], outer['end_position'])

    def test_annotation_reads_correction_case_insensitively(self):
        from .corpus_parsing import extract_error_annotations

        annotations = extract_error_annotations(LOWERCASE_CORRECTION_XML)
        self.assertEqual(len(annotations), 1)
        self.assertEqual(annotations[0]['correction_text'], 'Tuti sayang')

    def test_non_structured_content_yields_no_annotations(self):
        from .corpus_parsing import extract_error_annotations

        self.assertEqual(extract_error_annotations(PLAIN_TEXT), [])

    def test_existing_top_level_segment_fixture_is_unaffected(self):
        # Regression pin: the original (non-nested) CORPUS_XML fixture used
        # throughout this file must tokenize identically to before the fix.
        from .corpus_parsing import extract_word_streams

        original_words, corrected_words = extract_word_streams(CORPUS_XML)
        self.assertEqual(len(original_words), 10)
        self.assertIn('tetapi', corrected_words)
        self.assertNotIn('tapi', corrected_words)


class ErrorAnnotationWorkerTaskTests(TestCase):
    """worker/tasks/error_annotations.py (docs/error-analytics-plan.md) —
    extraction + Tier-2 aggregation, chained from
    worker/tasks/indexing.py::index_document. Loads the real taxonomy
    (info/error.xml) so raw_features actually resolve, same as production."""

    @classmethod
    def setUpTestData(cls):
        call_command('load_error_taxonomy', stdout=StringIO())

    def test_resolves_taxonomy_nodes_and_positions(self):
        from .models import ErrorAnnotation

        doc = Document.objects.create(title='n.xml', content=NESTED_CORPUS_XML)
        index_document(doc.id)  # chains extract_document_error_annotations internally

        annotations = ErrorAnnotation.objects.filter(document=doc).order_by('start_position')
        self.assertEqual(annotations.count(), 2)

        outer = annotations.get(source_segment_id='3')
        self.assertEqual(outer.taxonomy_node.code, 'urtfn')
        self.assertEqual(outer.taxonomy_node.path, 'eror;gramatikal;frasa-nomina;urtfn')

        # Position-consistency by construction: the outer span's Token rows
        # (mode=original) are exactly the annotation's text.
        tokens = list(
            doc.tokens.filter(mode='original', position__gte=outer.start_position, position__lt=outer.end_position)
            .order_by('position').values_list('word_type__form', flat=True)
        )
        self.assertEqual(tokens, ['goreng', 'nasi'])

    def test_parent_resolution_uses_explicit_attribute_not_structural_nesting(self):
        from .models import ErrorAnnotation

        doc = Document.objects.create(title='n.xml', content=NESTED_CORPUS_XML)
        index_document(doc.id)

        inner = ErrorAnnotation.objects.get(document=doc, source_segment_id='1')
        # Structurally nested inside segment 3, but the fixture deliberately
        # omits parent='1' (mirrors real info/KUIS2023FUA201-Eror.xml's
        # segment id='6', which is nested but has no parent= attribute
        # either) — so the FK stays unresolved rather than inferred from
        # structure.
        self.assertIsNone(inner.parent_id)

    def test_unresolved_raw_features_does_not_fail_the_batch(self):
        from .models import ErrorAnnotation

        content = (
            "<document><header><textfile>t</textfile><lang>id</lang></header><body>"
            "saya <segment id='1' features='eror;doesnotexist'>suka</segment> teh"
            "</body></document>"
        )
        doc = Document.objects.create(title='u.xml', content=content)
        index_document(doc.id)

        annotation = ErrorAnnotation.objects.get(document=doc)
        self.assertIsNone(annotation.taxonomy_node_id)
        self.assertEqual(annotation.raw_features, 'eror;doesnotexist')

    def test_compute_document_error_freq_excludes_inactive_and_unresolved(self):
        from .models import DocumentErrorFreq

        content = (
            "<document><header><textfile>t</textfile><lang>id</lang></header><body>"
            "<segment id='1' features='eror;ejaan;ejk' state='inactive'>a</segment> "
            "<segment id='2' features='eror;doesnotexist'>b</segment> "
            "<segment id='3' features='eror;ejaan;ejk'>c</segment>"
            "</body></document>"
        )
        doc = Document.objects.create(title='s.xml', content=content)
        index_document(doc.id)

        freqs = {f.taxonomy_node.code: f.count for f in DocumentErrorFreq.objects.filter(document=doc)}
        self.assertEqual(freqs, {'ejk': 1})  # only the active + resolved segment (id=3) counted

    def test_reextraction_replaces_rather_than_duplicates(self):
        from .models import ErrorAnnotation
        from worker.tasks.error_annotations import extract_document_error_annotations

        doc = Document.objects.create(title='n.xml', content=NESTED_CORPUS_XML)
        index_document(doc.id)
        first_count = ErrorAnnotation.objects.filter(document=doc).count()

        extract_document_error_annotations(doc.id)  # same content, run again

        self.assertEqual(ErrorAnnotation.objects.filter(document=doc).count(), first_count)

    def test_index_document_chains_annotations_before_freq_via_delay(self):
        # Pins the ordering fix: compute_document_error_freq is chained
        # from INSIDE extract_document_error_annotations, not fired as a
        # second independent .delay() from index_document — so by the time
        # index_document.delay() returns under CELERY_TASK_ALWAYS_EAGER=1,
        # both ErrorAnnotation and DocumentErrorFreq exist.
        from .models import DocumentErrorFreq, ErrorAnnotation

        doc = Document.objects.create(title='n.xml', content=NESTED_CORPUS_XML)
        index_document.delay(doc.id)

        self.assertTrue(ErrorAnnotation.objects.filter(document=doc).exists())
        self.assertTrue(DocumentErrorFreq.objects.filter(document=doc).exists())


class LoadErrorTaxonomyCommandTests(TestCase):
    """main/management/commands/load_error_taxonomy.py
    (docs/error-analytics-plan.md) — loads info/error.xml, the real
    taxonomy file, not a test-only fixture."""

    def test_idempotent_rerun(self):
        from .models import ErrorTaxonomyNode

        call_command('load_error_taxonomy', stdout=StringIO())
        first_count = ErrorTaxonomyNode.objects.count()

        call_command('load_error_taxonomy', stdout=StringIO())
        self.assertEqual(ErrorTaxonomyNode.objects.count(), first_count)

    def test_path_top_category_and_leaf_computed_correctly(self):
        from .models import ErrorTaxonomyNode

        call_command('load_error_taxonomy', stdout=StringIO())

        ktinf = ErrorTaxonomyNode.objects.get(code='ktinf')
        self.assertEqual(ktinf.path, 'eror;leksikal;kata;ktinf')
        self.assertTrue(ktinf.is_leaf)
        self.assertEqual(ktinf.top_category, 'leksikal')

        gramatikal = ErrorTaxonomyNode.objects.get(code='gramatikal')
        self.assertFalse(gramatikal.is_leaf)
        self.assertEqual(gramatikal.top_category, 'gramatikal')  # a depth-1 node is its own top_category

        root = ErrorTaxonomyNode.objects.get(code='eror')
        self.assertIsNone(root.parent)
        self.assertFalse(root.is_leaf)

    def test_every_real_sample_segment_feature_resolves(self):
        # Cross-check against actual uploaded-document data, not just the
        # taxonomy file in isolation.
        from django.conf import settings

        from .corpus_parsing import extract_error_annotations
        from .models import ErrorTaxonomyNode

        call_command('load_error_taxonomy', stdout=StringIO())

        sample_path = settings.BASE_DIR / 'info' / 'KUIS2023FUA201-Eror.xml'
        annotations = extract_error_annotations(sample_path.read_text(encoding='utf-8'))
        self.assertTrue(annotations)  # the sample actually has segments

        known_paths = set(ErrorTaxonomyNode.objects.values_list('path', flat=True))
        for annotation in annotations:
            self.assertIn(annotation['raw_features'], known_paths)


class BackfillErrorAnnotationsCommandTests(TestCase):
    """main/management/commands/backfill_error_annotations.py — mirrors
    BackfillContentHashCommandTests' shape for the error-annotation
    equivalent (docs/error-analytics-plan.md)."""

    @classmethod
    def setUpTestData(cls):
        call_command('load_error_taxonomy', stdout=StringIO())

    def test_backfills_only_documents_missing_annotations_by_default(self):
        from .models import ErrorAnnotation

        already_processed = Document.objects.create(title='a.xml', content=NESTED_CORPUS_XML)
        index_document(already_processed.id)
        self.assertTrue(ErrorAnnotation.objects.filter(document=already_processed).exists())

        # Distinct content — content_hash is globally unique, so this can't
        # reuse NESTED_CORPUS_XML.
        never_processed = Document.objects.create(title='b.xml', content=LOWERCASE_CORRECTION_XML)
        # Simulate a pre-feature document: Token rows exist (indexed before
        # this feature shipped) but no ErrorAnnotation rows yet.
        index_document(never_processed.id)
        ErrorAnnotation.objects.filter(document=never_processed).delete()

        out = StringIO()
        call_command('backfill_error_annotations', stdout=out)

        self.assertTrue(ErrorAnnotation.objects.filter(document=never_processed).exists())
        self.assertIn('b.xml', out.getvalue())
        self.assertNotIn('a.xml', out.getvalue())  # already had annotations — skipped

    def test_all_flag_recomputes_every_document(self):
        doc = Document.objects.create(title='a.xml', content=NESTED_CORPUS_XML)
        index_document(doc.id)

        out = StringIO()
        call_command('backfill_error_annotations', '--all', stdout=out)

        self.assertIn('a.xml', out.getvalue())


class RetokenizeDryRunTests(TestCase):
    """retokenize --dry-run (docs/error-analytics-plan.md) — reviews the
    nested-segment tokenizer fix's impact on corpus_db before writing
    anything, the step this project's prior schema migrations already use
    before touching real data."""

    def test_dry_run_writes_nothing(self):
        doc = Document.objects.create(
            title='n.xml', content=NESTED_CORPUS_XML, token_count=0, token_count_corrected=0,
        )
        # Deliberately NOT indexed — token_count stays at the stale value
        # above, simulating a document indexed before the tokenizer fix.

        out = StringIO()
        call_command('retokenize', '--dry-run', stdout=out)

        doc.refresh_from_db()
        self.assertEqual(doc.token_count, 0)  # untouched
        self.assertEqual(doc.tokens.count(), 0)  # no Token rows written

        output = out.getvalue()
        self.assertIn('n.xml', output)
        self.assertIn('(+5)', output)  # 5 original tokens recomputed from a stale 0
        self.assertIn('Dry run only', output)


class AccountApiTestCase(TestCase):
    """Shared fixture for main/api_users.py (docs/user-management-plan.md):
    a plain user, an Admin (is_staff), and a Super Admin (is_superuser) —
    the three tiers the feature's RBAC distinguishes."""

    @classmethod
    def setUpTestData(cls):
        cls.plain = User.objects.create_user('plain', password='pw-for-tests-1', email='plain@example.com')
        cls.admin = User.objects.create_user('admin', password='pw-for-tests-1', email='admin@example.com', is_staff=True)
        cls.super_admin = User.objects.create_user(
            'superadmin', password='pw-for-tests-1', email='superadmin@example.com',
            is_staff=True, is_superuser=True,
        )

    def auth_headers(self, username):
        response = self.client.post(
            reverse('token_obtain_pair'),
            data=json.dumps({'username': username, 'password': 'pw-for-tests-1'}),
            content_type='application/json',
        )
        tokens = response.json()
        return {'HTTP_AUTHORIZATION': f"Bearer {tokens['access']}"}, tokens.get('refresh')


class UserAccountApiTests(AccountApiTestCase):
    """Admin (and, via the confirmed superset, Super Admin) manages regular
    Users — /api/users/."""

    def test_anonymous_cannot_list_users(self):
        response = self.client.get(reverse('api_user_list'))
        self.assertEqual(response.status_code, 401)

    def test_non_staff_cannot_list_users(self):
        headers, _ = self.auth_headers('plain')
        response = self.client.get(reverse('api_user_list'), **headers)
        self.assertEqual(response.status_code, 403)

    def test_admin_can_list_and_create_users(self):
        headers, _ = self.auth_headers('admin')
        response = self.client.post(
            reverse('api_user_list'),
            data=json.dumps({'username': 'newbie', 'email': 'newbie@example.com', 'password': 'a-strong-pw-1'}),
            content_type='application/json',
            **headers,
        )
        self.assertEqual(response.status_code, 201)
        body = response.json()
        self.assertFalse(body['is_staff'])
        self.assertEqual(body['status'], 'active')

        response = self.client.get(reverse('api_user_list'), **headers)
        self.assertEqual(response.status_code, 200)
        usernames = {row['username'] for row in response.json()['results']}
        self.assertIn('newbie', usernames)

    def test_super_admin_can_also_manage_users(self):
        # Proves the confirmed superset: Super Admin's is_staff=True already
        # satisfies ADMIN_PERMISSIONS, no separate code path needed.
        headers, _ = self.auth_headers('superadmin')
        response = self.client.post(
            reverse('api_user_list'),
            data=json.dumps({'username': 'fromsuper', 'email': 'fromsuper@example.com', 'password': 'a-strong-pw-1'}),
            content_type='application/json',
            **headers,
        )
        self.assertEqual(response.status_code, 201)

    def test_create_rejects_duplicate_email_case_insensitively(self):
        headers, _ = self.auth_headers('admin')
        response = self.client.post(
            reverse('api_user_list'),
            data=json.dumps({'username': 'other', 'email': 'PLAIN@example.com', 'password': 'a-strong-pw-1'}),
            content_type='application/json',
            **headers,
        )
        self.assertEqual(response.status_code, 400)
        self.assertIn('email', response.json())

    def test_create_cannot_self_elevate_is_staff(self):
        headers, _ = self.auth_headers('admin')
        response = self.client.post(
            reverse('api_user_list'),
            data=json.dumps({
                'username': 'sneaky', 'email': 'sneaky@example.com', 'password': 'a-strong-pw-1',
                'is_staff': True, 'is_superuser': True,
            }),
            content_type='application/json',
            **headers,
        )
        self.assertEqual(response.status_code, 201)
        created = User.objects.get(username='sneaky')
        self.assertFalse(created.is_staff)
        self.assertFalse(created.is_superuser)

    def test_users_list_excludes_admins_and_superadmins(self):
        headers, _ = self.auth_headers('admin')
        response = self.client.get(reverse('api_user_list'), **headers)
        usernames = {row['username'] for row in response.json()['results']}
        self.assertNotIn('admin', usernames)
        self.assertNotIn('superadmin', usernames)

    def test_admin_gets_404_targeting_an_admin_via_users_endpoint(self):
        headers, _ = self.auth_headers('admin')
        response = self.client.get(reverse('api_user_detail', args=[self.super_admin.id]), **headers)
        self.assertEqual(response.status_code, 404)

    def test_admin_can_block_and_unblock_user(self):
        headers, _ = self.auth_headers('admin')
        response = self.client.post(reverse('api_user_block', args=[self.plain.id]), **headers)
        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertEqual(body['status'], 'blocked')
        # Checked directly on the response body, not just the DB afterward
        # — the view's `target` was select_related("profile") before this
        # block created/updated that row, so a stale cache would silently
        # serialize None here even though the DB write succeeded.
        self.assertIsNotNone(body['blocked_at'])
        self.assertEqual(body['blocked_by'], 'admin')

        self.plain.refresh_from_db()
        self.assertFalse(self.plain.is_active)
        profile = UserProfile.objects.get(user=self.plain)
        self.assertIsNotNone(profile.blocked_at)
        self.assertEqual(profile.blocked_by, self.admin)

        response = self.client.post(reverse('api_user_unblock', args=[self.plain.id]), **headers)
        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertEqual(body['status'], 'active')
        self.assertIsNone(body['blocked_at'])
        self.assertIsNone(body['blocked_by'])

        self.plain.refresh_from_db()
        self.assertTrue(self.plain.is_active)
        profile.refresh_from_db()
        self.assertIsNone(profile.blocked_at)

    def test_blocked_user_cannot_obtain_a_new_token(self):
        admin_headers, _ = self.auth_headers('admin')
        self.client.post(reverse('api_user_block', args=[self.plain.id]), **admin_headers)

        response = self.client.post(
            reverse('token_obtain_pair'),
            data=json.dumps({'username': 'plain', 'password': 'pw-for-tests-1'}),
            content_type='application/json',
        )
        self.assertEqual(response.status_code, 401)

    def test_blocking_blacklists_outstanding_refresh_token(self):
        _, refresh = self.auth_headers('plain')
        admin_headers, _ = self.auth_headers('admin')

        self.client.post(reverse('api_user_block', args=[self.plain.id]), **admin_headers)

        response = self.client.post(
            reverse('token_refresh'),
            data=json.dumps({'refresh': refresh}),
            content_type='application/json',
        )
        self.assertEqual(response.status_code, 401)

    def test_self_action_guard_rejects_targeting_own_account(self):
        # Unit-tested directly: the Admins-tier queryset already excludes
        # every super-admin, including the acting user's own row if they
        # were one, and an Admin's own row is never in the Users tier
        # either — so this guard is unreachable end-to-end through either
        # HTTP endpoint today. Kept as defense-in-depth regardless
        # (docs/user-management-plan.md); tested directly instead of
        # pretending an unreachable HTTP scenario exists.
        from rest_framework.exceptions import PermissionDenied
        from rest_framework.test import APIRequestFactory

        from .permissions import assert_not_targeting_self

        request = APIRequestFactory().get('/')
        request.user = self.admin
        with self.assertRaises(PermissionDenied):
            assert_not_targeting_self(request, self.admin)

    def test_soft_delete_anonymizes_but_keeps_the_row_and_documents(self):
        doc = Document.objects.create(title='mine.txt', content='saya suka teh', user=self.plain)
        headers, _ = self.auth_headers('admin')

        response = self.client.delete(reverse('api_user_detail', args=[self.plain.id]), **headers)
        self.assertEqual(response.status_code, 204)

        self.assertTrue(User.objects.filter(pk=self.plain.id).exists())
        self.plain.refresh_from_db()
        self.assertFalse(self.plain.is_active)
        self.assertEqual(self.plain.username, f'deleted-user-{self.plain.id}')
        self.assertFalse(self.plain.has_usable_password())

        doc.refresh_from_db()
        self.assertEqual(doc.user_id, self.plain.id)  # Document survives, FK intact

        profile = UserProfile.objects.get(user=self.plain)
        self.assertIsNotNone(profile.deleted_at)
        self.assertEqual(profile.deleted_by, self.admin)

    def test_deleted_user_404s_on_every_endpoint_afterward(self):
        headers, _ = self.auth_headers('admin')
        self.client.delete(reverse('api_user_detail', args=[self.plain.id]), **headers)

        response = self.client.get(reverse('api_user_detail', args=[self.plain.id]), **headers)
        self.assertEqual(response.status_code, 404)

        response = self.client.post(reverse('api_user_block', args=[self.plain.id]), **headers)
        self.assertEqual(response.status_code, 404)


class AdminAccountApiTests(AccountApiTestCase):
    """Super Admin manages Admins — /api/admins/. Admin itself is NOT a
    superset here (the inverse of the Users-tier superset)."""

    def test_anonymous_cannot_list_admins(self):
        response = self.client.get(reverse('api_admin_list'))
        self.assertEqual(response.status_code, 401)

    def test_admin_cannot_list_admins(self):
        headers, _ = self.auth_headers('admin')
        response = self.client.get(reverse('api_admin_list'), **headers)
        self.assertEqual(response.status_code, 403)

    def test_super_admin_can_list_and_create_admins(self):
        headers, _ = self.auth_headers('superadmin')
        response = self.client.post(
            reverse('api_admin_list'),
            data=json.dumps({'username': 'newadmin', 'email': 'newadmin@example.com', 'password': 'a-strong-pw-1'}),
            content_type='application/json',
            **headers,
        )
        self.assertEqual(response.status_code, 201)
        body = response.json()
        self.assertTrue(body['is_staff'])
        self.assertFalse(body['is_superuser'])

        response = self.client.get(reverse('api_admin_list'), **headers)
        self.assertEqual(response.status_code, 200)
        usernames = {row['username'] for row in response.json()['results']}
        self.assertIn('newadmin', usernames)

    def test_admins_list_excludes_superadmins(self):
        headers, _ = self.auth_headers('superadmin')
        response = self.client.get(reverse('api_admin_list'), **headers)
        usernames = {row['username'] for row in response.json()['results']}
        self.assertNotIn('superadmin', usernames)

    def test_super_admin_gets_404_targeting_a_superadmin_via_admins_endpoint(self):
        headers, _ = self.auth_headers('superadmin')
        response = self.client.get(reverse('api_admin_detail', args=[self.super_admin.id]), **headers)
        self.assertEqual(response.status_code, 404)

    def test_super_admin_can_block_an_admin(self):
        headers, _ = self.auth_headers('superadmin')
        response = self.client.post(reverse('api_admin_block', args=[self.admin.id]), **headers)
        self.assertEqual(response.status_code, 200)
        self.admin.refresh_from_db()
        self.assertFalse(self.admin.is_active)


# --- Document metadata catalogue (docs/metadata-catalogue-plan.md) ---------

METADATA_CSV_HEADER = 'File name,University,Year,Grade,Topic,Topic (translated into English),Number of words,Name code'

# CRLF line endings and NO trailing newline, matching the real catalogue files
# exactly — a reader that doesn't handle those leaves a stray '\r' on every
# Name code, which is the single most likely way this importer breaks on real
# data while passing a hand-written LF fixture.
METADATA_CSV = '\r\n'.join([
    METADATA_CSV_HEADER,
    'TUFS2023KOMSHI314.txt,TUFS,2023,3,wawancara,interview,650,KOMSHI',
    'OU2023BETA202.txt,OU,2023,2,tempat wisata,tourist attraction,405,BETA',
    'OU2023BETA202.txt,OU,2023,2,argumentasi,argumentation,374,BETA',
])

HEADER_XML = """<document>
  <header><textfile>KUIS/TUFS2023KOMSHI314.txt</textfile><lang>indonesian</lang></header>
  <body>saya suka nasi goreng</body>
</document>"""


def _write_csv(tmpdir, name, text):
    path = tmpdir / name
    # newline='' so the \r\n in the fixture reaches the file verbatim rather
    # than being translated by Python's universal-newline writer.
    with open(path, 'w', newline='', encoding='utf-8') as handle:
        handle.write(text)
    return path


class MetadataMatchingTests(TestCase):
    """main/metadata_catalogue.py's match-key formula and upload-time linking."""

    def test_header_textfile_wins_over_the_uploaded_filename(self):
        from .metadata_catalogue import extract_textfile_name, match_key_for_document

        self.assertEqual(extract_textfile_name(HEADER_XML), 'TUFS2023KOMSHI314.txt')

        # Uploaded under a name that shares nothing with the catalogue's: the
        # header is what reconciles them.
        doc = Document.objects.create(title='whatever-the-user-called-it.xml', content=HEADER_XML)
        self.assertEqual(match_key_for_document(doc), 'tufs2023komshi314')

    def test_real_sample_with_an_eror_suffix_matches_the_catalogue_name(self):
        """The case the whole header-first decision exists for: the file on
        disk is KUIS2023FUA201-Eror.xml, the catalogue says
        KUIS2023FUA201.txt, and no suffix heuristic is involved."""
        from django.conf import settings

        from .metadata_catalogue import match_key_for_document

        content = (settings.BASE_DIR / 'info' / 'KUIS2023FUA201-Eror.xml').read_text(encoding='utf-8')
        doc = Document.objects.create(title='KUIS2023FUA201-Eror.xml', content=content)

        self.assertEqual(match_key_for_document(doc), 'kuis2023fua201')

    def test_falls_back_to_the_title_when_there_is_no_header(self):
        from .metadata_catalogue import extract_textfile_name, match_key_for_document

        doc = Document.objects.create(title='PLAIN2023.TXT', content='just some words')
        self.assertIsNone(extract_textfile_name('just some words'))
        self.assertEqual(match_key_for_document(doc), 'plain2023')

    def test_unparseable_xml_falls_back_rather_than_raising(self):
        from .metadata_catalogue import extract_textfile_name, match_key_for_document

        broken = '<document><header><textfile>x.txt</textfile>'
        self.assertIsNone(extract_textfile_name(broken))
        doc = Document.objects.create(title='fallback.xml', content=broken)
        self.assertEqual(match_key_for_document(doc), 'fallback')

    def test_match_key_strips_one_extension_and_the_directory(self):
        from .metadata_catalogue import normalize_match_key

        self.assertEqual(normalize_match_key('KUIS/A2023B.txt'), 'a2023b')
        self.assertEqual(normalize_match_key('A2023B.xml'), 'a2023b')
        self.assertEqual(normalize_match_key('  A2023B.TXT  '), 'a2023b')
        self.assertEqual(normalize_match_key('no-extension'), 'no-extension')
        self.assertEqual(normalize_match_key(''), '')
        # NOT stripped — a suffix is a different filename, not a variant.
        self.assertEqual(normalize_match_key('A2023B-Eror.xml'), 'a2023b-eror')

    def test_upload_links_the_document_to_an_already_loaded_entry(self):
        from .document_ingest import get_or_create_document
        from .models import DocumentMetadata

        DocumentMetadata.objects.create(
            source_filename='TUFS2023KOMSHI314.txt', match_key='tufs2023komshi314',
            university='TUFS', year=2023, grade=3, source_file='t.csv',
        )

        doc, created = get_or_create_document(title='anything.xml', content=HEADER_XML, user=None)

        self.assertTrue(created)
        self.assertEqual(doc.catalogue_entry.university, 'TUFS')

    def test_upload_without_a_catalogue_entry_still_succeeds(self):
        """A document the catalogue doesn't cover is a normal, supported state."""
        from .document_ingest import get_or_create_document
        from .models import DocumentMetadata

        doc, created = get_or_create_document(title='unknown.xml', content=HEADER_XML, user=None)

        self.assertTrue(created)
        self.assertEqual(DocumentMetadata.objects.count(), 0)
        self.assertFalse(hasattr(doc, 'catalogue_entry') and doc.catalogue_entry)

    def test_a_dedup_hit_does_not_relink_or_steal_the_entry(self):
        from .document_ingest import get_or_create_document
        from .models import DocumentMetadata

        DocumentMetadata.objects.create(
            source_filename='TUFS2023KOMSHI314.txt', match_key='tufs2023komshi314',
            source_file='t.csv',
        )

        first, created_first = get_or_create_document(title='a.xml', content=HEADER_XML, user=None)
        second, created_second = get_or_create_document(title='b.xml', content=HEADER_XML, user=None)

        self.assertTrue(created_first)
        self.assertFalse(created_second)
        self.assertEqual(first.id, second.id)
        self.assertEqual(DocumentMetadata.objects.get().document_id, first.id)

    def test_an_entry_already_claimed_is_not_moved_to_another_document(self):
        from .metadata_catalogue import link_catalogue_entry
        from .models import DocumentMetadata

        entry = DocumentMetadata.objects.create(
            source_filename='TUFS2023KOMSHI314.txt', match_key='tufs2023komshi314',
            source_file='t.csv',
        )
        first = Document.objects.create(title='first.xml', content=HEADER_XML, content_hash='h1')
        link_catalogue_entry(first)

        # Same header, different content (so no dedup) — the second document
        # must NOT steal the entry and orphan the first.
        second = Document.objects.create(
            title='second.xml', content=HEADER_XML.replace('nasi goreng', 'nasi'), content_hash='h2',
        )
        self.assertIsNone(link_catalogue_entry(second))

        entry.refresh_from_db()
        self.assertEqual(entry.document_id, first.id)


class LoadMetadataCatalogueCommandTests(TestCase):
    """main/management/commands/load_metadata_catalogue.py."""

    def setUp(self):
        import tempfile
        from pathlib import Path

        self._tmp = tempfile.TemporaryDirectory()
        self.tmpdir = Path(self._tmp.name)
        self.addCleanup(self._tmp.cleanup)
        self.csv_path = _write_csv(self.tmpdir, 'metadata_2023.csv', METADATA_CSV)

    def _run(self, *args):
        out = StringIO()
        call_command('load_metadata_catalogue', '--file', str(self.csv_path), *args, stdout=out)
        return out.getvalue()

    def test_imports_rows_and_strips_the_crlf_from_the_last_column(self):
        from .models import DocumentMetadata

        self._run()

        entry = DocumentMetadata.objects.get(match_key='tufs2023komshi314')
        self.assertEqual(entry.source_filename, 'TUFS2023KOMSHI314.txt')
        self.assertEqual(entry.university, 'TUFS')
        self.assertEqual(entry.year, 2023)
        self.assertEqual(entry.grade, 3)
        self.assertEqual(entry.topic, 'wawancara')
        self.assertEqual(entry.topic_en, 'interview')
        self.assertEqual(entry.word_count, 650)
        # The real failure mode: 'KOMSHI\r' instead of 'KOMSHI'.
        self.assertEqual(entry.name_code, 'KOMSHI')
        self.assertEqual(entry.source_file, 'metadata_2023.csv')

    def test_conflicting_duplicate_is_last_row_wins_and_is_reported(self):
        from .models import DocumentMetadata

        output = self._run()

        entry = DocumentMetadata.objects.get(match_key='ou2023beta202')
        self.assertEqual(entry.topic, 'argumentasi')   # the later row
        self.assertEqual(entry.word_count, 374)

        # Silently picking a winner by file order is exactly what has to be
        # visible, so the filename and both line numbers must be named.
        self.assertIn('OU2023BETA202.txt', output)
        self.assertIn('DIFFERING', output)
        self.assertIn('3', output)  # the later line number

    def test_three_rows_collapse_to_two_entries(self):
        from .models import DocumentMetadata

        self._run()
        self.assertEqual(DocumentMetadata.objects.count(), 2)

    def test_idempotent_rerun_updates_rather_than_duplicating(self):
        from .models import DocumentMetadata

        self._run()
        first = DocumentMetadata.objects.count()
        output = self._run()

        self.assertEqual(DocumentMetadata.objects.count(), first)
        self.assertIn('0 created', output)

    def test_rerun_keeps_an_already_resolved_document_link(self):
        """The reason this command upserts instead of delete-and-recreate."""
        from .models import DocumentMetadata

        self._run()
        doc = Document.objects.create(title='anything.xml', content=HEADER_XML, content_hash='h')
        call_command('load_metadata_catalogue', '--relink', stdout=StringIO())
        self.assertEqual(DocumentMetadata.objects.get(match_key='tufs2023komshi314').document_id, doc.id)

        self._run()

        self.assertEqual(DocumentMetadata.objects.get(match_key='tufs2023komshi314').document_id, doc.id)

    def test_dry_run_writes_nothing(self):
        from .models import DocumentMetadata

        output = self._run('--dry-run')

        self.assertEqual(DocumentMetadata.objects.count(), 0)
        self.assertIn('Dry run', output)

    def test_import_links_a_document_uploaded_beforehand(self):
        """The catalogue -> document direction: the file was already there."""
        from .models import DocumentMetadata

        doc = Document.objects.create(title='anything.xml', content=HEADER_XML, content_hash='h')

        output = self._run()

        self.assertEqual(DocumentMetadata.objects.get(match_key='tufs2023komshi314').document_id, doc.id)
        self.assertIn('1 linked', output)

    def test_relink_resolves_documents_uploaded_after_the_import(self):
        from .models import DocumentMetadata

        self._run()
        self.assertIsNone(DocumentMetadata.objects.get(match_key='tufs2023komshi314').document_id)

        # Created directly, bypassing the upload path's own linking, so this
        # exercises --relink rather than get_or_create_document.
        doc = Document.objects.create(title='anything.xml', content=HEADER_XML, content_hash='h')

        out = StringIO()
        call_command('load_metadata_catalogue', '--relink', stdout=out)

        self.assertEqual(DocumentMetadata.objects.get(match_key='tufs2023komshi314').document_id, doc.id)
        self.assertIn('Relinked', out.getvalue())

    def test_non_numeric_year_is_dropped_to_null_rather_than_failing_the_row(self):
        from .models import DocumentMetadata

        path = _write_csv(self.tmpdir, 'bad.csv', '\r\n'.join([
            METADATA_CSV_HEADER,
            'X2023A.txt,TUFS,n/a,3,topik,topic,100,AAA',
        ]))
        out = StringIO()
        call_command('load_metadata_catalogue', '--file', str(path), stdout=out)

        entry = DocumentMetadata.objects.get(match_key='x2023a')
        self.assertIsNone(entry.year)
        self.assertEqual(entry.university, 'TUFS')   # the rest of the row survives
        self.assertIn('non-numeric year', out.getvalue())

    def test_missing_required_column_is_a_command_error(self):
        from django.core.management.base import CommandError

        path = _write_csv(self.tmpdir, 'nofilename.csv', 'University,Year\r\nTUFS,2023')
        with self.assertRaises(CommandError):
            call_command('load_metadata_catalogue', '--file', str(path), stdout=StringIO())

    def test_header_is_matched_case_and_whitespace_insensitively(self):
        from .models import DocumentMetadata

        path = _write_csv(self.tmpdir, 'messy.csv', '\r\n'.join([
            ' FILE NAME ,university,YEAR,Grade,Topic,Topic (Translated Into English),number of words,NAME CODE',
            'X2023A.txt,TUFS,2023,3,topik,topic,100,AAA',
        ]))
        call_command('load_metadata_catalogue', '--file', str(path), stdout=StringIO())

        entry = DocumentMetadata.objects.get(match_key='x2023a')
        self.assertEqual(entry.university, 'TUFS')
        self.assertEqual(entry.topic_en, 'topic')
        self.assertEqual(entry.name_code, 'AAA')

    def test_utf8_bom_is_tolerated(self):
        """Re-exporting the catalogue from Excel routinely adds one."""
        from .models import DocumentMetadata

        path = _write_csv(self.tmpdir, 'bom.csv', '﻿' + '\r\n'.join([
            METADATA_CSV_HEADER,
            'X2023A.txt,TUFS,2023,3,topik,topic,100,AAA',
        ]))
        call_command('load_metadata_catalogue', '--file', str(path), stdout=StringIO())

        self.assertEqual(DocumentMetadata.objects.get(match_key='x2023a').university, 'TUFS')

    def test_missing_file_is_a_command_error(self):
        from django.core.management.base import CommandError

        with self.assertRaises(CommandError):
            call_command('load_metadata_catalogue', '--file', 'nope.csv', stdout=StringIO())

    def test_relink_on_an_empty_catalogue_says_so_instead_of_failing(self):
        out = StringIO()
        call_command('load_metadata_catalogue', '--relink', stdout=out)
        self.assertIn('catalogue is empty', out.getvalue())

    def test_loads_the_real_repo_catalogue(self):
        """Against metadata/*.csv as actually committed, not a fixture — the
        two conflicting filenames in metadata_2023.csv are real data."""
        from .models import DocumentMetadata

        out = StringIO()
        call_command('load_metadata_catalogue', stdout=out)
        output = out.getvalue()

        self.assertGreater(DocumentMetadata.objects.count(), 1500)
        self.assertIn('OU2023BETA202.txt', output)
        self.assertIn('OU2023OUSA202.txt', output)
        # Nothing should have been skipped as unusable.
        self.assertNotIn('unusable File name', output)


class MetadataFilterViewTests(ExplorerTestCase):
    """The secondary metadata filter on the Django analysis surface — applied
    once in main/views.py::_get_selected_documents, so every analysis page and
    every /export/ route inherits it (docs/metadata-catalogue-plan.md).

    `annotated` is fully described, `plain` has an entry with every facet empty,
    and a third document has no entry at all — the three states the "(no value)"
    option has to reconcile.
    """

    @classmethod
    def setUpTestData(cls):
        super().setUpTestData()

        from .models import DocumentMetadata

        cls.uncatalogued = Document.objects.create(
            title='uncatalogued.txt', content=PLAIN_TEXT + ' extra', content_hash='uncat-hash',
        )
        index_document.delay(cls.uncatalogued.id)
        cls.corpus.documents.add(cls.uncatalogued)

        DocumentMetadata.objects.create(
            source_filename='annotated.txt', match_key='annotated', document=cls.annotated,
            university='TUFS', year=2023, grade=3, topic='wawancara', topic_en='interview',
            word_count=650, name_code='KOMSHI', source_file='t.csv',
        )
        DocumentMetadata.objects.create(
            source_filename='plain.txt', match_key='plain', document=cls.plain,
            source_file='t.csv',
        )
        # Covers a file nobody uploaded — must never widen a result.
        DocumentMetadata.objects.create(
            source_filename='absent.txt', match_key='absent', document=None,
            university='SFC', year=2024, grade=1, source_file='t.csv',
        )

    def titles(self, **params):
        """Document titles in scope for a given filter, via the real view path."""
        from .metadata_catalogue import metadata_filter_q, parse_facet_selection
        from .models import Document

        self.select(self.corpus)
        request = self.client.get(reverse('word_frequency'), params).wsgi_request

        selection = parse_facet_selection(request.GET)
        return set(
            Document.objects.filter(corpora__id__in=[self.corpus.id])
            .filter(metadata_filter_q(selection))
            .distinct()
            .values_list('title', flat=True)
        )

    def test_no_filter_includes_every_document(self):
        self.assertEqual(
            self.titles(),
            {'annotated.xml', 'plain.txt', 'uncatalogued.txt'},
        )

    def test_concrete_value_narrows_to_that_document(self):
        self.assertEqual(self.titles(university='TUFS'), {'annotated.xml'})

    def test_unset_selects_both_the_empty_field_and_the_absent_entry(self):
        """The load-bearing case: plain.txt has an entry with no university,
        uncatalogued.txt has no entry at all, and both read as "no value"."""
        self.assertEqual(
            self.titles(university='__none__'),
            {'plain.txt', 'uncatalogued.txt'},
        )

    def test_value_or_unset_within_a_facet_is_a_union(self):
        self.assertEqual(
            self.titles(university=['TUFS', '__none__']),
            {'annotated.xml', 'plain.txt', 'uncatalogued.txt'},
        )

    def test_facets_combine_with_and(self):
        self.assertEqual(self.titles(university='TUFS', grade='3'), {'annotated.xml'})
        self.assertEqual(self.titles(university='TUFS', grade='1'), set())

    def test_numeric_facet_unset_works(self):
        self.assertEqual(self.titles(grade='__none__'), {'plain.txt', 'uncatalogued.txt'})
        self.assertEqual(self.titles(year='2023'), {'annotated.xml'})

    def test_entry_without_a_document_never_widens_the_result(self):
        self.assertEqual(self.titles(university='SFC'), set())
        self.assertEqual(self.titles(year='2024'), set())

    def test_name_code_matches_on_prefix(self):
        self.assertEqual(self.titles(name_code='KOM'), {'annotated.xml'})
        self.assertEqual(self.titles(name_code='kom'), {'annotated.xml'})
        self.assertEqual(self.titles(name_code='OMSHI'), set())

    def test_junk_on_a_numeric_facet_widens_rather_than_erroring(self):
        self.assertEqual(
            self.titles(grade='not-a-number'),
            {'annotated.xml', 'plain.txt', 'uncatalogued.txt'},
        )

    def test_analysis_pages_apply_the_filter_and_still_render(self):
        self.select(self.corpus)

        for name in ['word_frequency', 'collocations', 'ngrams', 'dashboard', 'analysis_home']:
            with self.subTest(view=name):
                response = self.client.get(reverse(name), {'university': 'TUFS'})
                self.assertEqual(response.status_code, 200)

        response = self.client.get(reverse('kwic'), {'q': 'saya', 'university': 'TUFS'})
        self.assertEqual(response.status_code, 200)

    def test_filtering_changes_the_reported_token_total(self):
        self.select(self.corpus)

        unfiltered = self.client.get(reverse('word_frequency')).context['selection_token_total']
        filtered = self.client.get(
            reverse('word_frequency'), {'university': 'TUFS'}
        ).context['selection_token_total']

        self.assertGreater(unfiltered, filtered)
        self.assertGreater(filtered, 0)

    def test_unset_and_value_partition_the_corpus_token_total(self):
        self.select(self.corpus)

        def total(**params):
            return self.client.get(reverse('word_frequency'), params).context['selection_token_total']

        self.assertEqual(
            total(university='TUFS') + total(university='__none__'),
            total(),
        )

    def test_exports_inherit_the_filter(self):
        """The /export/ routes resolve documents through the same function, so
        a filtered export must not silently dump the whole corpus."""
        self.select(self.corpus)

        unfiltered = self.client.get(reverse('word_frequency_export_csv'))
        filtered = self.client.get(reverse('word_frequency_export_csv'), {'university': 'TUFS'})

        self.assertEqual(filtered.status_code, 200)
        self.assertLess(len(filtered.content), len(unfiltered.content))

    def test_context_bar_renders_the_facet_controls(self):
        self.select(self.corpus)
        response = self.client.get(reverse('word_frequency'))
        html = response.content.decode()

        self.assertIn('Metadata filter', html)
        self.assertIn('name="university"', html)
        self.assertIn('value="TUFS"', html)
        self.assertIn('(no value)', html)
        # SFC exists only on an entry with no document, so it must not be offered.
        self.assertNotIn('value="SFC"', html)

    def test_facet_params_are_not_emitted_twice_inside_the_context_form(self):
        """context_bar.html's `hidden_params exclude` must name every facet.
        That form holds the real checkboxes, so a hidden input for the same
        param there would submit every value twice.

        Scoped to that one form on purpose: the results toolbar's own forms
        (filter text, per-page) SHOULD carry the facets as hidden inputs —
        that's what stops applying a text filter from wiping the metadata
        filter. test_results_toolbar_carries_the_filter_forward covers that
        side.
        """
        self.select(self.corpus)
        html = self.client.get(
            reverse('word_frequency'), {'university': 'TUFS'}
        ).content.decode()

        start = html.index('<form method="get" class="context"')
        context_form = html[start:html.index('</form>', start)]

        for facet in ['university', 'year', 'grade', 'name_code']:
            with self.subTest(facet=facet):
                self.assertNotIn(f'<input type="hidden" name="{facet}"', context_form)
                # ...while the real control for it is present.
                self.assertIn(f'name="{facet}"', context_form)

    def test_results_toolbar_carries_the_filter_forward(self):
        """Applying a text filter, changing page size, sorting or exporting must
        all preserve the metadata filter — templatetags/kuis.py's hidden_params
        and qs tags do this for any GET param, so it needs no per-facet code."""
        self.select(self.corpus)
        html = self.client.get(
            reverse('word_frequency'), {'university': 'TUFS'}
        ).content.decode()

        self.assertIn('<input type="hidden" name="university" value="TUFS">', html)
        self.assertIn('export/?university=TUFS', html)
        self.assertIn('?university=TUFS&amp;sort=', html)

    def test_filter_state_is_marked_active_in_the_context_bar(self):
        self.select(self.corpus)

        plain = self.client.get(reverse('word_frequency'))
        self.assertFalse(plain.context['metadata_filter_active'])

        filtered = self.client.get(reverse('word_frequency'), {'university': 'TUFS'})
        self.assertTrue(filtered.context['metadata_filter_active'])
        self.assertIn('filtered', filtered.content.decode())

    def test_facet_values_come_only_from_linked_entries(self):
        from .metadata_catalogue import facet_values

        values = facet_values()
        self.assertEqual(values['university'], ['TUFS'])
        self.assertEqual(values['year'], [2023])
        self.assertEqual(values['grade'], [3])


class DocumentMetadataApiTests(ExplorerTestCase):
    """GET /api/documents/ exposing each document's catalogue entry
    (docs/metadata-catalogue-plan.md). Extends ExplorerTestCase for its users
    and JWT helper, same as DocumentApiTests."""

    @classmethod
    def setUpTestData(cls):
        super().setUpTestData()

        from .models import DocumentMetadata

        cls.bare = Document.objects.create(
            title='bare.xml', content=PLAIN_TEXT + ' more', content_hash='bare-hash',
        )
        DocumentMetadata.objects.create(
            source_filename='annotated.txt', match_key='annotated', document=cls.annotated,
            university='TUFS', year=2023, grade=3, topic='wawancara', topic_en='interview',
            word_count=650, name_code='KOMSHI', source_file='t.csv',
        )

    def auth_headers(self, username='researcher'):
        response = self.client.post(
            reverse('token_obtain_pair'),
            data=json.dumps({'username': username, 'password': 'pw-for-tests-1'}),
            content_type='application/json',
        )
        return {'HTTP_AUTHORIZATION': f"Bearer {response.json()['access']}"}

    def _by_title(self, **params):
        response = self.client.get(reverse('api_document_list'), params, **self.auth_headers())
        self.assertEqual(response.status_code, 200)
        return {row['title']: row for row in response.json()}

    def test_metadata_is_nested_on_each_document(self):
        rows = self._by_title()

        self.assertEqual(rows['annotated.xml']['metadata']['university'], 'TUFS')
        self.assertEqual(rows['annotated.xml']['metadata']['word_count'], 650)
        self.assertEqual(rows['annotated.xml']['metadata']['topic_en'], 'interview')

    def test_a_document_without_metadata_reports_null_not_an_error(self):
        self.assertIsNone(self._by_title()['bare.xml']['metadata'])

    def test_has_metadata_filter(self):
        without = set(self._by_title(has_metadata='false'))
        self.assertIn('bare.xml', without)
        self.assertNotIn('annotated.xml', without)

        with_meta = set(self._by_title(has_metadata='true'))
        self.assertEqual(with_meta, {'annotated.xml'})
