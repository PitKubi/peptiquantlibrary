import os

from django.core.management.base import BaseCommand, CommandError

from catalog.annotation import (assignment_from_matches, match_peptides,
                                single_substitution_variants)
from catalog.models import FastaAnnotation, SourcePeptideLot
from catalog.publish import STANDARD_SEQUENCE, publish_catalog


class Command(BaseCommand):
    help = ('Match all catalog peptide sequences against reference proteome '
            'FASTA(s) and store the annotations, then republish. Peptides with '
            'no exact hit get a second, single-substitution pass so mutant/'
            'variant peptides map to their parent protein. Curated SysQuan '
            'values always win over these at publish time.')

    def add_arguments(self, parser):
        parser.add_argument('fasta', nargs='+', help='UniProt-format FASTA path(s)')
        parser.add_argument('--no-publish', action='store_true')

    def handle(self, *args, **options):
        for path in options['fasta']:
            if not os.path.exists(path):
                raise CommandError(f'{path} not found')

        sequences = {
            s.strip().upper()
            for s in SourcePeptideLot.objects.values_list(
                'peptide_sequence', flat=True).distinct()
            if STANDARD_SEQUENCE.match(s.strip().upper())
        }
        self.stdout.write(f'Matching {len(sequences)} sequences against '
                          f'{len(options["fasta"])} FASTA file(s)...')
        matches, proteins, positions = match_peptides(
            sequences, options['fasta'],
            on_progress=lambda n: self.stdout.write(f'  {n} proteins scanned'))

        unmatched = {s for s in sequences if not matches.get(s)}
        variant_entries, variant_positions = self._variant_pass(
            unmatched, options['fasta'], proteins)

        fasta_label = ','.join(os.path.basename(p) for p in options['fasta'])
        existing = {a.peptide_sequence: a for a in FastaAnnotation.objects.all()}
        created = updated = 0
        for sequence in sequences:
            if matches.get(sequence):
                assignment = assignment_from_matches(matches[sequence])
                assignment['variant_match'] = False
                position_key = (sequence, assignment['uniprot_id'])
                assignment['match_start'] = positions.get(position_key)
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
                    peptide_sequence=sequence, fasta_label=fasta_label,
                    **assignment)
                created += 1
            else:
                for field, value in assignment.items():
                    setattr(annotation, field, value)
                annotation.fasta_label = fasta_label
                annotation.save()
                updated += 1

        exact = sum(1 for s in sequences if matches.get(s))
        via_variant = sum(1 for s in unmatched if variant_entries.get(s))
        self.stdout.write(
            f'annotations: {created} created, {updated} updated; '
            f'{exact}/{len(sequences)} exact, {via_variant} via single '
            f'substitution, {len(sequences) - exact - via_variant} unmatched')
        if not options['no_publish']:
            run, rejected = publish_catalog()
            self.stdout.write(f'published {run.rows_created} public peptides')

    def _variant_pass(self, unmatched, fasta_paths, proteins):
        """Second scan with every single-substitution variant of the unmatched
        peptides; hits are mapped back to the original sequence."""
        if not unmatched:
            return {}, {}
        variants = single_substitution_variants(unmatched)
        self.stdout.write(f'Variant pass: {len(unmatched)} unmatched peptides '
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
                    position = variant_positions.get((variant, entry[0]))
                    positions_by_original.setdefault(
                        (original, entry[0]), position)
        return entries_by_original, positions_by_original
