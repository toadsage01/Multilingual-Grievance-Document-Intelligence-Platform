from django.db import migrations, models
import uuid


class Migration(migrations.Migration):
    initial = True
    dependencies = []

    operations = [
        migrations.CreateModel(
            name="Department",
            fields=[
                (
                    "id",
                    models.UUIDField(
                        default=uuid.uuid4,
                        editable=False,
                        primary_key=True,
                        serialize=False,
                    ),
                ),
                ("name", models.CharField(max_length=255)),
                ("slug", models.SlugField(max_length=100, unique=True)),
                (
                    "guardrail_prompt",
                    models.TextField(
                        blank=True,
                        help_text=(
                            "Department-specific system prompt / restrictions "
                            "(e.g. 'do not advise on legal matters outside education policy')."
                        ),
                    ),
                ),
                ("created_at", models.DateTimeField(auto_now_add=True)),
            ],
            options={
                "db_table": "departments",
                "ordering": ["name"],
            },
        ),
    ]
