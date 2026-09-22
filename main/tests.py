"""Smoke tests for the Corpus Explorer interface.

They exercise the parts of the redesign that are easy to break silently: every
page rendering, the corpus selection surviving navigation, the text-mode toggle
changing what is counted, and the table controls (filter, sort, paginate,
export) agreeing with each other.
"""

import json

from django.contrib.auth.models import User
from django.test import TestCase
from django.urls import reverse

from .models import Corpus, Document

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
