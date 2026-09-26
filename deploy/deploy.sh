#!/usr/bin/env bash
# Deploy one commit of main. Run as root on the server, normally by the GitHub
# Actions workflow through AWS SSM (no SSH, no GitHub access on the server):
#
#   bash /opt/medusa/deploy/deploy.sh <commit-sha> <archive-url>
#
# <archive-url> is a short-lived presigned S3 URL to `git archive` of that
# commit, uploaded by the workflow. The archive's embedded commit id must match
# <commit-sha>. The code is synced into /opt/medusa, keeping untracked build
# output and venvs (.venv, node_modules, .next), then deploy/setup.sh runs.
# Secrets stay in /etc/medusa/env; /etc/medusa/deploy.env sets SERVER_NAME.
set -euo pipefail

SHA=${1:?usage: deploy.sh <commit-sha> <archive-url>}
URL=${2:?usage: deploy.sh <commit-sha> <archive-url>}
APP=/opt/medusa

[ "$(id -u)" -eq 0 ] || { echo "run as root"; exit 1; }
[[ "$SHA" =~ ^[0-9a-f]{40}$ ]] || { echo "not a full commit sha: $SHA"; exit 1; }
[[ "$URL" == https://*.amazonaws.com/* ]] || { echo "unexpected archive url"; exit 1; }

# One deploy at a time.
exec 9>/var/lock/medusa-deploy.lock
flock -w 900 9 || { echo "another deploy is still running"; exit 1; }

# shellcheck source=/dev/null
source /etc/medusa/deploy.env

WORK=$(mktemp -d /tmp/medusa-release.XXXXXX)
trap 'rm -rf "$WORK"' EXIT

echo "==> downloading ${SHA:0:7}"
curl -fsS --retry 3 -o "$WORK/release.tar.gz" "$URL"
# get-tar-commit-id stops after the header, so gunzip may get SIGPIPE: ignore its status.
EMBEDDED=$( (gunzip -c "$WORK/release.tar.gz" 2>/dev/null || true) | git get-tar-commit-id)
[ "$EMBEDDED" = "$SHA" ] || { echo "archive is commit $EMBEDDED, expected $SHA"; exit 1; }

mkdir "$WORK/src"
tar -xzf "$WORK/release.tar.gz" -C "$WORK/src"
[ -f "$WORK/src/deploy/setup.sh" ] || { echo "archive does not look like Medusa"; exit 1; }

echo "==> syncing into $APP (was $(cat "$APP/.deployed-sha" 2>/dev/null | cut -c1-7 || echo unknown))"
rsync -a --delete \
  --exclude '.venv/' --exclude 'node_modules/' --exclude '.next/' --exclude '.deployed-sha' \
  "$WORK/src/" "$APP/"
echo "$SHA" > "$APP/.deployed-sha"

echo "==> setup"
SERVER_NAME="$SERVER_NAME" CERT_EMAIL="${CERT_EMAIL:-}" bash "$APP/deploy/setup.sh"

echo "==> deployed ${SHA:0:7}"
