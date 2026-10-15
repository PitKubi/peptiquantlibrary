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


class CatalogPeptide(models.Model):
    """One publicly listed peptide product. Rows are written only by
    sync_catalog / import_catalog, never by request handlers, so the public
    site stays read-only with respect to the catalog."""
    source = models.CharField(max_length=30, choices=SOURCE_CHOICES, db_index=True)
    source_key = models.CharField(
        max_length=120,
        help_text='Identity of the row in its source (MRMP ID, or sequence|label for spreadsheet rows)')

    mrmp_id = models.CharField(max_length=50, blank=True, db_index=True)
    peptide_sequence = models.CharField(max_length=255, db_index=True)
    label_type = models.CharField(max_length=10, choices=LABEL_TYPE_CHOICES, db_index=True)
    label_tag = models.CharField(max_length=50, blank=True,
                                 help_text='Heavy-label tag, e.g. K+8, R+10; "-" for NAT')
    native_mutant = models.CharField(max_length=20, blank=True)
    species = models.CharField(max_length=120, blank=True, db_index=True)
    phospho_present = models.BooleanField(default=False)

    uniprot_id = models.CharField(max_length=50, blank=True, db_index=True)
    gene_name = models.CharField(max_length=100, blank=True, db_index=True)
    protein_name = models.CharField(max_length=255, blank=True)

    mass_confirmed = models.BooleanField(default=False)
    purity_confirmed = models.BooleanField(default=False)
    cze_purity = models.FloatField(null=True, blank=True)
    aaa_ratio = models.FloatField(null=True, blank=True)

    # Derived at sync time from workflow status + freezer location. The
    # location itself deliberately never leaves the SysQuan database.
    in_stock = models.BooleanField(default=False, db_index=True)
    workflow_status = models.CharField(max_length=30, blank=True)

    first_seen_at = models.DateTimeField(auto_now_add=True)
    synced_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['gene_name', 'peptide_sequence', 'label_type']
        constraints = [
            models.UniqueConstraint(fields=['source', 'source_key'],
                                    name='unique_source_row'),
        ]
        indexes = [
            models.Index(fields=['peptide_sequence', 'label_type']),
        ]

    def __str__(self):
        return f'{self.peptide_sequence} [{self.label_type}] {self.gene_name}'


class SyncRun(models.Model):
    """History of catalog syncs, surfaced by api/admin/health so a remote
    operator can tell whether the catalog is current without shell access."""
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
    """Snapshot of the requested peptide. Sequence and label are copied so the
    request stays legible even if the catalog row is later resynced or removed."""
    request = models.ForeignKey(QuoteRequest, on_delete=models.CASCADE, related_name='items')
    peptide = models.ForeignKey(CatalogPeptide, on_delete=models.SET_NULL, null=True)
    peptide_sequence = models.CharField(max_length=255)
    label_type = models.CharField(max_length=10)
    label_tag = models.CharField(max_length=50, blank=True)
    uniprot_id = models.CharField(max_length=50, blank=True)
    gene_name = models.CharField(max_length=100, blank=True)

    def __str__(self):
        return f'{self.peptide_sequence} [{self.label_type}]'
