import json
import tempfile

from django.test import TestCase, override_settings

from .annotation import assignment_from_matches, match_peptides
from .models import (FastaAnnotation, PublicPeptide, QuoteRequest,
                     SourcePeptideLot)
from .publish import normalize_species, publish_catalog
from .sync.sources import normalize_label_tag, rows_from_xlsx
from .sync.upsert import upsert_rows


def lot_row(**overrides):
    row = {
        'source': 'sysquan_workflow', 'source_key': 'MRMP-1',
        'mrmp_id': 'MRMP-1', 'peptide_sequence': 'LVNEVTEFAK',
        'label_type': 'SIS', 'label_tag': 'K+8', 'native_mutant': 'Native',
        'species_raw': 'Homo Sapiens', 'phospho_present': False,
        'uniprot_id': 'P02768', 'gene_name': 'ALB', 'protein_name': '',
        'mass_confirmed': True, 'purity_confirmed': True,
        'cze_purity': 0.95, 'aaa_ratio': 1.01,
        'workflow_status': '9_inventory', 'project_category': 'In-house',
    }
    row.update(overrides)
    return row


class UpsertTests(TestCase):
    def test_create_update_unchanged(self):
        run = upsert_rows([lot_row()], 'test')
        self.assertEqual((run.rows_created, run.rows_updated), (1, 0))

        run = upsert_rows([lot_row()], 'test')
        self.assertEqual((run.rows_created, run.rows_unchanged), (0, 1))

        run = upsert_rows([lot_row(gene_name='ALB2')], 'test')
        self.assertEqual(run.rows_updated, 1)
        self.assertEqual(SourcePeptideLot.objects.get().gene_name, 'ALB2')

    def test_dry_run_writes_nothing(self):
        run = upsert_rows([lot_row()], 'test', dry_run=True)
        self.assertEqual(run.rows_created, 1)
        self.assertEqual(SourcePeptideLot.objects.count(), 0)


