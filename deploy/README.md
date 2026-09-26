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
BOB_API_KEY=...          # optional, Inference-scoped; also install Bob Shell on the host
BOB_MODE=live            # live | replay | off
BOB_DAILY_BUDGET=5.0     # Bobcoins per UTC day across all visitors (0 = no cap)
```

`setup.sh` adds `TRUST_FORWARDED_FOR=true`, `BOB_MODE=live` and `ALLOWED_ORIGIN` if they are missing.

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

## Automatic deploys (GitHub Actions → S3 → SSM)

`.github/workflows/ci-deploy.yml` runs backend and frontend checks on every pull request and push.
On a push to `main` that passes, the `deploy` job:

1. assumes the `medusa-github-deploy` AWS role through GitHub OIDC (no AWS keys in GitHub),
2. uploads `git archive` of the commit to the private bucket `medusa-deploy-295662440018-apse1`
   (`releases/<sha>.tar.gz`, deleted after 14 days),
3. sends one SSM run-command to the instance with a 15-minute presigned link. The instance runs
   that archive's `deploy/deploy.sh <sha> <link>`, which checks the archive's embedded commit id,
   syncs the code into `/opt/medusa` (keeping `.venv`, `node_modules`, `.next`), runs `setup.sh`
   and records the commit in `/opt/medusa/.deployed-sha`,
4. checks `DEPLOY_URL/api/health`.

The server needs no GitHub or S3 credentials, and SSH stays closed except to the admin IP.
Deploys are serialised (GitHub concurrency group plus a lock on the server).

### What is set up

| Piece | Where |
| --- | --- |
| SSM access for the instance | IAM role + instance profile `medusa-ec2-ssm` (`AmazonSSMManagedInstanceCore`) |
| GitHub OIDC provider | `token.actions.githubusercontent.com` in account 295662440018 |
| Deploy role | `medusa-github-deploy`: assumable only by the OIDC subject `repo:Opti5-Labs@268177406/medusa@1387802377:environment:production` (this repo uses GitHub's immutable subject format, owner and repo IDs included, so a renamed or re-created repo cannot assume it); may `ssm:SendCommand` to `i-06b85ec4b09275bc4` with `AWS-RunShellScript`, read command results, and put/get `releases/*` in the bucket |
| Release bucket | `medusa-deploy-295662440018-apse1`: public access blocked, SSE-S3, TLS only, 14-day expiry |
| Server config | `/etc/medusa/deploy.env` (`SERVER_NAME=18-141-113-159.sslip.io`) |
| Repository variables | `AWS_DEPLOY_ROLE_ARN`, `AWS_REGION`, `EC2_INSTANCE_ID`, `RELEASE_BUCKET`, `DEPLOY_URL` |
| GitHub environment | `production`, deployments limited to `main` |

To redeploy without a new commit, use "Run workflow" on the Actions tab (on `main`). The manual
rsync route above still works for emergencies.
