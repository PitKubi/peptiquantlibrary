import os

from django.core.management.base import BaseCommand, CommandError

from catalog.annotation_service import annotate_sequences
from catalog.models import SourcePeptideLot
from catalog.publish import STANDARD_SEQUENCE, publish_catalog


class Command(BaseCommand):
    help = ('Match ALL catalog peptide sequences against reference proteome '
            'FASTA(s) and store the annotations, then republish. Peptides with '
            'no exact hit get a second, single-substitution pass so mutant/'
            'variant peptides map to their parent protein. Curated SysQuan '
            'values always win over these at publish time. Day-to-day, '
            'sync_catalog annotates NEW sequences by itself via '
            'PQL_FASTA_PATHS; this command is for full re-runs after a FASTA '
            'update.')

    def add_arguments(self, parser):
        parser.add_argument('fasta', nargs='+', help='UniProt-format FASTA path(s)')
        parser.add_argument('--no-publish', action='store_true')

    def handle(self, *args, **options):
        for path in options['fasta']:
            if not os.path.exists(path):
                raise CommandError(f'{path} not found')

        sequences = {
            s.strip().upper()
            for s in SourcePeptideLot.objects.values_list(
                'peptide_sequence', flat=True).distinct()
            if STANDARD_SEQUENCE.match(s.strip().upper())
        }
        summary = annotate_sequences(sequences, options['fasta'],
                                     log=self.stdout.write)
        self.stdout.write(f'annotations: {summary}')
        if not options['no_publish']:
            run, rejected = publish_catalog()
            self.stdout.write(f'published {run.rows_created} public peptides')
