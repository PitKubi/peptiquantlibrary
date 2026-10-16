import json

from django.core.management.base import BaseCommand

from catalog.audit import audit_catalog


class Command(BaseCommand):
    help = ('Re-derive every published claim (annotation integrity, '
            'live-table drift, page coherence) and print the findings as '
            'JSON. Exits non-zero when any error-level finding exists.')

    def handle(self, *args, **options):
        report = audit_catalog()
        self.stdout.write(json.dumps(report, indent=2))
        if not report['ok']:
            raise SystemExit(1)
