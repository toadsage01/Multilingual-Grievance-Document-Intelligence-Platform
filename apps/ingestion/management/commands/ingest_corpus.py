"""Bulk-ingest management command for seeding + operational re-ingest.

Usage:
    python manage.py ingest_corpus --input path/to/corpus.jsonl
    python manage.py ingest_corpus --seed
"""
import json
from pathlib import Path
from django.core.management.base import BaseCommand
from apps.ingestion.services import ingest_text
from apps.tenancy.models import Department


class Command(BaseCommand):
    help = "Bulk ingest documents from a JSONL file or the bundled seed corpus"

    def add_arguments(self, parser):
        parser.add_argument("--input", type=str, help="Path to JSONL corpus file")
        parser.add_argument(
            "--seed", action="store_true",
            help="Use the bundled synthetic seed corpus",
        )

    def handle(self, *args, **opts):
        if not (opts["input"] or opts["seed"]):
            self.stderr.write("must pass --input <path> or --seed")
            return

        if opts["seed"]:
            corpus_path = Path(__file__).resolve().parent.parent.parent / "seed" / "corpus.jsonl"
        else:
            corpus_path = Path(opts["input"])

        if not corpus_path.exists():
            self.stderr.write(f"corpus not found: {corpus_path}")
            return

        depts = {d.slug: d for d in Department.objects.all()}

        n_total = 0
        n_skipped = 0
        n_inserted = 0
        with corpus_path.open() as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                row = json.loads(line)
                dept = depts.get(row["department_slug"])
                if not dept:
                    self.stderr.write(f"unknown department: {row['department_slug']}")
                    continue
                result = ingest_text(
                    department_id=dept.id,
                    title=row["title"],
                    raw_text=row["raw_text"],
                    source_url=row.get("source_url", ""),
                )
                n_total += 1
                if result.new:
                    n_inserted += 1
                else:
                    n_skipped += 1
                self.stdout.write(
                    f"  {row['title'][:60]}: {'NEW' if result.new else 'SKIP'} chunks={result.chunk_count}"
                )

        self.stdout.write(
            self.style.SUCCESS(
                f"done: {n_total} processed, {n_inserted} inserted, {n_skipped} skipped"
            )
        )
