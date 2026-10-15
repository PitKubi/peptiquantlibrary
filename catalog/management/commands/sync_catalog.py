import os

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError

from catalog.annotation_service import annotate_sequences
from catalog.models import FastaAnnotation, SourcePeptideLot
from catalog.publish import STANDARD_SEQUENCE, publish_catalog
from catalog.sync.sources import rows_from_sysquan_db, rows_from_xlsx
from catalog.sync.upsert import upsert_rows


class Command(BaseCommand):
    help = ('Refresh the lot table from a source (--from-db reads the SysQuan '
            'Postgres via the read-only SYSQUAN_DB_DSN role, --from-xlsx reads '
            'a catalog spreadsheet), then republish the public catalog.')

    def add_arguments(self, parser):
        parser.add_argument('--from-db', action='store_true')
        parser.add_argument('--from-xlsx', metavar='PATH')
        parser.add_argument('--default-label-type', default='SIS',
                            help='Label type assumed for spreadsheet rows without one')
        parser.add_argument('--dry-run', action='store_true')
        parser.add_argument('--no-publish', action='store_true',
                            help='Sync lots only; skip rebuilding the public table')

    def handle(self, *args, **options):
        if options['from_db']:
            dsn = settings.SYSQUAN_DB_DSN
            if not dsn:
                raise CommandError('SYSQUAN_DB_DSN is not set')
            rows = rows_from_sysquan_db(dsn)
            source_label = 'sysquan_db'
        elif options['from_xlsx']:
            rows = rows_from_xlsx(options['from_xlsx'],
                                  default_label_type=options['default_label_type'])
            source_label = f'xlsx:{options["from_xlsx"]}'
        else:
            raise CommandError('Pass --from-db or --from-xlsx PATH')

        run = upsert_rows(rows, source_label, dry_run=options['dry_run'])
        prefix = 'DRY RUN ' if options['dry_run'] else ''
        self.stdout.write(
            f'{prefix}{source_label}: seen={run.rows_seen} created={run.rows_created} '
            f'updated={run.rows_updated} unchanged={run.rows_unchanged} '
            f'skipped={run.rows_skipped} in {run.duration_seconds}s')
        if run.error:
            raise CommandError(run.error)
        if options['dry_run']:
            return
        self._annotate_new_sequences()
        if not options['no_publish']:
            publish_run, rejected = publish_catalog()
            self.stdout.write(
                f'published {publish_run.rows_created} public peptides '
                f'({publish_run.rows_skipped} non-standard sequences held back)')

    def _annotate_new_sequences(self):
        fasta_paths = [p for p in settings.PQL_FASTA_PATHS if os.path.exists(p)]
        missing_fastas = [p for p in settings.PQL_FASTA_PATHS
                          if not os.path.exists(p)]
        if missing_fastas:
            self.stderr.write(f'PQL_FASTA_PATHS missing on disk, skipped: '
                              f'{missing_fastas}')
        if not fasta_paths:
            return
        sequences = {
            s.strip().upper()
            for s in SourcePeptideLot.objects.values_list(
                'peptide_sequence', flat=True).distinct()
            if STANDARD_SEQUENCE.match(s.strip().upper())
        }
        never_annotated = sequences - set(
            FastaAnnotation.objects.filter(peptide_sequence__in=sequences)
            .values_list('peptide_sequence', flat=True))
        if never_annotated:
            summary = annotate_sequences(never_annotated, fasta_paths,
                                         log=self.stdout.write)
            self.stdout.write(f'auto-annotated new sequences: {summary}')