class PublishTests(TestCase):
    def test_one_row_per_sequence_with_availability(self):
        upsert_rows([
            lot_row(),
            lot_row(source_key='MRMP-2', mrmp_id='MRMP-2',
                    label_type='NAT', label_tag='-'),
            lot_row(source_key='MRMP-2b', mrmp_id='MRMP-2b', label_tag='K+8'),
            lot_row(source_key='MRMP-3', mrmp_id='MRMP-3',
                    peptide_sequence='AGLQFPVGR', gene_name='H2AX',
                    uniprot_id='P16104', label_tag='R+10'),
        ], 'test')
        run, rejected = publish_catalog()
        self.assertEqual(PublicPeptide.objects.count(), 2)
        alb = PublicPeptide.objects.get(peptide_sequence='LVNEVTEFAK')
        self.assertTrue(alb.sis_available)
        self.assertTrue(alb.nat_available)
        self.assertEqual(alb.lot_count, 3)
        self.assertEqual(alb.sis_label_tags, 'K+8')
        self.assertEqual(alb.species, 'Human')
        self.assertEqual(alb.annotation_source, 'curated')
        h2ax = PublicPeptide.objects.get(peptide_sequence='AGLQFPVGR')
        self.assertFalse(h2ax.nat_available)

    def test_ineligible_and_nonstandard_lots_held_back(self):
        upsert_rows([
            lot_row(workflow_status='4_purification'),
            lot_row(source_key='MRMP-4', mrmp_id='MRMP-4',
                    peptide_sequence='FGRAK(+8)LLGAEE'),
        ], 'test')
        run, rejected = publish_catalog()
        self.assertEqual(PublicPeptide.objects.count(), 0)
        self.assertEqual(len(rejected), 1)

    def test_client_and_private_projects_never_publish(self):
        upsert_rows([
            lot_row(project_category='Acme Pharma - Client'),
            lot_row(source_key='MRMP-5', mrmp_id='MRMP-5',
                    peptide_sequence='AGLQFPVGR', project_category='private'),
        ], 'test')
        publish_catalog()
        self.assertEqual(PublicPeptide.objects.count(), 0)

    def test_client_lot_does_not_hide_sequence_with_public_lot(self):
        upsert_rows([
            lot_row(project_category='Client'),
            lot_row(source_key='MRMP-6', mrmp_id='MRMP-6',
                    label_type='NAT', label_tag='-'),
        ], 'test')
        publish_catalog()
        alb = PublicPeptide.objects.get(peptide_sequence='LVNEVTEFAK')
        self.assertFalse(alb.sis_available)
        self.assertTrue(alb.nat_available)
        self.assertEqual(alb.lot_count, 1)

    def test_nat_only_sequence_publishes(self):
        upsert_rows([lot_row(label_type='NAT', label_tag='-')], 'test')
        publish_catalog()
        alb = PublicPeptide.objects.get(peptide_sequence='LVNEVTEFAK')
        self.assertFalse(alb.sis_available)
        self.assertTrue(alb.nat_available)

    def test_fasta_fills_blanks_but_curated_wins(self):
        upsert_rows([
            lot_row(gene_name='', uniprot_id='', species_raw=''),
            lot_row(source_key='MRMP-5', mrmp_id='MRMP-5',
                    peptide_sequence='AGLQFPVGR', gene_name='CURATED',
                    uniprot_id='P16104', label_tag='R+10'),
        ], 'test')
        FastaAnnotation.objects.create(
            peptide_sequence='LVNEVTEFAK', uniprot_id='P02768',
            gene_name='ALB', protein_name='Serum albumin',
            species='Homo sapiens', match_count=1)
        FastaAnnotation.objects.create(
            peptide_sequence='AGLQFPVGR', uniprot_id='OTHER',
            gene_name='FASTA_GENE', protein_name='Other', species='Human',
            match_count=1)
        publish_catalog()
        filled = PublicPeptide.objects.get(peptide_sequence='LVNEVTEFAK')
        self.assertEqual(filled.gene_name, 'ALB')
        self.assertEqual(filled.protein_name, 'Serum albumin')
        self.assertEqual(filled.species, 'Human')
        self.assertEqual(filled.annotation_source, 'fasta')
        curated = PublicPeptide.objects.get(peptide_sequence='AGLQFPVGR')
        self.assertEqual(curated.gene_name, 'CURATED')
        self.assertEqual(curated.uniprot_id, 'P16104')

    def test_non_human_mouse_species_stay_internal(self):
        upsert_rows([
            lot_row(),
            lot_row(source_key='MRMP-BOV', mrmp_id='MRMP-BOV',
                    peptide_sequence='LVNELTEFAK',
                    species_raw='Bos taurus (Bovine)'),
        ], 'test')
        publish_catalog()
        sequences = set(PublicPeptide.objects.values_list(
            'peptide_sequence', flat=True))
        self.assertEqual(sequences, {'LVNEVTEFAK'})

    def test_species_normalization(self):
        self.assertEqual(normalize_species('homo Sapiens'), 'Human')
        self.assertEqual(normalize_species('Macaca fascicularis'), 'Macaque')
        self.assertEqual(normalize_species(''), '')
        self.assertEqual(normalize_species('rattus norvegicus'),
                         'Rattus norvegicus')
        self.assertEqual(normalize_species('Mus musculus + Homo sapiens'),
                         'Human + Mouse')

    def test_conserved_peptide_lists_both_species(self):
        upsert_rows([lot_row(gene_name='', uniprot_id='', species_raw='')],
                    'test')
        FastaAnnotation.objects.create(
            peptide_sequence='LVNEVTEFAK', uniprot_id='',
            gene_name='ALB', protein_name='Albumin',
            species='Homo sapiens + Mus musculus', match_count=2)
        publish_catalog()
        public = PublicPeptide.objects.get()
        self.assertEqual(public.species, 'Human + Mouse')

        response = self.client.get('/', {'species': 'Mouse'})
        self.assertContains(response, 'LVNEVTEFAK')


FASTA = """>sp|P02768|ALBU_HUMAN Serum albumin OS=Homo sapiens (Human) OX=9606 GN=ALB PE=1 SV=2
MKWVTFISLLFLFSSAYSLVNEVTEFAKTCVADESAENCDK
>sp|P16104|H2AX_HUMAN Histone H2AX OS=Homo sapiens (Human) OX=9606 GN=H2AX PE=1 SV=2
AGLQFPVGRVHRLLRKGNY
>sp|Q00001|FAKE1_HUMAN Shared protein one OS=Homo sapiens (Human) OX=9606 GN=SHRD PE=1 SV=1
AAASHAREDPEPK
>sp|Q00002|FAKE2_HUMAN Shared protein two OS=Homo sapiens (Human) OX=9606 GN=SHRD PE=1 SV=1
GGGSHAREDPEPK
>sp|Q00003|FAKE3_HUMAN Ambiguous protein OS=Homo sapiens (Human) OX=9606 GN=AMBI PE=1 SV=1
CCCSHAREDPEPK
"""


