"""Raw SQL: BEFORE UPDATE trigger on grievances to refresh updated_at.

Django's auto_now=True already does this at the ORM layer, but the
spec asks for the trigger at the DB level too — belt + suspenders so
a manual SQL UPDATE can't accidentally stale the timestamp.
"""
from django.db import migrations


def forwards(apps, schema_editor):
    with schema_editor.connection.cursor() as cur:
        cur.execute("""
            CREATE OR REPLACE FUNCTION fn_grievance_status_touch()
            RETURNS TRIGGER AS $$
            BEGIN
                NEW.updated_at = now();
                RETURN NEW;
            END;
            $$ LANGUAGE plpgsql;
        """)
        cur.execute("""
            CREATE TRIGGER trg_grievance_touch
            BEFORE UPDATE ON grievances
            FOR EACH ROW EXECUTE FUNCTION fn_grievance_status_touch();
        """)


def backwards(apps, schema_editor):
    with schema_editor.connection.cursor() as cur:
        cur.execute("DROP TRIGGER IF EXISTS trg_grievance_touch ON grievances;")
        cur.execute("DROP FUNCTION IF EXISTS fn_grievance_status_touch();")


class Migration(migrations.Migration):
    dependencies = [("grievances", "0001_initial")]
    operations = [migrations.RunPython(forwards, backwards)]
