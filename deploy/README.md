# Deploying Medusa to one EC2 instance

One Ubuntu 24.04 host runs everything: nginx, the Next.js frontend, the FastAPI
backend and the Docker sandbox. Nothing else should run on it (the backend's user
is in the `docker` group, which is root-equivalent).

## 1. Instance

- Ubuntu 24.04 LTS, `t3.xlarge` (4 vCPU / 16 GB), 30 GB gp3 disk.
- Security group: 80 and 443 open to the world, 22 open only to your IP.

## 2. Copy the code (no secrets, no build output)

```bash
rsync -az --delete \
  --exclude '.git' --exclude 'node_modules' --exclude '.venv' --exclude '.next' \
  --exclude '.env' --exclude '.env.*' --exclude '__pycache__' \
  ./ ubuntu@HOST:/tmp/medusa/
ssh ubuntu@HOST 'sudo rsync -a --delete /tmp/medusa/ /opt/medusa/'
```

## 3. Secrets

Create `/etc/medusa/env` on the host (mode 640, group `medusa`). Never commit it or bake it into an AMI.

```
IBM_WATSONX_API_KEY=...
IBM_WATSONX_PROJECT_ID=...
IBM_WATSONX_URL=https://eu-de.ml.cloud.ibm.com
IBM_WATSONX_MODEL=ibm/granite-4-h-small
GITHUB_TOKEN=            # optional
```

`setup.sh` adds `TRUST_FORWARDED_FOR=true`, `BOB_MODE=replay` and `ALLOWED_ORIGIN` if they are missing.

## 4. Install / update

```bash
ssh ubuntu@HOST 'sudo SERVER_NAME=1-2-3-4.sslip.io CERT_EMAIL=you@example.com bash /opt/medusa/deploy/setup.sh'
```

`1-2-3-4.sslip.io` resolves to `1.2.3.4`, so it works for HTTPS without buying a domain.
Leave out `SERVER_NAME`/`CERT_EMAIL` for plain HTTP on the IP.

Re-run the same two steps (copy, setup) to deploy an update.

## Operations

```bash
sudo systemctl status medusa-backend medusa-frontend
sudo journalctl -u medusa-backend -f
docker ps          # sandbox containers are short-lived and always removed
```