class AnnotationTests(TestCase):
    def _fasta_path(self):
        f = tempfile.NamedTemporaryFile(mode='w', suffix='.fasta', delete=False)
        f.write(FASTA)
        f.close()
        return f.name

    def test_unique_match_annotates_fully(self):
        matches, proteins, positions = match_peptides({'LVNEVTEFAK'},
                                                      [self._fasta_path()])
        assignment = assignment_from_matches(matches['LVNEVTEFAK'])
        self.assertEqual(assignment['uniprot_id'], 'P02768')
        self.assertEqual(assignment['gene_name'], 'ALB')
        self.assertEqual(assignment['protein_name'], 'Serum albumin')
        self.assertEqual(assignment['species'], 'Human')
        self.assertEqual(assignment['match_count'], 1)
        self.assertIn('LVNEVTEFAK', proteins['P02768'])
        self.assertEqual(positions[('LVNEVTEFAK', 'P02768')], 18)

    def test_shared_peptide_gets_representative_accession(self):
        matches, _, _ = match_peptides({'SHAREDPEPK'}, [self._fasta_path()])
        entries = matches['SHAREDPEPK']
        self.assertEqual(len(entries), 3)
        assignment = assignment_from_matches(entries)
        self.assertEqual(assignment['gene_name'], 'SHRD')
        self.assertEqual(assignment['uniprot_id'], 'Q00001')
        self.assertEqual(assignment['match_count'], 3)

    def test_unmatched_scores_zero(self):
        matches, _, _ = match_peptides({'WWWWWWWWWW'}, [self._fasta_path()])
        assignment = assignment_from_matches(matches.get('WWWWWWWWWW', []))
        self.assertEqual(assignment['match_count'], 0)

    def test_variant_peptide_maps_to_parent_protein(self):
        upsert_rows([lot_row(peptide_sequence='LVNEVTEAAK', gene_name='',
                             uniprot_id='', species_raw='',
                             native_mutant='Mutant')], 'test')
        from django.core.management import call_command
        call_command('annotate_from_fasta', self._fasta_path())
        annotation = FastaAnnotation.objects.get(peptide_sequence='LVNEVTEAAK')
        self.assertTrue(annotation.variant_match)
        self.assertEqual(annotation.uniprot_id, 'P02768')
        self.assertEqual(annotation.match_start, 18)
        public = PublicPeptide.objects.get()
        self.assertEqual(public.uniprot_id, 'P02768')
        response = self.client.get('/peptide/LVNEVTEAAK/')
        self.assertContains(response, 'variant peptide')
        self.assertContains(response, '<mark')

    def test_command_end_to_end(self):
        upsert_rows([lot_row(gene_name='', uniprot_id='', species_raw='')],
                    'test')
        from django.core.management import call_command
        call_command('annotate_from_fasta', self._fasta_path())
        public = PublicPeptide.objects.get()
        self.assertEqual(public.gene_name, 'ALB')
        self.assertEqual(public.annotation_source, 'fasta')
        annotation = FastaAnnotation.objects.get(peptide_sequence='LVNEVTEFAK')
        self.assertIn('LVNEVTEFAK', annotation.protein_sequence)

    def test_detail_highlights_peptide_in_protein(self):
        upsert_rows([lot_row(gene_name='', uniprot_id='', species_raw='')],
                    'test')
        from django.core.management import call_command
        call_command('annotate_from_fasta', self._fasta_path())
        response = self.client.get('/peptide/LVNEVTEFAK/')
        self.assertContains(response, '<mark')
        self.assertContains(response, 'Peptide in protein')
        self.assertContains(response, 'P02768')

    def test_republish_keeps_row_ids_and_urls(self):
        upsert_rows([lot_row()], 'test')
        publish_catalog()
        row_id = PublicPeptide.objects.get().id
        publish_catalog()
        self.assertEqual(PublicPeptide.objects.get().id, row_id)
        response = self.client.get('/peptide/LVNEVTEFAK/')
        self.assertEqual(response.status_code, 200)


