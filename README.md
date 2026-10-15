# PeptiQuant Library

Public catalog of the synthetic proteotypic peptides (SIS + NAT) synthesized by
MRM Proteomics / the Borchers lab, with quote requests. Origin: Christoph's
2026-10-08 request — a public database of all synthesized peptides, curated by
Maria in SysQuan, connected to the freezer inventory.

Standalone on purpose: own repo, own venv, own SQLite, own gunicorn unit, own
nginx vhost. It touches SysQuan only through `sync_catalog --from-db`, a
SELECT-only Postgres role over two tables. Moving hosts = copy this directory,
re-run `deploy/SERVER_SETUP.md`, repoint DNS.

## Data flow

SourcePeptideLot (raw synced lots) + FastaAnnotation (peptide -> proteome
matches) -> `publish_catalog` -> PublicPeptide: exactly one row per sequence
with SIS/NAT availability, the shape shown publicly. Eligibility = workflow
status "(9) In inventory" (or a curated spreadsheet source). Curated SysQuan
annotation always beats FASTA-derived values; only unambiguous FASTA matches
are published, the rest land in the curation report for Maria.

## Layout

- `catalog/` — models (SourcePeptideLot, FastaAnnotation, PublicPeptide,
  QuoteRequest, SyncRun), public views (search + bulk paste + session
  selection + quote request), token-protected admin API (`/api/admin/…`:
  health, deploy, sync, logs, quote-requests)
- `catalog/annotation.py` — FASTA parsing and peptide-to-proteome matching
- `catalog/publish.py` — grouping, species normalization, eligibility
- `catalog/sync/` — source readers (SysQuan Postgres, catalog xlsx) and upsert
- `catalog/management/commands/` — `sync_catalog`, `annotate_from_fasta`,
  `publish_catalog`, `catalog_stats`, `curation_report`, `export_catalog`,
  `import_catalog`
- `deploy/` — deploy.sh (self-deploy), systemd unit, nginx vhost, read-only
  role SQL, SERVER_SETUP.md (the full runbook)

## Local development

```bash
uv venv .venv && uv pip install -p .venv/bin/python -r requirements.txt
.venv/bin/python manage.py migrate
.venv/bin/python manage.py sync_catalog --from-xlsx <catalog.xlsx>   # or import_catalog <dump.json>
.venv/bin/python manage.py runserver
.venv/bin/python manage.py test catalog
```

Data on pk-main/laptop: the 2026-05 SIS catalog xlsx
(`project_workspaces/sysquant/data/peptide_db_maria/`, laptop) imports via
`sync_catalog --from-xlsx`.
