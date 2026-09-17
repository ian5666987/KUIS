"""Smoke tests for the Corpus Explorer interface.

They exercise the parts of the redesign that are easy to break silently: every
page rendering, the corpus selection surviving navigation, the text-mode toggle
changing what is counted, and the table controls (filter, sort, paginate,
export) agreeing with each other.
"""

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

        self.assertEqual(response.context['window'], 15)
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