class SyncAutoAnnotationTests(TestCase):
    def test_sync_annotates_sequences_it_has_never_seen(self):
        import openpyxl
        workbook = openpyxl.Workbook()
        sheet = workbook.active
        sheet.append(['Peptide Sequence', 'Label', 'NAT available'])
        sheet.append(['LVNEVTEFAK', 'K+8', 'yes'])
        xlsx = tempfile.NamedTemporaryFile(suffix='.xlsx', delete=False)
        workbook.save(xlsx.name)
        fasta = tempfile.NamedTemporaryFile(mode='w', suffix='.fasta',
                                            delete=False)
        fasta.write(FASTA)
        fasta.close()

        from django.core.management import call_command
        with override_settings(PQL_FASTA_PATHS=[fasta.name]):
            call_command('sync_catalog', '--from-xlsx', xlsx.name)
        public = PublicPeptide.objects.get()
        self.assertEqual(public.gene_name, 'ALB')
        self.assertEqual(public.uniprot_id, 'P02768')
        self.assertEqual(public.annotation_source, 'fasta')


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

    def test_nat_available_emits_second_lot(self):
        path = self._xlsx(
            ['Peptide Sequence', 'Gene', 'UniProt', 'Label', 'NAT available'],
            [['LVNEVTEFAK', 'ALB', 'P02768', 'K+8', 'yes'],
             ['AGLQFPVGR', 'H2AX', 'P16104', 'R+10', '']])
        rows = list(rows_from_xlsx(path))
        self.assertEqual(len(rows), 3)
        self.assertEqual(rows[1]['label_type'], 'NAT')
        self.assertEqual(rows[1]['peptide_sequence'], 'LVNEVTEFAK')

    def test_missing_sequence_column_raises(self):
        path = self._xlsx(['Gene', 'UniProt'], [['ALB', 'P02768']])
        with self.assertRaises(ValueError):
            list(rows_from_xlsx(path))

    def test_nat_label_defaults_to_dash(self):
        self.assertEqual(normalize_label_tag('', 'NAT'), '-')
        self.assertEqual(normalize_label_tag('', 'SIS'), '')

    def test_placeholder_annotations_blanked(self):
        path = self._xlsx(
            ['Peptide Sequence', 'Gene', 'UniProt', 'Species'],
            [['LVNEVTEFAK', '#N/A', '0', 'n/a'],
             ['AGLQFPVGR', 'H2AX', 'P16104', 'Human']])
        rows = list(rows_from_xlsx(path))
        self.assertEqual((rows[0]['gene_name'], rows[0]['uniprot_id'],
                          rows[0]['species_raw']), ('', '', ''))
        self.assertEqual(rows[1]['gene_name'], 'H2AX')


class PublicViewTests(TestCase):
    def setUp(self):
        upsert_rows([
            lot_row(),
            lot_row(source_key='MRMP-2', mrmp_id='MRMP-2',
                    label_type='NAT', label_tag='-'),
            lot_row(source_key='MRMP-3', mrmp_id='MRMP-3',
                    peptide_sequence='AGLQFPVGR', gene_name='H2AX',
                    uniprot_id='P16104', label_tag='R+10'),
        ], 'test')
        publish_catalog()

    def test_landing_shows_catalog_and_stats(self):
        response = self.client.get('/')
        self.assertContains(response, 'LVNEVTEFAK')
        self.assertContains(response, 'AGLQFPVGR')

    def test_search_and_availability_filter(self):
        response = self.client.get('/', {'q': 'ALB'})
        self.assertContains(response, 'LVNEVTEFAK')
        self.assertNotContains(response, 'AGLQFPVGR')

        response = self.client.get('/', {'availability': 'nat'})
        self.assertContains(response, 'LVNEVTEFAK')
        self.assertNotContains(response, 'AGLQFPVGR')

    def test_bulk_paste_matches_and_reports_unmatched(self):
        response = self.client.get('/', {'bulk': 'ALB\nP16104, NOSUCHGENE'})
        self.assertContains(response, 'LVNEVTEFAK')
        self.assertContains(response, 'AGLQFPVGR')
        self.assertContains(response, 'NOSUCHGENE')

    def test_selection_and_quote_flow(self):
        alb = PublicPeptide.objects.get(peptide_sequence='LVNEVTEFAK')
        response = self.client.post('/selection/', {
            'action': 'add', 'peptide': [alb.id], 'next': '/'})
        self.assertRedirects(response, '/')

        response = self.client.get('/request/')
        self.assertContains(response, 'LVNEVTEFAK')

        response = self.client.post('/request/', {
            'submit_request': '1',
            f'sis_{alb.id}': 'on', f'nat_{alb.id}': 'on',
            'name': 'Jane Doe', 'email': 'jane@example.org',
            'organization': 'Example Lab', 'message': '2 nmol each'})
        self.assertRedirects(response, '/request/done/')
        quote = QuoteRequest.objects.get()
        item = quote.items.get()
        self.assertTrue(item.want_sis)
        self.assertTrue(item.want_nat)
        self.assertEqual(self.client.session['selection'], [])

    def test_quote_requires_contact_and_a_form(self):
        alb = PublicPeptide.objects.get(peptide_sequence='LVNEVTEFAK')
        self.client.post('/selection/', {'action': 'add', 'peptide': [alb.id],
                                         'next': '/'})
        response = self.client.post('/request/', {
            'submit_request': '1', 'name': 'Jane', 'email': 'jane@example.org'})
        self.assertContains(response, 'required')
        self.assertEqual(QuoteRequest.objects.count(), 0)

    def test_added_rows_show_in_list_mark_and_stay_visible(self):
        alb = PublicPeptide.objects.get(peptide_sequence='LVNEVTEFAK')
        self.client.post('/selection/', {'action': 'add', 'peptide': [alb.id],
                                         'next': '/'})
        response = self.client.get('/')
        self.assertContains(response, 'LVNEVTEFAK')
        self.assertContains(response, 'in list')
        self.assertNotContains(response, f'name="peptide" value="{alb.id}"')
        self.assertContains(response, 'id="check-page"')

    def test_selection_remove(self):
        alb = PublicPeptide.objects.get(peptide_sequence='LVNEVTEFAK')
        self.client.post('/selection/', {'action': 'add', 'peptide': [alb.id],
                                         'next': '/'})
        self.client.post('/selection/', {'action': 'remove', 'peptide': alb.id,
                                         'next': '/request/'})
        self.assertEqual(self.client.session['selection'], [])


