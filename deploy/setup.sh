#!/usr/bin/env bash
# Bootstrap or update Medusa on a fresh Ubuntu 24.04 host. Idempotent.
#
# Usage (as root, from the copied repo):
#   sudo SERVER_NAME=1-2-3-4.sslip.io CERT_EMAIL=you@example.com bash deploy/setup.sh
#
# Expects:
#   - the repo at /opt/medusa (copied without .env files, node_modules or .venv)
#   - /etc/medusa/env with the secrets (see deploy/README.md); created empty if missing
# SERVER_NAME defaults to "_" (any host, HTTP only). With CERT_EMAIL set and a
# real hostname, a Let's Encrypt certificate is issued for HTTPS.
set -euo pipefail

APP=/opt/medusa
SERVER_NAME=${SERVER_NAME:-_}
CERT_EMAIL=${CERT_EMAIL:-}
export DEBIAN_FRONTEND=noninteractive

[ "$(id -u)" -eq 0 ] || { echo "run as root"; exit 1; }
[ -d "$APP/backend" ] || { echo "repo not found at $APP"; exit 1; }

echo "==> packages"
apt-get update -q
apt-get install -y -q ca-certificates curl gnupg nginx python3-venv python3-pip docker.io certbot python3-certbot-nginx
if ! command -v node >/dev/null || [ "$(node -v | cut -d. -f1 | tr -d v)" -lt 22 ]; then
  curl -fsSL https://deb.nodesource.com/setup_22.x | bash -
  apt-get install -y -q nodejs
fi
systemctl enable --now docker

echo "==> service user"
id medusa >/dev/null 2>&1 || useradd --system --home-dir /var/lib/medusa --create-home --shell /usr/sbin/nologin medusa
usermod -aG docker medusa
install -d -m 750 -o root -g medusa /etc/medusa
[ -f /etc/medusa/env ] || install -m 640 -o root -g medusa /dev/null /etc/medusa/env
grep -q '^TRUST_FORWARDED_FOR=' /etc/medusa/env || echo 'TRUST_FORWARDED_FOR=true' >> /etc/medusa/env
# Same default as the code: live Bob (it reports itself unavailable without BOB_API_KEY).
grep -q '^BOB_MODE=' /etc/medusa/env || echo 'BOB_MODE=live' >> /etc/medusa/env
if ! grep -q '^ALLOWED_ORIGIN=' /etc/medusa/env; then
  if [ "$SERVER_NAME" != "_" ]; then echo "ALLOWED_ORIGIN=https://$SERVER_NAME" >> /etc/medusa/env; fi
fi
chown -R medusa:medusa "$APP"

if grep -q '^BOB_MODE=live' /etc/medusa/env; then
  echo "==> bob shell"
  if ! command -v bob >/dev/null; then
    installer=$(mktemp)
    curl -fsSL https://bob.ibm.com/download/bobshell.sh -o "$installer"
    bash "$installer" --pm npm
    rm -f "$installer"
  fi
  bob --version | head -1
fi

# General-repo execution: install gVisor and build the runner image when the
# feature is on, or when EXEC_PREPARE=true (prepared and testable, still off).
if grep -qE '^(ARBITRARY_EXECUTION|EXEC_PREPARE)=true' /etc/medusa/env; then
  echo "==> gVisor + runner image"
  if ! command -v runsc >/dev/null; then
    curl -fsSL https://gvisor.dev/archive.key | gpg --dearmor --yes -o /usr/share/keyrings/gvisor-archive-keyring.gpg
    echo "deb [arch=$(dpkg --print-architecture) signed-by=/usr/share/keyrings/gvisor-archive-keyring.gpg] https://storage.googleapis.com/gvisor/releases release main" \
      > /etc/apt/sources.list.d/gvisor.list
    apt-get update -q
    apt-get install -y -q runsc
  fi
  if ! docker info --format '{{json .Runtimes}}' | grep -q '"runsc"'; then
    runsc install
    systemctl restart docker
  fi
  docker build -q -t medusa-pyrunner:latest "$APP/sandbox/pyrunner"
fi

echo "==> sandbox image"
docker build -q -t medusa-optilearn:latest "$APP/sandbox/optilearn"

echo "==> backend"
sudo -u medusa bash -c "cd $APP/backend && python3 -m venv .venv && .venv/bin/pip install -q --upgrade pip && .venv/bin/pip install -q -r requirements.txt"

echo "==> frontend"
# Same-origin API behind nginx: an empty base URL makes requests relative.
sudo -u medusa bash -c "cd $APP/frontend && npm ci --no-audit --no-fund && NEXT_PUBLIC_API_URL= NEXT_TELEMETRY_DISABLED=1 npm run build"

echo "==> systemd"
install -m 644 "$APP/deploy/medusa-backend.service" /etc/systemd/system/
install -m 644 "$APP/deploy/medusa-frontend.service" /etc/systemd/system/
systemctl daemon-reload
systemctl enable medusa-backend medusa-frontend
systemctl restart medusa-backend medusa-frontend

echo "==> nginx"
sed "s/SERVER_NAME/$SERVER_NAME/" "$APP/deploy/nginx.conf" > /etc/nginx/sites-available/medusa
ln -sf /etc/nginx/sites-available/medusa /etc/nginx/sites-enabled/medusa
rm -f /etc/nginx/sites-enabled/default
nginx -t
systemctl reload nginx

if [ "$SERVER_NAME" != "_" ] && [ -d "/etc/letsencrypt/live/$SERVER_NAME" ]; then
  echo "==> https (re-applying existing certificate)"
  certbot install --nginx -n --cert-name "$SERVER_NAME" --redirect
elif [ -n "$CERT_EMAIL" ] && [ "$SERVER_NAME" != "_" ]; then
  echo "==> https"
  certbot --nginx -n --agree-tos -m "$CERT_EMAIL" -d "$SERVER_NAME" --redirect
fi

echo "==> health"
for _ in $(seq 1 20); do
  curl -fsS http://127.0.0.1:8000/api/health && break
  sleep 1
done
echo
echo "Medusa is up. Sandbox image, backend and frontend are running."
