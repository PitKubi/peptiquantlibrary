"""Build the public one-row-per-peptide catalog from the raw lots plus the
FASTA annotations. Rebuilt wholesale on every call: the public table is a
derived artifact, the lots and annotations are the state."""
import re
import time
from collections import defaultdict

from django.db import transaction

from .models import FastaAnnotation, PublicPeptide, SourcePeptideLot, SyncRun

STANDARD_SEQUENCE = re.compile(r'^[ACDEFGHIKLMNPQRSTVWY]{4,}$')

# Maria's species column is free text (57% blank, mixed spellings in the May
# audit); the public page shows a controlled vocabulary.
SPECIES_NORMALIZATION = {
    'homo sapiens': 'Human', 'human': 'Human', 'homo sapiens (human)': 'Human',
    'mus musculus': 'Mouse', 'mouse': 'Mouse',
    'macaca': 'Macaque', 'macaque': 'Macaque', 'macaca fascicularis': 'Macaque',
    'macaca mulatta': 'Macaque',
    'human + macaca': 'Human + Macaque',
}


def normalize_species(raw):
    value = (raw or '').strip()
    if not value:
        return ''
    if '+' in value:
        parts = [normalize_species(p) for p in value.split('+')]
        return ' + '.join(sorted({p for p in parts if p}))
    lowered = value.lower()
    if lowered in SPECIES_NORMALIZATION:
        return SPECIES_NORMALIZATION[lowered]
    if 'macaca' in lowered or 'macaque' in lowered:
        return 'Macaque'
    return value[:1].upper() + value[1:]


# The public page lists human and mouse only (Peter, 2026-10-15); the
# occasional other-species standard (BSA, macaque ATXN3, plant spike-ins)
# stays internal. Rows with no species information are kept.
PUBLIC_SPECIES = {'Human', 'Mouse'}


def _species_is_public(species):
    if not species:
        return True
    return all(part in PUBLIC_SPECIES for part in species.split(' + '))


def _first_non_empty(values):
    for value in values:
        if value and value.strip():
            return value.strip()
    return ''


# Maria's native_mutant column and the spreadsheets' WT/MT column are free
# text; placeholder tokens mean "not recorded", never wild type.
NATIVE_MUTANT_NORMALIZATION = {
    'native': 'wildtype', 'wt': 'wildtype', 'wild type': 'wildtype',
    'wild-type': 'wildtype', 'wildtype': 'wildtype',
    'mutant': 'mutant', 'mt': 'mutant', 'mut': 'mutant', 'mutation': 'mutant',
}


def normalize_native_mutant(raw):
    return NATIVE_MUTANT_NORMALIZATION.get((raw or '').strip().lower(), '')


# Below this length an exact proteome hit is too likely by chance (a 5-mer is
# expected several times in a proteome) to justify a wild-type call.
MIN_WILDTYPE_INFERENCE_LENGTH = 7


def derive_sequence_variant(lots, annotation):
    """Curated WT/mutant wins; lots that disagree stay NA rather than guessed.
    Without curation the only defensible inference is wild type from an exact
    proteome match of a tryptic-length peptide. A single-substitution match is
    NOT evidence of mutation (a wild-type peptide of a species outside the
    loaded FASTAs, an isoform junction or a TrEMBL-only protein are equally
    likely; 31 such rows were published as "Mutant" before 2026-10-16), so it
    stays NA."""
    curated = {normalize_native_mutant(l.native_mutant) for l in lots} - {''}
    if len(curated) == 1:
        return curated.pop()
    if len(curated) > 1:
        return ''
    if (annotation is not None and annotation.match_count
            and not annotation.variant_match
            and len(annotation.peptide_sequence) >= MIN_WILDTYPE_INFERENCE_LENGTH):
        return 'wildtype'
    return ''


