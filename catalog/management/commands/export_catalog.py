import json

from django.core.management.base import BaseCommand

from catalog.models import FastaAnnotation, SourcePeptideLot
from catalog.sync.upsert import UPSERT_FIELDS

ANNOTATION_FIELDS = ['peptide_sequence', 'uniprot_id', 'gene_name',
                     'protein_name', 'species', 'protein_sequence',
                     'match_count', 'fasta_label']


class Command(BaseCommand):
    help = ('Dump lots + FASTA annotations as JSON. Together with '
            'import_catalog this is the server-migration path: export, copy, '
            'import (which republishes).')

    def add_arguments(self, parser):
        parser.add_argument('path')

    def handle(self, *args, **options):
        payload = {
            'lots': [
                {'source': lot.source, 'source_key': lot.source_key,
                 **{f: getattr(lot, f) for f in UPSERT_FIELDS}}
                for lot in SourcePeptideLot.objects.all()
            ],
            'annotations': [
                {f: getattr(a, f) for f in ANNOTATION_FIELDS}
                for a in FastaAnnotation.objects.all()
            ],
        }
        with open(options['path'], 'w') as f:
            json.dump(payload, f, indent=1, default=str)
        self.stdout.write(f'Wrote {len(payload["lots"])} lots and '
                          f'{len(payload["annotations"])} annotations to '
                          f'{options["path"]}')
