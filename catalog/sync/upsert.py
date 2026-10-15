import time

from catalog.models import CatalogPeptide, SyncRun

UPSERT_FIELDS = [
    'mrmp_id', 'peptide_sequence', 'label_type', 'label_tag', 'native_mutant',
    'species', 'phospho_present', 'uniprot_id', 'gene_name', 'protein_name',
    'mass_confirmed', 'purity_confirmed', 'cze_purity', 'aaa_ratio',
    'in_stock', 'workflow_status',
]


def upsert_rows(rows, source_label, dry_run=False):
    """Apply normalized row dicts to CatalogPeptide, recording a SyncRun.
    Returns the SyncRun (unsaved when dry_run)."""
    started = time.monotonic()
    run = SyncRun(source=source_label)
    existing = {
        (p.source, p.source_key): p
        for p in CatalogPeptide.objects.all()
    }
    try:
        for row in rows:
            run.rows_seen += 1
            if not row['peptide_sequence'] or not row['source_key']:
                run.rows_skipped += 1
                continue
            current = existing.get((row['source'], row['source_key']))
            if current is None:
                run.rows_created += 1
                if not dry_run:
                    CatalogPeptide.objects.create(
                        source=row['source'], source_key=row['source_key'],
                        **{f: row[f] for f in UPSERT_FIELDS})
                continue
            changed = [f for f in UPSERT_FIELDS if getattr(current, f) != row[f]]
            if not changed:
                run.rows_unchanged += 1
                continue
            run.rows_updated += 1
            if not dry_run:
                for f in changed:
                    setattr(current, f, row[f])
                current.save(update_fields=changed + ['synced_at'])
        run.ok = True
    except Exception as exc:
        run.error = f'{type(exc).__name__}: {exc}'
    run.duration_seconds = round(time.monotonic() - started, 2)
    if not dry_run:
        run.save()
    return run
