from django.db import models

LABEL_TYPE_CHOICES = [
    ('NAT', 'NAT (unlabeled)'),
    ('SIS', 'SIS (stable isotope standard)'),
]

SOURCE_CHOICES = [
    ('sysquan_workflow', 'SysQuan peptide workflow'),
    ('sysquan_inventory', 'SysQuan synthetic peptide inventory'),
    ('catalog_xlsx', 'Catalog spreadsheet import'),
]


class SourcePeptideLot(models.Model):
    """Raw synced rows, one per synthesis lot in the source system. Everything
    the public site shows is derived from these by publish_catalog; request
    handlers never write here, so the site stays read-only toward the catalog."""
    source = models.CharField(max_length=30, choices=SOURCE_CHOICES, db_index=True)
    source_key = models.CharField(
        max_length=120,
        help_text='Identity of the row in its source (MRMP ID, or sequence|label for spreadsheet rows)')

    mrmp_id = models.CharField(max_length=50, blank=True, db_index=True)
    peptide_sequence = models.CharField(max_length=255, db_index=True)
    label_type = models.CharField(max_length=10, choices=LABEL_TYPE_CHOICES)
    label_tag = models.CharField(max_length=50, blank=True,
                                 help_text='Heavy-label tag, e.g. K+8, R+10; "-" for NAT')
    native_mutant = models.CharField(max_length=20, blank=True)
    species_raw = models.CharField(max_length=120, blank=True)
    phospho_present = models.BooleanField(default=False)

    uniprot_id = models.CharField(max_length=50, blank=True)
    gene_name = models.CharField(max_length=100, blank=True)
    protein_name = models.CharField(max_length=255, blank=True)

    mass_confirmed = models.BooleanField(default=False)
    purity_confirmed = models.BooleanField(default=False)
    cze_purity = models.FloatField(null=True, blank=True)
    aaa_ratio = models.FloatField(null=True, blank=True)
    workflow_status = models.CharField(max_length=30, blank=True)
    project_category = models.CharField(max_length=100, blank=True)
    kit_name = models.CharField(
        max_length=100, blank=True,
        help_text='Commercial kit(s) this lot belongs to, e.g. BAK270; '
                  'comes from kit catalog spreadsheets, not the workflow tool')

    first_seen_at = models.DateTimeField(auto_now_add=True)
    synced_at = models.DateTimeField(auto_now=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=['source', 'source_key'],
                                    name='unique_source_row'),
        ]

    # Per Peter 2026-10-15: only peptides physically in inventory are listed;
    # mid-production and failed lots stay internal.
    # Confidentiality rule agreed with Maria (2026-10-15 call): a lot whose
    # project category contains "client" or "private" never reaches the
    # public catalog, whatever its inventory status. Maria flags such
    # projects in the sysquan tool; Peter/Christoph decide everything else.
    # Spreadsheet lots count only when they name a kit (2026-10-16): Maria's
    # tool is the sole master for everything else, and a spreadsheet's
    # "master inventory" copies carry no project category, so they silently
    # bypassed her control and re-published a client peptide (LPLTFGGGTK).
    CONFIDENTIAL_CATEGORY_WORDS = ('client', 'private')

    def is_catalog_eligible(self):
        category = self.project_category.lower()
        if any(word in category for word in self.CONFIDENTIAL_CATEGORY_WORDS):
            return False
        if self.source == 'catalog_xlsx':
            return bool(self.kit_name)
        return self.workflow_status == '9_inventory'

    def __str__(self):
        return f'{self.source_key} {self.peptide_sequence} [{self.label_type}]'


class FastaAnnotation(models.Model):
    """Result of matching a catalog peptide sequence against reference
    proteome FASTA(s). match_count 0 = scanned, found nowhere; 1 = proteotypic;
    >1 = shared. Only unique assignments are used publicly; gene/species are
    kept when unambiguous across all matches (isoform-safe)."""
    peptide_sequence = models.CharField(max_length=255, unique=True)
    uniprot_id = models.CharField(max_length=50, blank=True)
    gene_name = models.CharField(max_length=100, blank=True)
    protein_name = models.CharField(max_length=255, blank=True)
    species = models.CharField(max_length=120, blank=True)
    protein_sequence = models.TextField(
        blank=True,
        help_text='Sequence of the assigned protein, for the highlighted '
                  'peptide-in-protein view on the detail page')
    match_start = models.IntegerField(
        null=True, blank=True,
        help_text='0-based position of the (possibly variant) match in '
                  'protein_sequence')
    variant_match = models.BooleanField(
        default=False,
        help_text='Matched via a single amino-acid substitution (mutant/'
                  'variant peptide mapped to its parent protein)')
    match_count = models.IntegerField(default=0)
    fasta_label = models.CharField(max_length=255, blank=True)
    annotated_at = models.DateTimeField(auto_now=True)

    def __str__(self):
        return f'{self.peptide_sequence} -> {self.uniprot_id or "?"} (x{self.match_count})'


