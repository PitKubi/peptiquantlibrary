import json
import tempfile

from django.test import TestCase, override_settings

from .models import CatalogPeptide, QuoteRequest
from .sync.sources import normalize_label_tag, rows_from_xlsx
from .sync.upsert import upsert_rows


def workflow_row(**overrides):
    row = {
        'source': 'sysquan_workflow', 'source_key': 'MRMP-1',
        'mrmp_id': 'MRMP-1', 'peptide_sequence': 'LVNEVTEFAK',
        'label_type': 'SIS', 'label_tag': 'K+8', 'native_mutant': 'Native',
        'species': 'Human', 'phospho_present': False,
        'uniprot_id': 'P02768', 'gene_name': 'ALB', 'protein_name': 'Serum albumin',
        'mass_confirmed': True, 'purity_confirmed': True,
        'cze_purity': 0.95, 'aaa_ratio': 1.01,
        'in_stock': True, 'workflow_status': '9_inventory',
    }
    row.update(overrides)
    return row


class UpsertTests(TestCase):
    def test_create_update_unchanged(self):
        run = upsert_rows([workflow_row()], 'test')
        self.assertEqual((run.rows_created, run.rows_updated), (1, 0))

        run = upsert_rows([workflow_row()], 'test')
        self.assertEqual((run.rows_created, run.rows_unchanged), (0, 1))

        run = upsert_rows([workflow_row(in_stock=False)], 'test')
        self.assertEqual(run.rows_updated, 1)
        self.assertFalse(CatalogPeptide.objects.get().in_stock)

    def test_dry_run_writes_nothing(self):
        run = upsert_rows([workflow_row()], 'test', dry_run=True)
        self.assertEqual(run.rows_created, 1)
        self.assertEqual(CatalogPeptide.objects.count(), 0)

    def test_blank_sequence_skipped(self):
        run = upsert_rows([workflow_row(peptide_sequence='')], 'test')
        self.assertEqual(run.rows_skipped, 1)
        self.assertEqual(CatalogPeptide.objects.count(), 0)


class LabelNormalizationTests(TestCase):
    def test_nat_without_tag_becomes_dash(self):
        self.assertEqual(normalize_label_tag('', 'NAT'), '-')

    def test_sis_without_tag_stays_empty(self):
        self.assertEqual(normalize_label_tag('', 'SIS'), '')


class XlsxSourceTests(TestCase):
    def _xlsx(self, header, rows):
        import openpyxl
        workbook = openpyxl.Workbook()
        sheet = workbook.active
        sheet.append(header)
        for row in rows:
            sheet.append(row)
        f = tempfile.NamedTemporaryFile(suffix='.xlsx', delete=False)
        workbook.save(f.name)
        return f.name

    def test_header_aliases_and_rows(self):
        path = self._xlsx(
            ['Peptide Sequence', 'Gene', 'UniProt', 'Label', 'Species'],
            [['LVNEVTEFAK', 'ALB', 'P02768', 'K+8', 'Human'],
             ['', '', '', '', ''],
             ['AGLQFPVGR', 'H2AX', 'P16104', 'R+10', 'Human']])
        rows = list(rows_from_xlsx(path))
        self.assertEqual(len(rows), 2)
        self.assertEqual(rows[0]['gene_name'], 'ALB')
        self.assertEqual(rows[0]['label_type'], 'SIS')
        self.assertEqual(rows[1]['label_tag'], 'R+10')

    def test_missing_sequence_column_raises(self):
        path = self._xlsx(['Gene', 'UniProt'], [['ALB', 'P02768']])
        with self.assertRaises(ValueError):
            list(rows_from_xlsx(path))


