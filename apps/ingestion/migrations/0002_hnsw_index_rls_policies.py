"""Raw SQL migration: HNSW vector index + RLS policies.

Two things Django's ORM can't express for us:
1. HNSW index with vector_cosine_ops — the right index type for cosine
   similarity search at >100k chunk scale.
2. Row-level security policies that filter every tenant-scoped table
   by the current Postgres session var app.current_tenant (set by the
   TenantContextMiddleware).

These are written as forward + reverse SQL pairs so we can roll back
cleanly during local dev iterations.
"""
from django.db import migrations


# every tenant-scoped table. the policy assumes a department_id column
# exists on each — enforced by the schema migration chain.
_TENANT_TABLES = [
    "documents",
    "document_chunks",
    "conversations",
    "messages",
    "grievances",
    "grievance_status_history",
]


def forwards(apps, schema_editor):
    with schema_editor.connection.cursor() as cur:
        # 1. HNSW vector index — load-bearing one for retrieval
        cur.execute("""
            CREATE INDEX IF NOT EXISTS idx_chunks_embedding
            ON document_chunks
            USING hnsw (embedding vector_cosine_ops);
        """)

        # 2. RLS: enable + policy per table. FORCE is what makes the
        # policy bind even to the table owner (so superuser queries still
        # get filtered — important for the isolation test).
        for table in _TENANT_TABLES:
            cur.execute(f"DROP POLICY IF EXISTS tenant_isolation ON {table};")
            cur.execute(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY;")
            cur.execute(f"ALTER TABLE {table} FORCE ROW LEVEL SECURITY;")
            cur.execute(f"""
                CREATE POLICY tenant_isolation ON {table}
                USING (department_id::text = current_setting('app.current_tenant', true));
            """)


def backwards(apps, schema_editor):
    with schema_editor.connection.cursor() as cur:
        for table in _TENANT_TABLES:
            cur.execute(f"DROP POLICY IF EXISTS tenant_isolation ON {table};")
            cur.execute(f"ALTER TABLE {table} NO FORCE ROW LEVEL SECURITY;")
            cur.execute(f"ALTER TABLE {table} DISABLE ROW LEVEL SECURITY;")
        cur.execute("DROP INDEX IF EXISTS idx_chunks_embedding;")


class Migration(migrations.Migration):
    dependencies = [("ingestion", "0001_initial")]
    operations = [migrations.RunPython(forwards, backwards)]
