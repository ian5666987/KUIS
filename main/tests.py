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

from .models import Corpus, Document
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
