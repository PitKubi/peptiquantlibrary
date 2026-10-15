import json

from django.core.management.base import BaseCommand

from catalog.models import CatalogPeptide
from catalog.sync.upsert import UPSERT_FIELDS


class Command(BaseCommand):
    help = ('Dump the full catalog as JSON. Together with import_catalog this '
            'is the server-migration path: export, copy, import.')

    def add_arguments(self, parser):
        parser.add_argument('path')

    def handle(self, *args, **options):
        rows = [
            {'source': p.source, 'source_key': p.source_key,
             **{f: getattr(p, f) for f in UPSERT_FIELDS}}
            for p in CatalogPeptide.objects.all()
        ]
        with open(options['path'], 'w') as f:
            json.dump(rows, f, indent=1, default=str)
        self.stdout.write(f'Wrote {len(rows)} peptides to {options["path"]}')
