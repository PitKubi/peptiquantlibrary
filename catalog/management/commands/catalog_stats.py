import json

from django.core.management.base import BaseCommand
from django.db.models import Count

from catalog.models import (FastaAnnotation, PublicPeptide, QuoteRequest,
                            SourcePeptideLot, SyncRun)


def catalog_stats():
    public = PublicPeptide.objects
    last_sync = SyncRun.objects.exclude(source='publish').first()
    last_publish = SyncRun.objects.filter(source='publish').first()
    published_sequences = public.values_list('peptide_sequence', flat=True)
    return {
        'public_peptides': public.count(),
        'sis_available': public.filter(sis_available=True).count(),
        'nat_available': public.filter(nat_available=True).count(),
        'both_available': public.filter(sis_available=True,
                                        nat_available=True).count(),
        'proteins': (public.exclude(uniprot_id='')
                     .values('uniprot_id').distinct().count()),
        'by_species': dict(public.values_list('species').annotate(n=Count('id'))),
        'by_annotation_source': dict(
            public.values_list('annotation_source').annotate(n=Count('id'))),
        'lots_total': SourcePeptideLot.objects.count(),
        'curation_gaps': {
            'missing_gene': public.filter(gene_name='').count(),
            'missing_uniprot': public.filter(uniprot_id='').count(),
            'missing_species': public.filter(species='').count(),
            'fasta_multi_mapped': FastaAnnotation.objects.filter(
                match_count__gt=1,
                peptide_sequence__in=published_sequences).count(),
            'fasta_unmatched': FastaAnnotation.objects.filter(
                match_count=0,
                peptide_sequence__in=published_sequences).count(),
        },
        'quote_requests_new': QuoteRequest.objects.filter(status='new').count(),
        'last_sync': _run_summary(last_sync),
        'last_publish': _run_summary(last_publish),
    }


def _run_summary(run):
    if run is None:
        return None
    return {
        'started_at': run.started_at.isoformat(),
        'source': run.source,
        'ok': run.ok,
        'rows_seen': run.rows_seen,
        'created': run.rows_created,
        'updated': run.rows_updated,
        'error': run.error,
    }


class Command(BaseCommand):
    help = 'Print catalog health counts as JSON.'

    def handle(self, *args, **options):
        self.stdout.write(json.dumps(catalog_stats(), indent=2, default=str))
