"""Accuracy audit of the published catalog. Two independent angles:

1. Annotation integrity, checked against the stored protein sequences
   themselves (not against the code that wrote them): an exact annotation's
   peptide must occur in its protein, a variant annotation must differ from
   the aligned region by exactly one residue.
2. Live-table drift: the PublicPeptide rows must equal what build_public_rows
   derives right now from lots + annotations, field by field.

Built after Christoph found a detail page whose highlighted region was not
the peptide (2026-10-16): every claim the site renders is re-derived here.
Exposed as the audit management command and GET api/admin/audit/.
"""
from .models import FastaAnnotation, PublicPeptide
from .publish import build_public_rows

EXAMPLE_LIMIT = 20

PUBLIC_FIELDS = ('gene_name', 'uniprot_id', 'protein_name', 'species',
                 'sis_available', 'nat_available', 'sis_label_tags',
                 'sequence_variant', 'kit_names', 'annotation_source',
                 'lot_count')


def audit_catalog():
    findings = _Findings()
    public = list(PublicPeptide.objects.all())
    annotations = {
        a.peptide_sequence: a
        for a in FastaAnnotation.objects.filter(
            peptide_sequence__in=[p.peptide_sequence for p in public])
    }
    for annotation in annotations.values():
        _check_annotation_integrity(annotation, findings)
    _check_public_vs_derived(public, findings)
    for peptide in public:
        _check_page_coherence(peptide, annotations.get(peptide.peptide_sequence),
                              findings)
    return {
        'ok': not findings.errors,
        'public_peptides': len(public),
        'annotations_checked': len(annotations),
        'forms': {
            'paired': sum(1 for p in public if p.sis_available and p.nat_available),
            'sis_only': sum(1 for p in public if p.sis_available and not p.nat_available),
            'nat_only': sum(1 for p in public if not p.sis_available and p.nat_available),
        },
        'sequence_variant': {
            'wildtype': sum(1 for p in public if p.sequence_variant == 'wildtype'),
            'mutant': sum(1 for p in public if p.sequence_variant == 'mutant'),
            'mutant_inferred': sum(1 for p in public
                                   if p.sequence_variant == 'mutant_inferred'),
            'na': sum(1 for p in public if not p.sequence_variant),
        },
        'error_total': sum(e['count'] for e in findings.errors.values()),
        'warning_total': sum(w['count'] for w in findings.warnings.values()),
        'errors': findings.errors,
        'warnings': findings.warnings,
    }


class _Findings:
    """{kind: {'count': n, 'examples': [...]}} with capped examples, split
    into errors (a page would show something wrong) and warnings (data worth
    a curation look, page still honest)."""

    def __init__(self):
        self.errors = {}
        self.warnings = {}

    def error(self, kind, example):
        self._add(self.errors, kind, example)

    def warn(self, kind, example):
        self._add(self.warnings, kind, example)

    @staticmethod
    def _add(bucket, kind, example):
        entry = bucket.setdefault(kind, {'count': 0, 'examples': []})
        entry['count'] += 1
        if len(entry['examples']) < EXAMPLE_LIMIT:
            entry['examples'].append(example)


def _check_annotation_integrity(annotation, findings):
    sequence = annotation.peptide_sequence
    protein = annotation.protein_sequence
    if not annotation.match_count:
        if protein:
            findings.warn('unmatched_annotation_keeps_protein_sequence', sequence)
        return
    if not protein:
        findings.warn('match_without_protein_sequence', sequence)
        return
    if not annotation.variant_match:
        if sequence not in protein:
            findings.error('exact_annotation_peptide_not_in_protein',
                           f'{sequence} -> {annotation.uniprot_id}')
        elif (annotation.match_start is not None
              and not protein.startswith(sequence, annotation.match_start)):
            findings.error('match_start_points_elsewhere',
                           f'{sequence} -> {annotation.uniprot_id}'
                           f' @{annotation.match_start}')
        return
    if annotation.match_count > 1 and not annotation.gene_name:
        # The acceptance gate requires one non-empty gene or a single
        # accession; a stored row like this predates the gate (the
        # LPLTFGGGTK class: many unrelated GN=-less proteins pooled into
        # an arbitrary parent) and needs a full re-annotation.
        findings.error('variant_annotation_violates_acceptance_gate',
                       f'{sequence} -> {annotation.uniprot_id}'
                       f' (x{annotation.match_count}, no gene)')
    if annotation.match_start is None:
        findings.error('variant_annotation_without_position', sequence)
        return
    region = protein[annotation.match_start:annotation.match_start + len(sequence)]
    mismatches = (len(sequence) if len(region) != len(sequence) else
                  sum(1 for a, b in zip(region, sequence) if a != b))
    if mismatches != 1:
        findings.error('variant_region_not_single_substitution',
                       f'{sequence} -> {annotation.uniprot_id}'
                       f' @{annotation.match_start} ({mismatches} mismatches)')


def _check_public_vs_derived(public, findings):
    derived, _, _ = build_public_rows()
    live = {p.peptide_sequence: p for p in public}
    for sequence in set(live) - set(derived):
        findings.error('published_but_no_longer_derivable', sequence)
    for sequence in set(derived) - set(live):
        findings.error('derivable_but_not_published', sequence)
    for sequence in set(live) & set(derived):
        stale = [field for field in PUBLIC_FIELDS
                 if getattr(live[sequence], field) != derived[sequence][field]]
        if stale:
            findings.error('published_fields_stale',
                           f'{sequence}: {", ".join(stale)}')


def _check_page_coherence(peptide, annotation, findings):
    """The detail page shows curated identity next to the FASTA-mapped
    protein; flag rows where those disagree so a visitor never reconciles
    them for us."""
    if (annotation is not None and annotation.protein_sequence
            and annotation.uniprot_id and peptide.uniprot_id
            and annotation.uniprot_id != peptide.uniprot_id):
        findings.warn('curated_accession_differs_from_mapped_protein',
                      f'{peptide.peptide_sequence}: curated'
                      f' {peptide.uniprot_id}, mapped {annotation.uniprot_id}')
    if annotation is None or not annotation.match_count:
        return
    if peptide.sequence_variant == 'wildtype' and annotation.variant_match:
        findings.warn('curated_wildtype_without_exact_proteome_match',
                      peptide.peptide_sequence)
    if peptide.sequence_variant == 'mutant' and not annotation.variant_match:
        findings.warn('curated_mutant_matches_wildtype_proteome_exactly',
                      peptide.peptide_sequence)
