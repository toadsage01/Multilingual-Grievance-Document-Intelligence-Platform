# Terraform-managed infra for Setu.
#
# What this provisions:
#   - Neon Postgres project (with the pgvector extension enabled)
#   - Upstash Redis DB (free tier)
#   - Render web service (deployed from main)
#
# What this DOES NOT manage (on purpose):
#   - LLM provider API keys. They are secrets, injected via
#     GitHub Actions secrets and Render env vars, never in state.
#
# Usage:
#   cd infra/terraform
#   terraform init
#   terraform plan -var='neon_org_id=…' -var='render_api_key=…' -var='upstash_api_key=…'
#   terraform apply

terraform {
  required_version = ">= 1.6.0"
  required_providers {
    neon = {
      source  = "neondatabase/neon"
      version = "~> 0.6"
    }
    upstash = {
      source  = "upstash/upstash"
      version = "~> 1.0"
    }
    render = {
      source  = "render-oss/render"
      version = "~> 1.0"
    }
  }
}

variable "neon_org_id" {
  type        = string
  description = "Neon organization id (find at https://console.neon.tech)."
}

variable "upstash_email" {
  type        = string
  description = "Upstash account email."
}

variable "upstash_api_key" {
  type        = string
  sensitive   = true
  description = "Upstash API key (https://console.upstash.com)."
}

variable "render_api_key" {
  type        = string
  sensitive   = true
  description = "Render API key with deploy scope."
}

variable "render_owner_id" {
  type        = string
  description = "Render owner (team) id."
}

variable "repo_url" {
  type        = string
  default     = "https://github.com/toadsage01/setu"
  description = "GitHub repo to deploy from."
}

# ----------------------------------------------------------------------
# Neon Postgres
# ----------------------------------------------------------------------
resource "neon_project" "setu" {
  organization_id = var.neon_org_id
  name             = "setu"
  pg_version       = 16
}

resource "neon_branch" "main" {
  project_id = neon_project.setu.id
  name       = "main"
  is_primary = true
}

resource "neon_database" "setu" {
  project_id = neon_project.setu.id
  branch_id = neon_branch.main.id
  name       = "setu"
  owner_name = neon_project.setu.database_user
}

# enable pgvector via the SQL editor or a one-off neon_endpoint run
# (Neon doesn't yet expose CREATE EXTENSION via Terraform, so document
# it instead of faking it):
#
#   CREATE EXTENSION IF NOT EXISTS vector;
#
# This is safe to re-run — the extension is idempotent.

output "neon_database_url" {
  value       = "postgresql://${neon_project.setu.database_user}@${neon_project.setu.database_host}/${neon_database.setu.name}"
  description = "Paste DATABASE_URL into Render and GitHub Actions."
  sensitive   = false
}

# ----------------------------------------------------------------------
# Upstash Redis
# ----------------------------------------------------------------------
provider "upstash" {
  email   = var.upstash_email
  api_key = var.upstash_api_key
}

resource "upstash_redis_database" "setu" {
  name        = "setu-cache"
  region      = "us-east-1"
  tls         = true
  multizone   = false
  eviction    = true
}

output "upstash_redis_url" {
  value       = upstash_redis_database.setu.endpoint
  description = "REDIS_URL endpoint — pair with the upstash token."
}

# ----------------------------------------------------------------------
# Render
# ----------------------------------------------------------------------
provider "render" {
  api_key = var.render_api_key
}

resource "render_web_service" "setu" {
  owner_id         = var.render_owner_id
  name             = "setu"
  repo_url         = var.repo_url
  branch           = "main"
  runtime          = "python"
  plan             = "free"
  build_command    = "pip install -r requirements.txt"
  release_command  = "python manage.py migrate --noinput"
  start_command    = "gunicorn config.wsgi:application --workers 2 --timeout 60"

  env_vars {
    key   = "DJANGO_SETTINGS_MODULE"
    value = "config.settings.prod"
  }
  env_vars {
    key   = "DJANGO_DEBUG"
    value = "False"
  }
  env_vars {
    key   = "ALLOWED_HOSTS"
    value = "setu.onrender.com"
  }
  env_vars {
    key   = "EMBEDDING_MODEL_NAME"
    value = "intfloat/multilingual-e5-base"
  }
  env_vars {
    key   = "RAG_TOP_K"
    value = "5"
  }
  env_vars {
    key   = "RAG_CONFIDENCE_THRESHOLD"
    value = "0.72"
  }
  # secrets are NOT managed in terraform — they go in via the Render UI
  # or `render env set DJANGO_SECRET_KEY … GROQ_API_KEY … GEMINI_API_KEY …`
}
