"""
Management command to import OBN Bid Analytics data from CSV file.

This simulates the application behavior by:
1. Creating projects with initial status 'Ongoing'
2. Adding OBN technology with technique if provided
3. Transitioning to 'Submitted' status with Date_Received as submission_date
4. Transitioning to 'Won' status with Date_Award
5. Adding contract date, actual start/end dates
6. Adding water depth data to Scope of Work
"""
import csv
from datetime import datetime
from pathlib import Path

from django.core.management.base import BaseCommand
from django.db import transaction

from market_analysis.models import (
    Client, Project, ProjectTechnology, ProjectContract, ScopeOfWork
)


# Mapping country names to ISO 3166-1 alpha-2 codes
COUNTRY_MAP = {
    'Nigeria': 'NG',
    'Mexico': 'MX',
    'UK': 'GB',
    'USA': 'US',
    'Norway': 'NO',
    'India': 'IN',
    'Egypt': 'EG',
    'Malaysia': 'MY',
    'Guyana': 'GY',
    'Trinidad': 'TT',
}

# Mapping CSV bid types to model bid types
BID_TYPE_MAP = {
    'RFP': 'RFP',
    'MC': 'MC',
    'DIR': 'DR',  # Direct Award
}


def parse_date(date_str):
    """Parse date from format like '1-Dec-19' or '12-Oct-21'."""
    if not date_str or not date_str.strip():
        return None
    try:
        return datetime.strptime(date_str.strip(), '%d-%b-%y').date()
    except ValueError:
        return None


