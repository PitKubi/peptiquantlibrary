#!/bin/bash
# Self-deploy, triggered by POST /api/admin/deploy/. Mirrors the sysquant
# deploy.sh lessons:
#   * set -e / pipefail so a failed migrate can never report success;
#   * fetch + reset --hard, never bare pull (stale token or drifted tree must
#     fail loudly, not no-op);
#   * the service restart is the LAST line, because this script runs inside the
#     gunicorn cgroup and everything after the restart is killed with it.
set -e
set -o pipefail

REPO_DIR="${PQL_REPO_DIR:-/home/peter/peptiquantlibrary}"
SERVICE="${PQL_SERVICE:-peptiquantlibrary}"
# .deploy.env is NOT in git; it defines GIT_REMOTE_URL (with credentials if the
# repo is private) and may override REPO_DIR/SERVICE.
if [ -f "$REPO_DIR/.deploy.env" ]; then
  source "$REPO_DIR/.deploy.env"
fi

cd "$REPO_DIR"

HEAD_BEFORE=$(git rev-parse --short HEAD)
git stash push -m "deploy-autostash-$(date +%Y%m%d-%H%M%S)" 2>&1 | tail -1 || true
git fetch "${GIT_REMOTE_URL:-origin}" main
git reset --hard FETCH_HEAD
HEAD_AFTER=$(git rev-parse --short HEAD)
echo "HEAD ${HEAD_BEFORE} -> ${HEAD_AFTER}"

.venv/bin/pip install -q -r requirements.txt
.venv/bin/python manage.py migrate --noinput
.venv/bin/python manage.py collectstatic --noinput | tail -1 || echo "collectstatic failed (non-fatal)"
.venv/bin/python manage.py catalog_stats

echo "Restarting ${SERVICE}..."
sudo -n systemctl restart "$SERVICE"
