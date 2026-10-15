import os

from django.core.management.base import BaseCommand, CommandError

from catalog.annotation import assignment_from_matches, match_peptides
from catalog.models import FastaAnnotation, SourcePeptideLot
from catalog.publish import STANDARD_SEQUENCE, publish_catalog


class Command(BaseCommand):
    help = ('Match all catalog peptide sequences against reference proteome '
            'FASTA(s) and store the annotations, then republish. Curated '
            'SysQuan values always win over these at publish time.')

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
        matches, protein_sequences = match_peptides(
            sequences, options['fasta'],
            on_progress=lambda n: self.stdout.write(f'  {n} proteins scanned'))

        fasta_label = ','.join(os.path.basename(p) for p in options['fasta'])
        existing = {a.peptide_sequence: a for a in FastaAnnotation.objects.all()}
        created = updated = 0
        for sequence in sequences:
            assignment = assignment_from_matches(matches.get(sequence, []))
            assignment['protein_sequence'] = protein_sequences.get(
                assignment['uniprot_id'], '')
            annotation = existing.get(sequence)
            if annotation is None:
                FastaAnnotation.objects.create(
                    peptide_sequence=sequence, fasta_label=fasta_label, **assignment)
                created += 1
            else:
                for field, value in assignment.items():
                    setattr(annotation, field, value)
                annotation.fasta_label = fasta_label
                annotation.save()
                updated += 1

        matched = sum(1 for s in sequences if matches.get(s))
        unique = sum(1 for s in sequences if len(matches.get(s, [])) == 1)
        self.stdout.write(
            f'annotations: {created} created, {updated} updated; '
            f'{matched}/{len(sequences)} matched, {unique} proteotypic')
        if not options['no_publish']:
            run, rejected = publish_catalog()
            self.stdout.write(f'published {run.rows_created} public peptides')
