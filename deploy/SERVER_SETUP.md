# PeptiQuant Library — one-time server setup

Target: the SysQuan production host (Strato). Everything here happens once;
afterwards all operation goes through `POST /api/admin/deploy/` and
`POST /api/admin/sync/`, no shell needed.

The whole instance is `/home/peter/peptiquantlibrary` + `/etc/peptiquantlibrary/env`
+ one systemd unit + one nginx vhost. Moving servers = copy the directory
(db.sqlite3 included, or `export_catalog`/`import_catalog`), repeat steps 3-7,
repoint DNS.

## 1. DNS

A records for `peptiquantlibrary.com` and `www` → the server's IP
(same registrar flow as quantivum.com on Namecheap).

## 2. Read-only database role

As postgres superuser, run `create_readonly_role.sql` (edit the password first;
check the actual database name with `\l` — the GRANT CONNECT line must match).

## 3. Code + venv

```bash
cd /home/peter
git clone https://github.com/PitKubi/peptiquantlibrary.git
cd peptiquantlibrary
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt psycopg2-binary
```

For a private repo, put the credentialed remote in `/home/peter/peptiquantlibrary/.deploy.env`
(mode 0600, never committed):

```bash
GIT_REMOTE_URL="https://PitKubi:<token>@github.com/PitKubi/peptiquantlibrary.git"
```

## 4. Environment

`/etc/peptiquantlibrary/env` (root-owned, mode 0640, group peter or www-data):

```
PQL_SECRET_KEY=<50 random chars>
PQL_DEBUG=0
PQL_ALLOWED_HOSTS=peptiquantlibrary.com,www.peptiquantlibrary.com
PQL_CSRF_TRUSTED_ORIGINS=https://peptiquantlibrary.com,https://www.peptiquantlibrary.com
PQL_ADMIN_TOKEN=<long random token>
SYSQUAN_DB_DSN=postgresql://pql_reader:<password>@localhost:5432/<dbname>
PQL_QUOTE_NOTIFY_EMAILS=christoph.borchers@mcgill.ca,rpopp@mrmproteomics.com,mtrawkina@mrmproteomics.com
EMAIL_BACKEND=django.core.mail.backends.smtp.EmailBackend
EMAIL_HOST=mail.smtp2go.com
EMAIL_PORT=587
EMAIL_HOST_USER=<smtp2go user>
EMAIL_HOST_PASSWORD=<smtp2go password>
DEFAULT_FROM_EMAIL=peptiquantonco@mrmproteomics.com
PQL_LOG_DIR=/home/peter/peptiquantlibrary/logs
```

First migrate + sync by hand to prove the DSN works:

```bash
cd /home/peter/peptiquantlibrary
set -a; source /etc/peptiquantlibrary/env; set +a
.venv/bin/python manage.py migrate
.venv/bin/python manage.py sync_catalog --from-db
.venv/bin/python manage.py catalog_stats
.venv/bin/python manage.py collectstatic --noinput
```

## 5. systemd + sudoers

```bash
sudo cp deploy/peptiquantlibrary.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now peptiquantlibrary
```

Sudoers line so the self-deploy can restart ONLY this unit without a password
(`sudo visudo -f /etc/sudoers.d/peptiquantlibrary`):

```
peter ALL=(root) NOPASSWD: /usr/bin/systemctl restart peptiquantlibrary
```

## 6. nginx + TLS

```bash
sudo cp deploy/nginx_peptiquantlibrary.conf /etc/nginx/sites-available/peptiquantlibrary
sudo ln -s /etc/nginx/sites-available/peptiquantlibrary /etc/nginx/sites-enabled/
sudo nginx -t && sudo systemctl reload nginx
sudo certbot --nginx -d peptiquantlibrary.com -d www.peptiquantlibrary.com
```

## 7. Nightly sync

```bash
( crontab -l 2>/dev/null | grep -v pql-sync; \
  echo '15 4 * * * cd /home/peter/peptiquantlibrary && set -a && . /etc/peptiquantlibrary/env && set +a && .venv/bin/python manage.py sync_catalog --from-db >> logs/sync.log 2>&1  # pql-sync' \
) | crontab -
```

## Remote operation (after setup)

```bash
TOKEN=<PQL_ADMIN_TOKEN>
curl -s -H "X-Admin-Token: $TOKEN" https://peptiquantlibrary.com/api/admin/health/
curl -s -X POST -H "X-Admin-Token: $TOKEN" https://peptiquantlibrary.com/api/admin/deploy/
curl -s -X POST -H "X-Admin-Token: $TOKEN" https://peptiquantlibrary.com/api/admin/sync/
curl -s -H "X-Admin-Token: $TOKEN" "https://peptiquantlibrary.com/api/admin/logs/?file=deploy.log&tail=100"
curl -s -H "X-Admin-Token: $TOKEN" https://peptiquantlibrary.com/api/admin/quote-requests/
```

`health` returns `git_head` + `api_build`: after a deploy, confirm the head
advanced AND the build string changed (the sysquant lesson — a reported new
head does not yet mean the new code is serving).
