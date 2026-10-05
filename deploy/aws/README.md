# Deployment

| Part | Where | URL |
|---|---|---|
| Web (`apps/web`) | Vercel project `rci-web`, function region `bom1` | https://rci-web-lyart.vercel.app |
| API, worker, Postgres, Redis, Caddy | EC2 `i-0a3f883647a087272` (t3.medium, ap-south-1), Elastic IP `13.127.215.28` | https://13-127-215-28.sslip.io |

The web app calls the API server-side (`RCI_API_URL`). Browser file uploads go straight to the API
(`NEXT_PUBLIC_RCI_API_URL`) because Vercel caps request bodies at 4.5 MB; the API allows the Vercel origins via
`RCI_CORS_ORIGINS`. The site has no login and is open to anyone with the URL.

AWS resources (ap-south-1): security group `sg-034c451e3965e6157` (SSH from one admin IP, 80/443 open),
key pair `rci-deploy` (`~/.ssh/rci-deploy.pem`), Elastic IP `eipalloc-0565228965c7d2221`.

## Secrets

`deploy/aws/.env.prod` (not committed; a copy lives on the host at `/opt/rci/deploy/aws/.env.prod`) holds the
Postgres password, the vault keys, the auth secret and `OPENAI_API_KEY`. Back up the vault keys: without them the
PII stored in `vault.pii_tokens` cannot be decrypted.

## Redeploy the backend

From the repo root (Git Bash):

```
tar --exclude=node_modules --exclude=__pycache__ --exclude='*.egg-info' -czf /tmp/rci-src.tgz services config packages infra deploy
scp -i ~/.ssh/rci-deploy.pem /tmp/rci-src.tgz ubuntu@13.127.215.28:/opt/rci/
ssh -i ~/.ssh/rci-deploy.pem ubuntu@13.127.215.28 "cd /opt/rci && tar xzf rci-src.tgz && rm rci-src.tgz && \
  docker compose -f deploy/aws/docker-compose.yml --env-file deploy/aws/.env.prod up -d --build"
```

Migrations and seed run in the one-shot `migrate` service before the API and worker start.
Logs: `docker compose -f deploy/aws/docker-compose.yml --env-file deploy/aws/.env.prod logs -f api worker`.

SSH is allowed only from the IP that created the security group. From a new IP:
`aws ec2 authorize-security-group-ingress --region ap-south-1 --group-id sg-034c451e3965e6157 --protocol tcp --port 22 --cidr <ip>/32`.

## Redeploy the web app

`cd apps/web && vercel deploy --prod`

## Tear down

```
aws ec2 terminate-instances --region ap-south-1 --instance-ids i-0a3f883647a087272
aws ec2 release-address --region ap-south-1 --allocation-id eipalloc-0565228965c7d2221
aws ec2 delete-security-group --region ap-south-1 --group-id sg-034c451e3965e6157   # after the instance is gone
aws ec2 delete-key-pair --region ap-south-1 --key-name rci-deploy
vercel remove rci-web
```

Terminating the instance deletes its disk, including the database and uploaded documents.
