# Place this file at market_analysis/management/commands/backfill_changelog.py
from django.core.management.base import BaseCommand
from django.db import transaction
from market_analysis.models import (
    BidTypeHistory, ProjectStatusHistory, ChangeLog
)
from django.utils import timezone

class Command(BaseCommand):
    help = 'Backfill ChangeLog from existing BidTypeHistory and ProjectStatusHistory tables.'

    def handle(self, *args, **options):
        self.stdout.write('Starting backfill of ChangeLog...')

        created = 0
        skipped = 0
        with transaction.atomic():
            # Backfill BidTypeHistory -> ChangeLog (BID)
            for b in BidTypeHistory.objects.select_related('project').order_by('changed_at'):
                # skip if equivalent ChangeLog already exists
                exists = ChangeLog.objects.filter(
                    project=b.project,
                    change_type='BID',
                    field_name='bid_type',
                    previous_value=b.previous_bid_type,
                    new_value=b.new_bid_type,
                    changed_at=b.changed_at
                ).exists()
                if exists:
                    skipped += 1
                    continue

                ChangeLog.objects.create(
                    project=b.project,
                    change_type='BID',
                    field_name='bid_type',
                    previous_value=b.previous_bid_type,
                    new_value=b.new_bid_type,
                    event_date=None,
                    changed_at=b.changed_at,
                    notes=(b.notes or '')
                )
                created += 1

            # Backfill ProjectStatusHistory -> ChangeLog (STATUS)
            for s in ProjectStatusHistory.objects.select_related('project').order_by('changed_at'):
                # Determine event_date for submission/award/lost if available
                event_date = None
                new = (s.new_status or '').lower()
                if new == 'submitted':
                    # prefer project's submission_date if set
                    event_date = getattr(s.project, 'submission_date', None)
                elif new == 'won':
                    event_date = getattr(s.project, 'award_date', None)
                elif new == 'lost':
                    event_date = getattr(s.project, 'lost_date', None)

                exists = ChangeLog.objects.filter(
                    project=s.project,
                    change_type='STATUS',
                    field_name='status',
                    previous_value=s.previous_status,
                    new_value=s.new_status,
                    changed_at=s.changed_at
                ).exists()
                if exists:
                    skipped += 1
                    continue

                ChangeLog.objects.create(
                    project=s.project,
                    change_type='STATUS',
                    field_name='status',
                    previous_value=s.previous_status,
                    new_value=s.new_status,
                    event_date=event_date,
                    changed_at=s.changed_at,
                    notes=(s.notes or '')
                )
                created += 1

        self.stdout.write(self.style.SUCCESS(f'Backfill complete. Created: {created}, Skipped (existing): {skipped}'))