@override_settings(PQL_ADMIN_TOKEN='sekret')
class AdminApiTests(TestCase):
    def test_token_gate(self):
        self.assertEqual(self.client.get('/api/admin/health/').status_code, 403)
        response = self.client.get('/api/admin/health/',
                                   headers={'X-Admin-Token': 'sekret'})
        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertIn('git_head', body)
        self.assertIn('curation_gaps', body)

    @override_settings(PQL_ADMIN_TOKEN='')
    def test_unconfigured_token_refuses(self):
        self.assertEqual(self.client.get('/api/admin/health/').status_code, 503)

    def test_test_email_sends_via_configured_backend(self):
        response = self.client.post(
            '/api/admin/test-email/', '{"to": "pk@example.org"}',
            content_type='application/json', headers={'X-Admin-Token': 'sekret'})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()['sent'], 1)
        from django.core import mail
        self.assertEqual(mail.outbox[0].to, ['pk@example.org'])

    def test_quote_requests_listing_and_handling(self):
        upsert_rows([lot_row()], 'test')
        publish_catalog()
        peptide = PublicPeptide.objects.get()
        self.client.post('/selection/', {'action': 'add',
                                         'peptide': [peptide.id], 'next': '/'})
        self.client.post('/request/', {
            'submit_request': '1', f'sis_{peptide.id}': 'on',
            'name': 'Jane', 'email': 'jane@example.org'})
        response = self.client.get('/api/admin/quote-requests/',
                                   headers={'X-Admin-Token': 'sekret'})
        listed = response.json()['quote_requests']
        self.assertEqual(len(listed), 1)
        self.assertIn('[SIS]', listed[0]['items'][0])

        response = self.client.post(
            '/api/admin/quote-requests/mark-handled/',
            json.dumps({'id': listed[0]['id']}),
            content_type='application/json',
            headers={'X-Admin-Token': 'sekret'})
        self.assertEqual(response.json()['updated'], 1)


class ExportImportTests(TestCase):
    def test_round_trip_republishes(self):
        upsert_rows([lot_row(),
                     lot_row(source_key='MRMP-2', mrmp_id='MRMP-2',
                             label_type='NAT', label_tag='-')], 'test')
        FastaAnnotation.objects.create(
            peptide_sequence='LVNEVTEFAK', uniprot_id='P02768',
            gene_name='ALB', match_count=1)
        from django.core.management import call_command
        with tempfile.NamedTemporaryFile(suffix='.json', delete=False) as f:
            call_command('export_catalog', f.name)
            SourcePeptideLot.objects.all().delete()
            FastaAnnotation.objects.all().delete()
            PublicPeptide.objects.all().delete()
            call_command('import_catalog', f.name)
        self.assertEqual(SourcePeptideLot.objects.count(), 2)
        self.assertEqual(FastaAnnotation.objects.count(), 1)
        public = PublicPeptide.objects.get()
        self.assertTrue(public.sis_available and public.nat_available)
