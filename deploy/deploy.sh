#!/usr/bin/env bash
# Deploy one commit of main. Run as root on the server, normally by the GitHub
# Actions workflow through AWS SSM (no SSH):
#
#   bash /opt/medusa/deploy/deploy.sh <commit-sha>
#
# Expects (one-time setup, see deploy/README.md):
#   - /opt/medusa is a git checkout of the repo
#   - /root/.ssh/medusa_repo_key is a read-only deploy key for the repo
#   - /etc/medusa/deploy.env sets SERVER_NAME (and optionally CERT_EMAIL)
# Untracked build output and venvs (.venv, node_modules, .next) are kept, so
# setup.sh only rebuilds what changed. Secrets stay in /etc/medusa/env.
set -euo pipefail

SHA=${1:?usage: deploy.sh <commit-sha>}
APP=/opt/medusa

[ "$(id -u)" -eq 0 ] || { echo "run as root"; exit 1; }
[[ "$SHA" =~ ^[0-9a-f]{7,40}$ ]] || { echo "not a commit sha: $SHA"; exit 1; }

# One deploy at a time.
exec 9>/var/lock/medusa-deploy.lock
flock -w 900 9 || { echo "another deploy is still running"; exit 1; }

# shellcheck source=/dev/null
source /etc/medusa/deploy.env
export GIT_SSH_COMMAND="ssh -i /root/.ssh/medusa_repo_key -o IdentitiesOnly=yes -o StrictHostKeyChecking=accept-new"
git config --global --get-all safe.directory | grep -qx "$APP" || git config --global --add safe.directory "$APP"

cd "$APP"
echo "==> fetching main"
git fetch --quiet origin main
if ! git merge-base --is-ancestor "$SHA" origin/main; then
  echo "refusing to deploy $SHA: it is not on origin/main"
  exit 1
fi
echo "==> checking out $SHA (was $(git rev-parse --short HEAD 2>/dev/null || echo none))"
git reset --quiet --hard "$SHA"
git clean -fdq  # tracked-file deletions only; ignored build output is kept

echo "==> setup"
SERVER_NAME="$SERVER_NAME" CERT_EMAIL="${CERT_EMAIL:-}" bash "$APP/deploy/setup.sh"

echo "==> deployed $(git rev-parse --short HEAD)"