class PublicPeptide(models.Model):
    """The published catalog: exactly one row per peptide sequence, the shape
    Christoph asked for. Rebuilt wholesale by publish_catalog; never edited by
    hand or by request handlers."""
    peptide_sequence = models.CharField(max_length=255, unique=True)
    gene_name = models.CharField(max_length=100, blank=True, db_index=True)
    uniprot_id = models.CharField(max_length=50, blank=True, db_index=True)
    protein_name = models.CharField(max_length=255, blank=True)
    species = models.CharField(max_length=120, blank=True, db_index=True)

    sis_available = models.BooleanField(default=False)
    nat_available = models.BooleanField(default=False)
    sis_label_tags = models.CharField(max_length=100, blank=True)

    # Christoph 2026-10-16: "please state if the sequence represents wild type
    # or mutant"; Peter to Maria same day: with no curated mutation info, show
    # NA rather than guess. 'mutant_inferred' (Peter 2026-10-16) is the one
    # sanctioned inference: unambiguous single-substitution mapping AND the
    # wild-type counterpart sequence is itself in the catalog.
    SEQUENCE_VARIANT_CHOICES = [('wildtype', 'Wild type'),
                                ('mutant', 'Mutant'),
                                ('mutant_inferred', 'Mutant (inferred)'),
                                ('', 'NA')]
    sequence_variant = models.CharField(max_length=16, blank=True,
                                        choices=SEQUENCE_VARIANT_CHOICES)
    kit_names = models.CharField(
        max_length=200, blank=True,
        help_text='Distinct kit names across this sequence\'s lots')

    ANNOTATION_SOURCES = [('curated', 'Curated in SysQuan'),
                          ('fasta', 'FASTA-derived'),
                          ('curated+fasta', 'Curated, FASTA-completed'),
                          ('', 'Unannotated')]
    annotation_source = models.CharField(max_length=20, blank=True,
                                         choices=ANNOTATION_SOURCES)
    lot_count = models.IntegerField(default=0)
    published_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['gene_name', 'peptide_sequence']

    def __str__(self):
        forms = '+'.join(f for f, on in [('SIS', self.sis_available),
                                         ('NAT', self.nat_available)] if on)
        return f'{self.peptide_sequence} ({forms}) {self.gene_name}'


class SyncRun(models.Model):
    """History of catalog syncs/publishes, surfaced by api/admin/health so a
    remote operator can tell whether the catalog is current without shell access."""
    started_at = models.DateTimeField(auto_now_add=True)
    source = models.CharField(max_length=200)
    rows_seen = models.IntegerField(default=0)
    rows_created = models.IntegerField(default=0)
    rows_updated = models.IntegerField(default=0)
    rows_unchanged = models.IntegerField(default=0)
    rows_skipped = models.IntegerField(default=0)
    duration_seconds = models.FloatField(default=0)
    ok = models.BooleanField(default=False)
    error = models.TextField(blank=True)

    class Meta:
        ordering = ['-started_at']

    def __str__(self):
        state = 'ok' if self.ok else 'FAILED'
        return f'{self.started_at:%Y-%m-%d %H:%M} {self.source} {state}'


class QuoteRequest(models.Model):
    STATUS_CHOICES = [('new', 'New'), ('handled', 'Handled')]

    name = models.CharField(max_length=200)
    email = models.EmailField()
    organization = models.CharField(max_length=200, blank=True)
    message = models.TextField(blank=True)
    status = models.CharField(max_length=10, choices=STATUS_CHOICES, default='new')
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-created_at']

    def __str__(self):
        return f'{self.created_at:%Y-%m-%d} {self.email} ({self.items.count()} peptides)'


class QuoteRequestItem(models.Model):
    """Snapshot of the requested peptide and which forms (SIS/NAT) were asked
    for, copied so the request stays legible even after a catalog republish."""
    request = models.ForeignKey(QuoteRequest, on_delete=models.CASCADE, related_name='items')
    peptide = models.ForeignKey(PublicPeptide, on_delete=models.SET_NULL, null=True)
    peptide_sequence = models.CharField(max_length=255)
    want_sis = models.BooleanField(default=False)
    want_nat = models.BooleanField(default=False)
    uniprot_id = models.CharField(max_length=50, blank=True)
    gene_name = models.CharField(max_length=100, blank=True)

    def __str__(self):
        forms = '+'.join(f for f, on in [('SIS', self.want_sis),
                                         ('NAT', self.want_nat)] if on)
        return f'{self.peptide_sequence} ({forms})'
