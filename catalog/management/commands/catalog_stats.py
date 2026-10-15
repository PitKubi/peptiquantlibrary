import json

from django.core.management.base import BaseCommand
from django.db.models import Count

from catalog.models import CatalogPeptide, QuoteRequest, SyncRun


def catalog_stats():
    by = lambda field: dict(  # noqa: E731 — tiny local aggregator
        CatalogPeptide.objects.values_list(field).annotate(n=Count('id')))
    last_sync = SyncRun.objects.first()
    return {
        'peptides_total': CatalogPeptide.objects.count(),
        'in_stock': CatalogPeptide.objects.filter(in_stock=True).count(),
        'by_label_type': by('label_type'),
        'by_source': by('source'),
        'by_species': by('species'),
        'quote_requests_new': QuoteRequest.objects.filter(status='new').count(),
        'last_sync': None if last_sync is None else {
            'started_at': last_sync.started_at.isoformat(),
            'source': last_sync.source,
            'ok': last_sync.ok,
            'rows_seen': last_sync.rows_seen,
            'created': last_sync.rows_created,
            'updated': last_sync.rows_updated,
            'error': last_sync.error,
        },
    }


class Command(BaseCommand):
    help = 'Print catalog health counts as JSON.'

    def handle(self, *args, **options):
        self.stdout.write(json.dumps(catalog_stats(), indent=2, default=str))
