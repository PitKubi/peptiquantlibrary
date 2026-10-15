import json

from django.core.management.base import BaseCommand

from catalog.management.commands.export_catalog import ANNOTATION_FIELDS
from catalog.models import FastaAnnotation
from catalog.publish import publish_catalog
from catalog.sync.upsert import upsert_rows


class Command(BaseCommand):
    help = ('Load a JSON dump written by export_catalog (upsert, not replace), '
            'then republish.')

    def add_arguments(self, parser):
        parser.add_argument('path')
        parser.add_argument('--dry-run', action='store_true')

    def handle(self, *args, **options):
        with open(options['path']) as f:
            payload = json.load(f)
        run = upsert_rows(payload['lots'], f'import:{options["path"]}',
                          dry_run=options['dry_run'])
        self.stdout.write(
            f'lots: seen={run.rows_seen} created={run.rows_created} '
            f'updated={run.rows_updated} unchanged={run.rows_unchanged} '
            f'skipped={run.rows_skipped}')
        if options['dry_run']:
            return
        for row in payload.get('annotations', []):
            FastaAnnotation.objects.update_or_create(
                peptide_sequence=row['peptide_sequence'],
                defaults={f: row[f] for f in ANNOTATION_FIELDS
                          if f != 'peptide_sequence'})
        publish_run, rejected = publish_catalog()
        self.stdout.write(f'published {publish_run.rows_created} public peptides')
