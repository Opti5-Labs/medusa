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

## Automatic deploys (GitHub Actions → SSM)

`.github/workflows/ci-deploy.yml` runs backend and frontend checks on every pull request and push.
On a push to `main` that passes, the `deploy` job assumes an AWS role through GitHub OIDC and
sends one SSM run-command to the instance: `bash /opt/medusa/deploy/deploy.sh <sha>`. The server
checks out that exact commit of `main` and runs `setup.sh`. There are no AWS keys or SSH keys in
GitHub, and SSH stays closed to everyone but the admin IP.

One-time setup (steps 1 and 2 grant access, so an admin runs them):

**0. Instance can be reached by SSM** (done): the instance has the `medusa-ec2-ssm` instance
profile (`AmazonSSMManagedInstanceCore`) and the SSM agent is online.

**1. Let GitHub Actions deploy.** Create the OIDC provider and a role that only the `production`
environment of `Opti5-Labs/medusa` can assume, and that can only run commands on this instance:

```bash
ACC=295662440018 INSTANCE=i-06b85ec4b09275bc4 REGION=ap-southeast-1
aws iam create-open-id-connect-provider --url https://token.actions.githubusercontent.com \
  --client-id-list sts.amazonaws.com
aws iam create-role --role-name medusa-github-deploy --assume-role-policy-document "{
  \"Version\":\"2012-10-17\",\"Statement\":[{\"Effect\":\"Allow\",
  \"Principal\":{\"Federated\":\"arn:aws:iam::$ACC:oidc-provider/token.actions.githubusercontent.com\"},
  \"Action\":\"sts:AssumeRoleWithWebIdentity\",
  \"Condition\":{\"StringEquals\":{\"token.actions.githubusercontent.com:aud\":\"sts.amazonaws.com\",
  \"token.actions.githubusercontent.com:sub\":\"repo:Opti5-Labs/medusa:environment:production\"}}}]}"
aws iam put-role-policy --role-name medusa-github-deploy --policy-name medusa-ssm-deploy --policy-document "{
  \"Version\":\"2012-10-17\",\"Statement\":[
  {\"Effect\":\"Allow\",\"Action\":\"ssm:SendCommand\",\"Resource\":[
    \"arn:aws:ec2:$REGION:$ACC:instance/$INSTANCE\",
    \"arn:aws:ssm:$REGION::document/AWS-RunShellScript\"]},
  {\"Effect\":\"Allow\",\"Action\":[\"ssm:GetCommandInvocation\",\"ssm:ListCommandInvocations\"],\"Resource\":\"*\"}]}"
```

**2. Let the server pull the (private) repo.** The server's key pair already exists
(`/root/.ssh/medusa_repo_key`). Add its public half as a read-only deploy key:

```bash
ssh ubuntu@18.141.113.159 'sudo cat /root/.ssh/medusa_repo_key.pub' > /tmp/medusa_repo_key.pub
gh repo deploy-key add /tmp/medusa_repo_key.pub --repo Opti5-Labs/medusa --title medusa-server-readonly
```

**3. Turn `/opt/medusa` into a checkout** (keeps `.venv`, `node_modules`, `.next`):

```bash
ssh ubuntu@18.141.113.159 'sudo bash -c "
  cd /opt/medusa && git init -q && git config --global --add safe.directory /opt/medusa
  git remote add origin git@github.com:Opti5-Labs/medusa.git
  GIT_SSH_COMMAND=\"ssh -i /root/.ssh/medusa_repo_key -o StrictHostKeyChecking=accept-new\" git fetch -q origin main
  git reset -q --hard origin/main && git clean -fdq && chown -R medusa:medusa /opt/medusa"'
```

`/etc/medusa/deploy.env` (done) holds `SERVER_NAME=18-141-113-159.sslip.io`.

**4. Repository variables and the environment:**

```bash
gh variable set AWS_DEPLOY_ROLE_ARN --repo Opti5-Labs/medusa --body arn:aws:iam::295662440018:role/medusa-github-deploy
gh variable set AWS_REGION --repo Opti5-Labs/medusa --body ap-southeast-1
gh variable set EC2_INSTANCE_ID --repo Opti5-Labs/medusa --body i-06b85ec4b09275bc4
gh variable set DEPLOY_URL --repo Opti5-Labs/medusa --body https://18-141-113-159.sslip.io
```

In GitHub, Settings → Environments → `production` (created on the first deploy), restrict
deployment branches to `main`.

After that, every merge to `main` deploys itself; the run log shows the tail of `setup.sh` and a
health check. To redeploy without a new commit, use "Run workflow" on the Actions tab.