def eligible_lots_by_sequence():
    """(lots grouped by normalized sequence, rejected) over the eligible lots;
    rejected lists the ones whose sequence is not a plain peptide. Shared by
    publish and the accuracy audit so both see the same catalog."""
    lots_by_sequence = defaultdict(list)
    rejected = []
    for lot in SourcePeptideLot.objects.all():
        if not lot.is_catalog_eligible():
            continue
        sequence = lot.peptide_sequence.strip().upper()
        if STANDARD_SEQUENCE.match(sequence):
            lots_by_sequence[sequence].append(lot)
        else:
            rejected.append(lot)
    return lots_by_sequence, rejected


def build_public_rows():
    """({sequence: field dict} of what the public table should contain,
    rejected lots, eligible lot count). Pure derivation: publish_catalog
    persists it, the accuracy audit diffs it against the live table."""
    lots_by_sequence, rejected = eligible_lots_by_sequence()

    annotations = {
        a.peptide_sequence: a
        for a in FastaAnnotation.objects.filter(
            peptide_sequence__in=list(lots_by_sequence))
    }

    rows = {}
    for sequence, lots in lots_by_sequence.items():
        curated = {
            'gene_name': _first_non_empty(l.gene_name for l in lots),
            'uniprot_id': _first_non_empty(l.uniprot_id for l in lots),
            'protein_name': _first_non_empty(l.protein_name for l in lots),
            'species': normalize_species(
                _first_non_empty(l.species_raw for l in lots)),
        }
        fasta = annotations.get(sequence)
        used_curated = any(curated.values())
        used_fasta = False
        if fasta is not None:
            for field in ('gene_name', 'uniprot_id', 'protein_name', 'species'):
                if not curated[field] and getattr(fasta, field):
                    value = getattr(fasta, field)
                    curated[field] = (normalize_species(value)
                                      if field == 'species' else value)
                    used_fasta = True
        if not _species_is_public(curated['species']):
            continue
        sis_lots = [l for l in lots if l.label_type == 'SIS']
        tags = sorted({l.label_tag for l in sis_lots
                       if l.label_tag and l.label_tag != '-'})
        rows[sequence] = dict(
            sis_available=bool(sis_lots),
            nat_available=any(l.label_type == 'NAT' for l in lots),
            sis_label_tags=', '.join(tags),
            sequence_variant=derive_sequence_variant(lots, fasta),
            annotation_source=('curated+fasta' if used_curated and used_fasta
                               else 'curated' if used_curated
                               else 'fasta' if used_fasta else ''),
            lot_count=len(lots),
            **curated,
        )
    lots_seen = sum(len(lots) for lots in lots_by_sequence.values())
    return rows, rejected, lots_seen


def publish_catalog():
    """Returns (SyncRun, rejected) where rejected lists eligible lots whose
    sequence is not a plain peptide (kept out of the public page, surfaced in
    the curation report)."""
    started = time.monotonic()
    run = SyncRun(source='publish')
    rows, rejected, lots_seen = build_public_rows()

    # Upsert keyed by sequence, never delete-and-recreate: row ids (and with
    # them open pages, bookmarks and session selections) must survive a
    # republish. Found the hard way when every publish 404'd the detail links
    # of already-open pages.
    with transaction.atomic():
        existing = {p.peptide_sequence: p for p in PublicPeptide.objects.all()}
        for sequence, values in rows.items():
            current = existing.get(sequence)
            if current is None:
                PublicPeptide.objects.create(peptide_sequence=sequence, **values)
                continue
            changed = [f for f, v in values.items() if getattr(current, f) != v]
            if changed:
                for f in changed:
                    setattr(current, f, values[f])
                current.save(update_fields=changed + ['published_at'])
        gone = set(existing) - set(rows)
        if gone:
            PublicPeptide.objects.filter(peptide_sequence__in=gone).delete()

    run.rows_seen = lots_seen + len(rejected)
    run.rows_created = len(rows)
    run.rows_skipped = len(rejected)
    run.ok = True
    run.duration_seconds = round(time.monotonic() - started, 2)
    run.save()
    return run, rejected
