from datetime import date
from django.core.management.base import BaseCommand
from django.db import transaction
from django.utils import timezone

from market_analysis.models import Project


class Command(BaseCommand):
    help = "Convert Submitted -> Won and clear award_date where rules apply."

    def add_arguments(self, parser):
        parser.add_argument(
            "--dry-run",
            action="store_true",
            help="Show what would change without writing to the database.",
        )
        parser.add_argument(
            "--yes",
            action="store_true",
            help="Apply changes without interactive confirmation.",
        )

    def handle(self, *args, **options):
        dry_run = options["dry_run"]
        auto_yes = options["yes"]

        qs = Project.objects.filter(status="Submitted")
        total = qs.count()
        if total == 0:
            self.stdout.write("No projects with status 'Submitted' found.")
            return

        self.stdout.write(f"Found {total} project(s) with status 'Submitted'.")

        if dry_run:
            self.stdout.write("Running in dry-run mode. No database changes will be made.")

        if not auto_yes and not dry_run:
            confirm = input("Proceed to change all 'Submitted' -> 'Won' and adjust award dates? [y/N]: ")
            if confirm.strip().lower() != "y":
                self.stdout.write("Aborted by user.")
                return

        changed_count = 0
        cleared_award_count = 0

        # Nov 26 rule (month=11, day=26)
        MAGIC_MONTH = 11
        MAGIC_DAY = 26

        with transaction.atomic():
            for p in qs.select_related("contract"):
                sub = p.submission_date
                award = p.award_date

                clear_award = False
                # If award == submission -> clear
                if award and sub and award == sub:
                    clear_award = True

                # If award is Nov 26 -> clear
                if award and award.month == MAGIC_MONTH and award.day == MAGIC_DAY:
                    clear_award = True

                # Report planned actions
                if dry_run:
                    self.stdout.write(f"[DRY] Project {p.project_id} ({p.internal_id or p.name}): status Submitted -> Won")
                    if award:
                        if clear_award:
                            self.stdout.write(f"    [DRY] award_date {award} -> CLEAR")
                        else:
                            self.stdout.write(f"    [DRY] award_date preserved: {award}")
                    else:
                        self.stdout.write(f"    [DRY] award_date is already blank")
                    continue

                # Apply changes using queryset update (avoids Project.save hooks)
                # 1) Clear award_date if required
                if clear_award:
                    Project.objects.filter(pk=p.pk).update(award_date=None)
                    cleared_award_count += 1

                # 2) Set status -> Won
                Project.objects.filter(pk=p.pk).update(status="Won")
                changed_count += 1

        if dry_run:
            self.stdout.write("Dry-run complete. No changes were written.")
        else:
            self.stdout.write(f"Updated {changed_count} project(s) status -> 'Won'.")
            self.stdout.write(f"Cleared award_date on {cleared_award_count} project(s).")
            self.stdout.write("Done.")