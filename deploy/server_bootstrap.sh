#!/bin/bash
# One-shot production setup on the SysQuan server. Run ONCE as root:
#   bash <(curl -fsSL https://raw.githubusercontent.com/PitKubi/peptiquantlibrary/main/deploy/server_bootstrap.sh)
# Idempotent: safe to re-run after a failed step. Interactive prompts:
# sysquan DB name, postgres password for the new read-only role, optional SMTP.
set -e

REPO_URL="https://github.com/PitKubi/peptiquantlibrary.git"
APP_USER="peter"
APP_DIR="/home/${APP_USER}/peptiquantlibrary"
ENV_DIR="/etc/peptiquantlibrary"
DOMAIN="peptiquantlibrary.com"

if [ "$(id -u)" != 0 ]; then echo "Run as root."; exit 1; fi

echo "== 1/8 packages"
apt-get install -y -q git python3-venv nginx certbot python3-certbot-nginx >/dev/null

echo "== 2/8 code + venv (as ${APP_USER})"
if [ ! -d "$APP_DIR/.git" ]; then
  sudo -u "$APP_USER" git clone "$REPO_URL" "$APP_DIR"
fi
cd "$APP_DIR"
sudo -u "$APP_USER" bash -c "cd $APP_DIR && python3 -m venv .venv \
  && .venv/bin/pip install -q -r requirements.txt psycopg2-binary"

echo "== 3/8 read-only database role"
read -r -p "SysQuan database name [sysquant]: " DB_NAME
DB_NAME=${DB_NAME:-sysquant}
PQL_DB_PASS=$(openssl rand -hex 16)
sudo -u postgres psql -v ON_ERROR_STOP=0 <<SQL
CREATE ROLE pql_reader WITH LOGIN PASSWORD '${PQL_DB_PASS}';
ALTER ROLE pql_reader WITH LOGIN PASSWORD '${PQL_DB_PASS}';
GRANT CONNECT ON DATABASE ${DB_NAME} TO pql_reader;
SQL
sudo -u postgres psql -d "$DB_NAME" -v ON_ERROR_STOP=1 <<'SQL'
GRANT USAGE ON SCHEMA public TO pql_reader;
GRANT SELECT ON sysq_inhouse_peptideworkflowentry TO pql_reader;
GRANT SELECT ON sysq_inhouse_syntheticpeptideinventory TO pql_reader;
SQL

echo "== 4/8 environment file"
mkdir -p "$ENV_DIR"
if [ ! -f "$ENV_DIR/env" ]; then
  SECRET=$(openssl rand -hex 32)
  TOKEN=$(openssl rand -hex 24)
  read -r -p "SMTP host (empty = email disabled for now): " SMTP_HOST
  if [ -n "$SMTP_HOST" ]; then
    read -r -p "SMTP user: " SMTP_USER
    read -r -s -p "SMTP password: " SMTP_PASS; echo
    read -r -p "From address: " FROM_ADDR
    EMAIL_BLOCK="EMAIL_BACKEND=django.core.mail.backends.smtp.EmailBackend
EMAIL_HOST=${SMTP_HOST}
EMAIL_PORT=587
EMAIL_HOST_USER=${SMTP_USER}
EMAIL_HOST_PASSWORD=${SMTP_PASS}
DEFAULT_FROM_EMAIL=${FROM_ADDR}"
  else
    EMAIL_BLOCK="# EMAIL_* unset: quote requests are stored and visible via the admin API"
  fi
  cat > "$ENV_DIR/env" <<ENV
