# Setu Terraform

Provisions the three cloud primitives this project needs:
- **Neon Postgres** (with the `vector` extension enabled — see below)
- **Upstash Redis** (free-tier cache + RQ queue)
- **Render web service** (deploys from `main` on every push)

LLM provider API keys are deliberately **not** managed here. They're
secrets, injected via the Render dashboard or `render env set`. Keep
them out of state.

## Usage

```bash
cd infra/terraform
terraform init
terraform plan \
  -var='neon_org_id=org-abc123' \
  -var='upstash_email=you@example.com' \
  -var='upstash_api_key=…' \
  -var='render_api_key=…' \
  -var='render_owner_id=…'
terraform apply
```

## Enabling pgvector on Neon

Terraform can't run `CREATE EXTENSION` yet — do it once via the Neon
SQL editor:

```sql
CREATE EXTENSION IF NOT EXISTS vector;
```

Idempotent, safe to re-run.