class Command(BaseCommand):
    help = 'Import OBN Bid Analytics data from CSV file into the database.'

    def add_arguments(self, parser):
        parser.add_argument(
            '--csv-path',
            type=str,
            default='All_OBN_Bid_Analytics - Copilot.csv',
            help='Path to the CSV file to import'
        )
        parser.add_argument(
            '--dry-run',
            action='store_true',
            help='Print what would be done without making changes'
        )

    def handle(self, *args, **options):
        csv_path = Path(options['csv_path'])
        dry_run = options['dry_run']

        if not csv_path.exists():
            self.stderr.write(self.style.ERROR(f'CSV file not found: {csv_path}'))
            return

        self.stdout.write(f'Importing data from: {csv_path}')
        if dry_run:
            self.stdout.write(self.style.WARNING('DRY RUN - no changes will be made'))

        with open(csv_path, 'r', encoding='utf-8-sig') as f:
            reader = csv.DictReader(f)
            rows = list(reader)

        self.stdout.write(f'Found {len(rows)} rows to import')

        if dry_run:
            for i, row in enumerate(rows, 1):
                self.stdout.write(f"Row {i}: {row.get('Client', 'N/A')} - {row.get('Project', 'N/A')}")
            return

        created_count = 0
        error_count = 0

        for i, row in enumerate(rows, 1):
            try:
                with transaction.atomic():
                    self._import_row(row, i)
                    created_count += 1
            except Exception as e:
                self.stderr.write(self.style.ERROR(f'Error in row {i}: {e}'))
                error_count += 1

        self.stdout.write(self.style.SUCCESS(
            f'Import complete. Created: {created_count}, Errors: {error_count}'
        ))

    def _import_row(self, row, row_num):
        """Import a single row from the CSV."""
        # Extract and clean data
        client_name = row.get('Client', '').strip()
        project_name = row.get('Project', '').strip()
        region = row.get('Region', '').strip()
        country_name = row.get('Country', '').strip()
        bid_type_csv = row.get('Bid_Type', '').strip()

        # Handle BOM in first column
        if not bid_type_csv:
            bid_type_csv = row.get('\ufeffBid_Type', '').strip()

        date_received = parse_date(row.get('Date_Received', ''))
        date_submitted = parse_date(row.get('Date_Submitted', ''))
        date_award = parse_date(row.get('Date_Award', ''))
        date_contract = parse_date(row.get('Date_Contract', ''))
        actual_date_start = parse_date(row.get('Actual_Date_Start', ''))
        actual_date_end = parse_date(row.get('Actual_Date_End', ''))

        min_water_depth = row.get('Min_Water_Depth', '').strip()
        max_water_depth = row.get('Max_Water_Depth', '').strip()
        obn_technique = row.get('OBN_Tecnique', '').strip()

        # Validate required fields
        if not client_name:
            raise ValueError('Client name is required')
        if not project_name:
            raise ValueError('Project name is required')

        # Map country to ISO code
        country_code = COUNTRY_MAP.get(country_name)
        if not country_code:
            raise ValueError(f'Unknown country: {country_name}')

        # Map bid type
        bid_type = BID_TYPE_MAP.get(bid_type_csv)
        if not bid_type:
            raise ValueError(f'Unknown bid type: {bid_type_csv}')

        # 1. Get or create client
        client, _ = Client.objects.get_or_create(name=client_name)

        # 2. Create project with initial status 'Ongoing'
        project = Project.objects.create(
            client=client,
            name=project_name,
            country=country_code,
            region=region,
            bid_type=bid_type,
            date_received=date_received,
            status='Ongoing'
        )
        self.stdout.write(f'  Row {row_num}: Created project "{project_name}" for client "{client_name}"')

        # 3. Add technology (OBN with technique if provided)
        tech_kwargs = {
            'project': project,
            'technology': 'OBN',
            'survey_type': '3D Seismic',  # Default survey type
        }
        if obn_technique and obn_technique in ['NOAR', 'ROV', 'DN']:
            tech_kwargs['obn_technique'] = obn_technique

        ProjectTechnology.objects.create(**tech_kwargs)
        self.stdout.write(f'    Added OBN technology' + (f' with technique {obn_technique}' if obn_technique else ''))

        # 4. Transition to 'Submitted' status
        # Use Date_Submitted if available, otherwise use Date_Received as submission date
        submission_date = date_submitted if date_submitted else date_received
        if submission_date:
            project.status = 'Submitted'
            project.submission_date = submission_date
            project.save()
            self.stdout.write(f'    Transitioned to Submitted (date: {submission_date})')

        # 5. Transition to 'Won' status if Date_Award exists
        if date_award:
            project.status = 'Won'
            project.award_date = date_award
            project.save()
            self.stdout.write(f'    Transitioned to Won (date: {date_award})')

            # 6. Create/update ProjectContract for Won projects
            contract, _ = ProjectContract.objects.get_or_create(project=project)

            if date_contract:
                contract.contract_date = date_contract
            if actual_date_start:
                contract.actual_start = actual_date_start
            if actual_date_end:
                contract.actual_end = actual_date_end

            contract.save()
            self.stdout.write(f'    Updated contract: contract_date={date_contract}, start={actual_date_start}, end={actual_date_end}')
        else:
            # Still add actual dates even if not Won (for projects that have execution dates but no award date)
            if actual_date_start or actual_date_end:
                contract, _ = ProjectContract.objects.get_or_create(project=project)
                if actual_date_start:
                    contract.actual_start = actual_date_start
                if actual_date_end:
                    contract.actual_end = actual_date_end
                if date_contract:
                    contract.contract_date = date_contract
                contract.save()
                self.stdout.write(f'    Added contract dates: start={actual_date_start}, end={actual_date_end}')

        # 7. Add water depth to Scope of Work if provided
        if min_water_depth or max_water_depth:
            scope_kwargs = {'project': project}
            if min_water_depth:
                try:
                    scope_kwargs['water_depth_min'] = int(min_water_depth)
                except ValueError:
                    pass
            if max_water_depth:
                try:
                    scope_kwargs['water_depth_max'] = int(max_water_depth)
                except ValueError:
                    pass

            if len(scope_kwargs) > 1:  # More than just project
                ScopeOfWork.objects.create(**scope_kwargs)
                self.stdout.write(f'    Added Scope of Work: min_depth={min_water_depth}, max_depth={max_water_depth}')
