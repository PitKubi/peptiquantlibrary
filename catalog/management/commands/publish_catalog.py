from django.core.management.base import BaseCommand

from catalog.publish import publish_catalog


class Command(BaseCommand):
    help = 'Rebuild the public one-row-per-peptide table from lots + annotations.'

    def handle(self, *args, **options):
        run, rejected = publish_catalog()
        self.stdout.write(
            f'published {run.rows_created} public peptides from {run.rows_seen} '
            f'eligible lots in {run.duration_seconds}s; '
            f'{run.rows_skipped} non-standard sequences held back')
        for lot in rejected[:10]:
            self.stdout.write(f'  held back: {lot.source_key} {lot.peptide_sequence!r}')
