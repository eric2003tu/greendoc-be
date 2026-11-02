from django.core.management.base import BaseCommand

from learning.models import TermStat


class Command(BaseCommand):
    help = "Print the words learned by the shared engine, one per line (for review or for a Hunspell .dic)."

    def add_arguments(self, parser):
        parser.add_argument("--min-contributors", type=int, default=3)
        parser.add_argument("--names", action="store_true", help="Include names as well as ordinary words.")

    def handle(self, *args, **options):
        kinds = [TermStat.WORD] + ([TermStat.NAME] if options["names"] else [])
        rows = TermStat.objects.filter(kind__in=kinds, removed=False, contributors__gte=options["min_contributors"]).order_by("key")
        for row in rows:
            self.stdout.write(row.key.capitalize() if row.kind == TermStat.NAME else row.key)