PQL_SECRET_KEY=${SECRET}
PQL_DEBUG=0
PQL_ALLOWED_HOSTS=${DOMAIN},www.${DOMAIN}
PQL_CSRF_TRUSTED_ORIGINS=https://${DOMAIN},https://www.${DOMAIN}
PQL_ADMIN_TOKEN=${TOKEN}
SYSQUAN_DB_DSN=postgresql://pql_reader:${PQL_DB_PASS}@localhost:5432/${DB_NAME}
PQL_FASTA_PATHS=${APP_DIR}/fasta/uniprot_human_reviewed.fasta,${APP_DIR}/fasta/uniprot_mouse_reviewed.fasta
PQL_QUOTE_NOTIFY_EMAILS=christoph.borchers@mcgill.ca,rpopp@mrmproteomics.com,mtrawkina@mrmproteomics.com
PQL_LOG_DIR=${APP_DIR}/logs
${EMAIL_BLOCK}
ENV
  chmod 640 "$ENV_DIR/env"; chgrp "$APP_USER" "$ENV_DIR/env"
fi

echo "== 5/8 reference proteomes"
sudo -u "$APP_USER" mkdir -p "$APP_DIR/fasta" "$APP_DIR/logs"
for ORG in "9606 uniprot_human_reviewed" "10090 uniprot_mouse_reviewed"; do
  set -- $ORG
  F="$APP_DIR/fasta/$2.fasta"
  [ -s "$F" ] || sudo -u "$APP_USER" curl -s --http1.1 --retry 3 -o "$F" \
    "https://rest.uniprot.org/uniprotkb/stream?format=fasta&query=reviewed:true+AND+organism_id:$1"
  echo "  $2: $(grep -c '^>' "$F") entries"
done

echo "== 6/8 migrate + first sync (reads Maria's tables, annotates, publishes)"
sudo -u "$APP_USER" bash -c "cd $APP_DIR && set -a && . $ENV_DIR/env && set +a \
  && .venv/bin/python manage.py migrate --noinput \
  && .venv/bin/python manage.py sync_catalog --from-db \
  && .venv/bin/python manage.py collectstatic --noinput >/dev/null \
  && .venv/bin/python manage.py catalog_stats"

echo "== 7/8 services"
cp "$APP_DIR/deploy/peptiquantlibrary.service" /etc/systemd/system/
systemctl daemon-reload
systemctl enable --now peptiquantlibrary
echo "${APP_USER} ALL=(root) NOPASSWD: /usr/bin/systemctl restart peptiquantlibrary" \
  > /etc/sudoers.d/peptiquantlibrary
chmod 440 /etc/sudoers.d/peptiquantlibrary
cp "$APP_DIR/deploy/nginx_peptiquantlibrary.conf" /etc/nginx/sites-available/peptiquantlibrary
ln -sf /etc/nginx/sites-available/peptiquantlibrary /etc/nginx/sites-enabled/
nginx -t && systemctl reload nginx
( sudo -u "$APP_USER" crontab -l 2>/dev/null | grep -v pql-sync; \
  echo "15 4 * * * cd $APP_DIR && set -a && . $ENV_DIR/env && set +a && .venv/bin/python manage.py sync_catalog --from-db >> logs/sync.log 2>&1  # pql-sync" \
) | sudo -u "$APP_USER" crontab -

echo "== 8/8 TLS (needs the DNS A record already pointing here)"
if getent hosts "$DOMAIN" | grep -q "$(curl -s -4 ifconfig.me)"; then
  certbot --nginx -d "$DOMAIN" -d "www.$DOMAIN" --non-interactive --agree-tos \
    -m peterkubiniok@googlemail.com --redirect || echo "certbot failed; re-run: certbot --nginx -d $DOMAIN -d www.$DOMAIN"
else
  echo "DNS does not point here yet; once it does, run:"
  echo "  certbot --nginx -d $DOMAIN -d www.$DOMAIN"
fi

echo
echo "DONE. Health check:"
grep PQL_ADMIN_TOKEN "$ENV_DIR/env"
echo "curl -s -H \"X-Admin-Token: <that token>\" http://127.0.0.1:8100/api/admin/health/"
echo "Give the token to Claude so it can deploy/sync/monitor remotely."
