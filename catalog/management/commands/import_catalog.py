import json

from django.core.management.base import BaseCommand

from catalog.sync.upsert import upsert_rows


class Command(BaseCommand):
    help = 'Load a JSON dump written by export_catalog (upsert, not replace).'

    def add_arguments(self, parser):
        parser.add_argument('path')
        parser.add_argument('--dry-run', action='store_true')

    def handle(self, *args, **options):
        with open(options['path']) as f:
            rows = json.load(f)
        run = upsert_rows(rows, f'import:{options["path"]}',
                          dry_run=options['dry_run'])
        self.stdout.write(
            f'seen={run.rows_seen} created={run.rows_created} '
            f'updated={run.rows_updated} unchanged={run.rows_unchanged} '
            f'skipped={run.rows_skipped}')