class PublicViewTests(TestCase):
    def setUp(self):
        upsert_rows([
            workflow_row(),
            workflow_row(source_key='MRMP-2', mrmp_id='MRMP-2',
                         label_type='NAT', label_tag='-', in_stock=False),
            workflow_row(source_key='MRMP-3', mrmp_id='MRMP-3',
                         peptide_sequence='AGLQFPVGR', gene_name='H2AX',
                         uniprot_id='P16104', protein_name='Histone H2AX'),
        ], 'test')

    def test_search_by_gene_and_filters(self):
        response = self.client.get('/', {'q': 'ALB'})
        self.assertContains(response, 'LVNEVTEFAK')
        self.assertNotContains(response, 'AGLQFPVGR')

        response = self.client.get('/', {'q': 'ALB', 'label_type': 'SIS'})
        self.assertContains(response, 'in stock')

        response = self.client.get('/', {'q': 'ALB', 'in_stock': '1'})
        self.assertEqual(
            len(response.context['page'].object_list), 1)

    def test_detail_links_counterpart(self):
        sis = CatalogPeptide.objects.get(mrmp_id='MRMP-1')
        response = self.client.get(f'/peptide/{sis.id}/')
        self.assertContains(response, 'Same sequence, other label')

    def test_quote_request_flow(self):
        peptide = CatalogPeptide.objects.get(mrmp_id='MRMP-1')
        response = self.client.post('/request/', {'peptide': [peptide.id]})
        self.assertContains(response, 'LVNEVTEFAK')

        response = self.client.post('/request/', {
            'submit_request': '1', 'peptide': [peptide.id],
            'name': 'Jane Doe', 'email': 'jane@example.org',
            'organization': 'Example Lab', 'message': '2 nmol each'})
        self.assertRedirects(response, '/request/done/')
        quote = QuoteRequest.objects.get()
        self.assertEqual(quote.items.count(), 1)
        self.assertEqual(quote.items.get().peptide_sequence, 'LVNEVTEFAK')

    def test_quote_request_requires_contact(self):
        peptide = CatalogPeptide.objects.get(mrmp_id='MRMP-1')
        response = self.client.post('/request/', {
            'submit_request': '1', 'peptide': [peptide.id], 'name': '', 'email': ''})
        self.assertContains(response, 'required')
        self.assertEqual(QuoteRequest.objects.count(), 0)


@override_settings(PQL_ADMIN_TOKEN='sekret')
class AdminApiTests(TestCase):
    def test_token_gate(self):
        self.assertEqual(self.client.get('/api/admin/health/').status_code, 403)
        response = self.client.get('/api/admin/health/',
                                   headers={'X-Admin-Token': 'sekret'})
        self.assertEqual(response.status_code, 200)
        self.assertIn('git_head', response.json())

    @override_settings(PQL_ADMIN_TOKEN='')
    def test_unconfigured_token_refuses(self):
        self.assertEqual(self.client.get('/api/admin/health/').status_code, 503)

    def test_quote_requests_listing_and_handling(self):
        upsert_rows([workflow_row()], 'test')
        peptide = CatalogPeptide.objects.get()
        self.client.post('/request/', {
            'submit_request': '1', 'peptide': [peptide.id],
            'name': 'Jane', 'email': 'jane@example.org'})
        response = self.client.get('/api/admin/quote-requests/',
                                   headers={'X-Admin-Token': 'sekret'})
        listed = response.json()['quote_requests']
        self.assertEqual(len(listed), 1)

        response = self.client.post(
            '/api/admin/quote-requests/mark-handled/',
            json.dumps({'id': listed[0]['id']}),
            content_type='application/json',
            headers={'X-Admin-Token': 'sekret'})
        self.assertEqual(response.json()['updated'], 1)


class ExportImportTests(TestCase):
    def test_round_trip(self):
        upsert_rows([workflow_row(),
                     workflow_row(source_key='MRMP-2', mrmp_id='MRMP-2',
                                  label_type='NAT', label_tag='-')], 'test')
        from django.core.management import call_command
        with tempfile.NamedTemporaryFile(suffix='.json', delete=False) as f:
            call_command('export_catalog', f.name)
            CatalogPeptide.objects.all().delete()
            call_command('import_catalog', f.name)
        self.assertEqual(CatalogPeptide.objects.count(), 2)
        restored = CatalogPeptide.objects.get(mrmp_id='MRMP-1')
        self.assertTrue(restored.in_stock)
        self.assertEqual(restored.cze_purity, 0.95)
