"""Peptide-to-proteome matching: annotate catalog sequences by exact substring
search against reference FASTA(s) (UniProt format). Fills what Maria's curation
hasn't yet: accession, gene, protein name, species.

Assignment rules (proteomics convention, agreed with Peter 2026-10-15):
  * exactly one matching protein  -> full annotation (proteotypic);
  * several matches, one gene     -> gene/protein/species kept, accession only
                                     when single (isoforms must not block the
                                     gene column);
  * several genes                 -> ambiguous, nothing published; surfaces in
                                     the curation report instead.
"""
import re
from collections import defaultdict

UNIPROT_ID_LINE = re.compile(r'^>(?:sp|tr)\|(?P<accession>[^|]+)\|\S+\s*(?P<rest>.*)$')
SPECIES_FIELD = re.compile(r'\bOS=(.+?)(?=\s+[A-Z]{2}=|$)')
GENE_FIELD = re.compile(r'\bGN=(\S+)')

PREFIX_LENGTH = 5


def parse_fasta(path):
    """Yield (accession, gene, protein_name, species, sequence) per entry."""
    header = None
    chunks = []
    with open(path) as f:
        for line in f:
            line = line.rstrip('\n')
            if line.startswith('>'):
                if header is not None:
                    yield (*header, ''.join(chunks))
                header = _parse_header(line)
                chunks = []
            else:
                chunks.append(line.strip())
    if header is not None:
        yield (*header, ''.join(chunks))


def _parse_header(line):
    match = UNIPROT_ID_LINE.match(line)
    if not match:
        return (line[1:].split()[0] if line[1:].split() else '', '', '', '')
    rest = match['rest']
    protein = re.split(r'\s+[A-Z]{2}=', rest)[0].strip()
    species = SPECIES_FIELD.search(rest)
    gene = GENE_FIELD.search(rest)
    return (match['accession'],
            gene.group(1) if gene else '',
            protein,
            _trim_species(species.group(1) if species else ''))


def _trim_species(species):
    # UniProt OS= reads e.g. "Homo sapiens (Human)"; the short common name in
    # parentheses is the public-friendly one.
    inner = re.search(r'\(([^)]+)\)\s*$', species)
    return inner.group(1) if inner else species


def match_peptides(peptide_sequences, fasta_paths, on_progress=None):
    """Return ({sequence: [(accession, gene, protein, species), ...]},
    {accession: protein_sequence}) for every input sequence found in the
    FASTA(s). Protein sequences are kept only for matched accessions, for the
    peptide-in-protein view. One linear scan per protein with a prefix index
    over the peptides, so runtime is proteome-bound, not peptides x proteins."""
    by_prefix = defaultdict(list)
    short_peptides = []
    for sequence in peptide_sequences:
        if len(sequence) >= PREFIX_LENGTH:
            by_prefix[sequence[:PREFIX_LENGTH]].append(sequence)
        else:
            short_peptides.append(sequence)

    matches = defaultdict(list)
    matched_protein_sequences = {}
    first_positions = {}
    proteins_scanned = 0
    for path in fasta_paths:
        for accession, gene, protein_name, species, protein_seq in parse_fasta(path):
            entry = (accession, gene, protein_name, species)
            hit = False
            for i in range(len(protein_seq) - PREFIX_LENGTH + 1):
                for peptide in by_prefix.get(protein_seq[i:i + PREFIX_LENGTH], ()):
                    if (protein_seq.startswith(peptide, i)
                            and entry not in matches[peptide]):
                        matches[peptide].append(entry)
                        first_positions[(peptide, accession)] = i
                        hit = True
            for peptide in short_peptides:
                found = protein_seq.find(peptide)
                if found >= 0 and entry not in matches[peptide]:
                    matches[peptide].append(entry)
                    first_positions[(peptide, accession)] = found
                    hit = True
            if hit:
                matched_protein_sequences[accession] = protein_seq
            proteins_scanned += 1
            if on_progress and proteins_scanned % 5000 == 0:
                on_progress(proteins_scanned)
    return matches, matched_protein_sequences, first_positions


AMINO_ACIDS = 'ACDEFGHIKLMNPQRSTVWY'


def single_substitution_variants(peptide_sequences):
    """{variant_sequence: [original, ...]} for every single amino-acid
    substitution of the given peptides. Used to map mutant/variant peptides
    (absent from wild-type proteomes by construction) back to their parent
    protein. A variant that equals another catalog peptide is kept on purpose:
    a mutant restoring to its wild-type sibling (KRAS G12C -> G12_WT) is
    exactly how the parent protein is found, and the result is always labeled
    variant_match."""
    variants = defaultdict(list)
    for sequence in peptide_sequences:
        for i, original_aa in enumerate(sequence):
            for aa in AMINO_ACIDS:
                if aa == original_aa:
                    continue
                variants[sequence[:i] + aa + sequence[i + 1:]].append(sequence)
    return variants


def assignment_from_matches(entries):
    """Reduce a sequence's match list to the publishable annotation dict."""
    if not entries:
        return {'uniprot_id': '', 'gene_name': '', 'protein_name': '',
                'species': '', 'match_count': 0}
    species_set = {species for _, _, _, species in entries if species}
    first = entries[0]
    # A conserved peptide can occur in several species (Peter: "some species
    # can be both"); all of them are reported, joined with ' + '. Accession,
    # gene and protein are ALWAYS filled from the first match as the
    # representative (Peter: every peptide needs its annotation visible);
    # match_count carries the shared-ness, which the detail page discloses as
    # "occurs in N proteins".
    return {
        'uniprot_id': first[0],
        'gene_name': first[1],
        'protein_name': first[2],
        'species': ' + '.join(sorted(species_set)),
        'match_count': len(entries),
    }
