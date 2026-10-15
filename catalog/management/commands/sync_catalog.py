from django.conf import settings
from django.core.management.base import BaseCommand, CommandError

from catalog.sync.sources import rows_from_sysquan_db, rows_from_xlsx
from catalog.sync.upsert import upsert_rows


class Command(BaseCommand):
    help = ('Refresh the public catalog from a source: --from-db reads the '
            'SysQuan Postgres via the read-only SYSQUAN_DB_DSN role, '
            '--from-xlsx reads a catalog spreadsheet.')

    def add_arguments(self, parser):
        parser.add_argument('--from-db', action='store_true')
        parser.add_argument('--from-xlsx', metavar='PATH')
        parser.add_argument('--default-label-type', default='SIS',
                            help='Label type assumed for spreadsheet rows without one')
        parser.add_argument('--dry-run', action='store_true')

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
