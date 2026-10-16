#!/bin/bash
# Enable quote-request email on the production box by reusing the smtp2go
# credentials sysquan already sends with (systemd drop-in email.conf).
# No secret lives in this script or in git. Run as root:
#   sudo curl -fsSL https://raw.githubusercontent.com/PitKubi/peptiquantlibrary/main/deploy/enable_email.sh -o /tmp/pql_email.sh && sudo bash /tmp/pql_email.sh
set -e

ENV_FILE="/etc/peptiquantlibrary/env"
SYSQUAN_DROPIN="/etc/systemd/system/gunicorn.service.d/email.conf"

if [ "$(id -u)" != 0 ]; then echo "Run as root."; exit 1; fi

PW=$(grep -oP 'EMAIL_HOST_PASSWORD=\K[^"]+' "$SYSQUAN_DROPIN")
[ -n "$PW" ] || { echo "No smtp2go password in $SYSQUAN_DROPIN"; exit 1; }

# Idempotent: drop any earlier EMAIL block (and the 'unset' comment) first.
sed -i '/^# EMAIL_/d; /^EMAIL_/d; /^DEFAULT_FROM_EMAIL=/d' "$ENV_FILE"
cat >> "$ENV_FILE" <<EOF
EMAIL_BACKEND=django.core.mail.backends.smtp.EmailBackend
EMAIL_HOST=mail.smtp2go.com
EMAIL_PORT=587
EMAIL_HOST_USER=peptiquantonco
EMAIL_HOST_PASSWORD=$PW
EMAIL_USE_TLS=1
DEFAULT_FROM_EMAIL="PeptiQuant Library <peptiquantonco@mrmproteomics.com>"
EOF

systemctl restart peptiquantlibrary
echo "EMAIL CONFIGURED; verify with the api/admin/test-email/ endpoint."
