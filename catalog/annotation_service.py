"""Orchestrates FASTA annotation for a set of catalog sequences: exact pass,
single-substitution variant pass for the rest, FastaAnnotation upserts.
Used by the annotate_from_fasta command (all sequences) and by sync_catalog
(only sequences that have never been annotated), so a nightly sync keeps new
peptides annotated without a separate step."""
import os

from .annotation import (assignment_from_matches, match_peptides,
                         single_substitution_variants)
from .models import FastaAnnotation


def annotate_sequences(sequences, fasta_paths, log=lambda line: None):
    """Match the given sequences against the FASTA(s) and upsert their
    FastaAnnotation rows. Returns a one-line summary string."""
    sequences = set(sequences)
    if not sequences:
        return 'nothing to annotate'
    log(f'Matching {len(sequences)} sequences against '
        f'{len(fasta_paths)} FASTA file(s)...')
    matches, proteins, positions = match_peptides(sequences, fasta_paths)

    unmatched = {s for s in sequences if not matches.get(s)}
    variant_entries, variant_positions = _variant_pass(
        unmatched, fasta_paths, proteins, log)

    fasta_label = ','.join(os.path.basename(p) for p in fasta_paths)
    existing = {
        a.peptide_sequence: a
        for a in FastaAnnotation.objects.filter(peptide_sequence__in=sequences)
    }
    for sequence in sequences:
        if matches.get(sequence):
            assignment = assignment_from_matches(matches[sequence])
            assignment['variant_match'] = False
            assignment['match_start'] = positions.get(
                (sequence, assignment['uniprot_id']))
        else:
            assignment = assignment_from_matches(
                variant_entries.get(sequence, []))
            assignment['variant_match'] = bool(assignment['match_count'])
            assignment['match_start'] = variant_positions.get(
                (sequence, assignment['uniprot_id']))
        assignment['protein_sequence'] = proteins.get(
            assignment['uniprot_id'], '')
        annotation = existing.get(sequence)
        if annotation is None:
            FastaAnnotation.objects.create(
                peptide_sequence=sequence, fasta_label=fasta_label, **assignment)
        else:
            for field, value in assignment.items():
                setattr(annotation, field, value)
            annotation.fasta_label = fasta_label
            annotation.save()

    exact = sum(1 for s in sequences if matches.get(s))
    via_variant = sum(1 for s in unmatched if variant_entries.get(s))
    return (f'{exact}/{len(sequences)} exact, {via_variant} via single '
            f'substitution, {len(sequences) - exact - via_variant} unmatched')


def _variant_pass(unmatched, fasta_paths, proteins, log):
    if not unmatched:
        return {}, {}
    variants = single_substitution_variants(unmatched)
    log(f'Variant pass: {len(unmatched)} unmatched peptides '
        f'-> {len(variants)} single-substitution probes...')
    variant_matches, variant_proteins, variant_positions = match_peptides(
        set(variants), fasta_paths)
    proteins.update(variant_proteins)
    entries_by_original = {}
    positions_by_original = {}
    for variant, entries in variant_matches.items():
        for original in variants[variant]:
            known = entries_by_original.setdefault(original, [])
            for entry in entries:
                if entry not in known:
                    known.append(entry)
                positions_by_original.setdefault(
                    (original, entry[0]),
                    variant_positions.get((variant, entry[0])))
    return entries_by_original, positions_by_original
