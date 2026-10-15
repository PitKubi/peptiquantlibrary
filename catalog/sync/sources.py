"""Readers that turn each catalog source into normalized row dicts matching
SourcePeptideLot field names. Keeping the SQL here (plain psycopg2, no Django
models for the foreign schema) is what keeps this project fully standalone
from the sysquant codebase: moving servers means changing one DSN."""


def normalize_label_tag(label_tag, label_type):
    tag = (label_tag or '').strip()
    if tag:
        return tag
    if (label_type or '').strip().upper() == 'NAT':
        return '-'
    return ''


def normalize_label_type(label_type):
    value = (label_type or '').strip().upper()
    return value if value in ('NAT', 'SIS') else 'NAT'


WORKFLOW_SQL = """
    SELECT mrmp_id, peptide_sequence, label_type, label, native_mutant,
           species, phospho_present, uniprot_id, gene_name,
           mass_confirmed_post_synth, mass_confirmed_post_hplc,
           purity_confirmed, cze_purity, aaa_ratio, workflow_status
    FROM sysq_inhouse_peptideworkflowentry
    WHERE workflow_status != 'failed'
"""

# Legacy inventory rows that never went through the workflow app
# (source='csv_upload'); workflow-promoted rows are already covered above.
INVENTORY_SQL = """
    SELECT mrmp_id, peptide_sequence, label_type, phospho_present,
           uniprot_id, gene_name,
           post_synthesis_confirmed, post_hplc_confirmed,
           cze_purity, aaa_ratio
    FROM sysq_inhouse_syntheticpeptideinventory
    WHERE source = 'csv_upload'
      AND mrmp_id NOT IN (SELECT mrmp_id FROM sysq_inhouse_peptideworkflowentry)
"""


def rows_from_sysquan_db(dsn):
    import psycopg2
    import psycopg2.extras

    with psycopg2.connect(dsn) as connection:
        with connection.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cursor:
            cursor.execute(WORKFLOW_SQL)
            for r in cursor.fetchall():
                yield {
                    'source': 'sysquan_workflow',
                    'source_key': r['mrmp_id'],
                    'mrmp_id': r['mrmp_id'],
                    'peptide_sequence': (r['peptide_sequence'] or '').strip(),
                    'label_type': normalize_label_type(r['label_type']),
                    'label_tag': normalize_label_tag(r['label'], r['label_type']),
                    'native_mutant': (r['native_mutant'] or '').strip(),
                    'species_raw': (r['species'] or '').strip(),
                    'phospho_present': bool(r['phospho_present']),
                    'uniprot_id': (r['uniprot_id'] or '').strip(),
                    'gene_name': (r['gene_name'] or '').strip(),
                    'protein_name': '',
                    'mass_confirmed': bool(r['mass_confirmed_post_synth']
                                           or r['mass_confirmed_post_hplc']),
                    'purity_confirmed': bool(r['purity_confirmed']),
                    'cze_purity': r['cze_purity'],
                    'aaa_ratio': r['aaa_ratio'],
                    'workflow_status': r['workflow_status'] or '',
                }
            cursor.execute(INVENTORY_SQL)
            for r in cursor.fetchall():
                yield {
                    'source': 'sysquan_inventory',
                    'source_key': r['mrmp_id'],
                    'mrmp_id': r['mrmp_id'],
                    'peptide_sequence': (r['peptide_sequence'] or '').strip(),
                    'label_type': normalize_label_type(r['label_type']),
                    'label_tag': normalize_label_tag('', r['label_type']),
                    'native_mutant': '',
                    'species_raw': '',
                    'phospho_present': bool(r['phospho_present']),
                    'uniprot_id': (r['uniprot_id'] or '').strip(),
                    'gene_name': (r['gene_name'] or '').strip(),
                    'protein_name': '',
                    'mass_confirmed': bool(r['post_synthesis_confirmed']
                                           or r['post_hplc_confirmed']),
                    'purity_confirmed': r['cze_purity'] is not None,
                    'cze_purity': r['cze_purity'],
                    'aaa_ratio': r['aaa_ratio'],
                    # Legacy inventory rows ARE the curated inventory; they
                    # carry no workflow status, so eligibility is granted here.
                    'workflow_status': '9_inventory',
                }


# Spreadsheet import for catalogs that exist outside the SysQuan database,
# such as the 2026-05 SIS catalog with the BAK270 kit peptides. That format is
# one SIS row per peptide plus a "NAT available" yes/blank column, so a yes
# also emits a NAT lot. Header names are matched case-insensitively.
XLSX_COLUMN_ALIASES = {
    'peptide_sequence': ('peptide sequence', 'sequence', 'peptide'),
    'label_type': ('label type', 'sis/nat', 'type'),
    'label_tag': ('label', 'label tag', 'heavy label'),
    'uniprot_id': ('uniprot', 'uniprot id', 'uniprot accession', 'accession',
                   'measure identifier'),
    'gene_name': ('gene', 'gene name', 'gene symbol'),
    'protein_name': ('protein', 'protein name'),
    'species': ('species', 'organism'),
    'mrmp_id': ('mrmp id', 'mrmp_id', 'peptide id'),
    'nat_available': ('nat available', 'nat'),
}


def _header_map(header_cells):
    lowered = {str(c).strip().lower(): i for i, c in enumerate(header_cells) if c}
    mapping = {}
    for field, aliases in XLSX_COLUMN_ALIASES.items():
        for alias in aliases:
            if alias in lowered:
                mapping[field] = lowered[alias]
                break
    return mapping


def rows_from_xlsx(path, default_label_type='SIS'):
    import openpyxl

    workbook = openpyxl.load_workbook(path, read_only=True, data_only=True)
    sheet = workbook.active
    rows = sheet.iter_rows(values_only=True)
    mapping = _header_map(next(rows))
    if 'peptide_sequence' not in mapping:
        raise ValueError(
            f'{path}: no peptide-sequence column found; headers must include one of '
            f'{XLSX_COLUMN_ALIASES["peptide_sequence"]}')

    def cell(row, field):
        index = mapping.get(field)
        if index is None or index >= len(row) or row[index] is None:
            return ''
        return str(row[index]).strip()

    for row in rows:
        sequence = cell(row, 'peptide_sequence')
        if not sequence:
            continue
        label_type = normalize_label_type(cell(row, 'label_type') or default_label_type)
        label_tag = normalize_label_tag(cell(row, 'label_tag'), label_type)
        base = {
            'source': 'catalog_xlsx',
            'mrmp_id': cell(row, 'mrmp_id'),
            'peptide_sequence': sequence,
            'native_mutant': '',
            'species_raw': cell(row, 'species'),
            'phospho_present': False,
            'uniprot_id': cell(row, 'uniprot_id'),
            'gene_name': cell(row, 'gene_name'),
            'protein_name': cell(row, 'protein_name'),
            'mass_confirmed': False,
            'purity_confirmed': False,
            'cze_purity': None,
            'aaa_ratio': None,
            'workflow_status': '',
        }
        yield {**base, 'source_key': f'{sequence}|{label_type}|{label_tag}',
               'label_type': label_type, 'label_tag': label_tag}
        if (label_type == 'SIS'
                and cell(row, 'nat_available').lower() in ('yes', 'y', 'true', '1')):
            yield {**base, 'source_key': f'{sequence}|NAT|-',
                   'label_type': 'NAT', 'label_tag': '-'}
