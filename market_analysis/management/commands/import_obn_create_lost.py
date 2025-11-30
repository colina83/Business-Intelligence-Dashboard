"""
Management command to import OBN Create Lost data from CSV file.

This script reads 'OBN_Create_Lost.csv' and for each row:
1. Creates a Project using Client, Survey Name (Project column), Bid Type
2. Maps Region according to:
   - WAF -> AMME
   - SAM -> NSA
   - GOM -> NSA
   - Middle East -> AMME
   - APAC -> Asia
   - North Sea -> Europe
3. Sets status to 'Ongoing' with date_received from Date_Received column
4. Creates ProjectTechnology with survey_type='3D Seismic', technology='OBN',
   and obn_technique from Survey_Type column (NOAR or ROV) if available
5. Creates ScopeOfWork with water_depth_min/max if available
"""
import csv
from datetime import datetime
from pathlib import Path

from django.core.management.base import BaseCommand
from django.db import transaction

from market_analysis.models import (
    Client, Project, ProjectTechnology, ScopeOfWork
)


# Mapping CSV region values to model region values
REGION_MAP = {
    'WAF': 'AMME',
    'SAM': 'NSA',
    'GOM': 'NSA',
    'Middle East': 'AMME',
    'APAC': 'Asia',
    'North Sea': 'Europe',
}

# Mapping country names to ISO 3166-1 alpha-2 codes
COUNTRY_MAP = {
    'Nigeria': 'NG',
    'Brazil': 'BR',
    'Norway': 'NO',
    'UK': 'GB',
    'India': 'IN',
    'USA': 'US',
    'Saudi Arabia': 'SA',
    'Malaysia': 'MY',
    'Ghana': 'GH',
    'Ivory Coast': 'CI',
    'Suriname': 'SR',
    'Guyana': 'GY',
    'UK or Norway': 'NO',  # Default to Norway when ambiguous
    'Angola': 'AO',
}

# Valid OBN techniques derived from model choices
VALID_OBN_TECHNIQUES = {choice[0] for choice in ProjectTechnology.OBN_TECHNIQUE}

# Valid regions derived from model choices
VALID_REGIONS = {choice[0] for choice in Project.REGIONS}

# Valid bid types derived from model choices
VALID_BID_TYPES = {choice[0] for choice in Project.BID_TYPE}


def parse_date(date_str):
    """Parse date from format like '1-Mar-2019' or '15-Nov-2021'."""
    if not date_str or not date_str.strip():
        return None
    try:
        return datetime.strptime(date_str.strip(), '%d-%b-%Y').date()
    except ValueError:
        return None


class Command(BaseCommand):
    help = 'Import OBN Create Lost data from CSV file into the database.'

    def add_arguments(self, parser):
        parser.add_argument(
            '--csv-path',
            type=str,
            default='OBN_Create_Lost.csv',
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
        skipped_count = 0
        error_count = 0

        for i, row in enumerate(rows, 1):
            try:
                with transaction.atomic():
                    imported = self._import_row(row, i)
                    if imported:
                        created_count += 1
                    else:
                        skipped_count += 1
            except Exception as e:
                self.stderr.write(self.style.ERROR(f'Error in row {i}: {e}'))
                error_count += 1

        self.stdout.write(self.style.SUCCESS(
            f'Import complete. Created: {created_count}, Skipped: {skipped_count}, Errors: {error_count}'
        ))

    def _import_row(self, row, row_num):
        """Import a single row from the CSV."""
        # Extract and clean data
        client_name = row.get('Client', '').strip()
        project_name = row.get('Project', '').strip()
        region_csv = row.get('Region', '').strip()
        country_name = row.get('Country', '').strip()
        bid_type = row.get('Bid_Type', '').strip()

        date_received = parse_date(row.get('Date_Received', ''))
        water_depth_min_str = row.get('Water_Depth_Min', '').strip()
        water_depth_max_str = row.get('Water_Depth_Max', '').strip()
        survey_type_csv = row.get('Survey_Type', '').strip()

        # Validate required fields
        if not client_name:
            raise ValueError('Client name is required')
        if not project_name:
            raise ValueError('Project name is required')

        # Map region according to the specification
        region = REGION_MAP.get(region_csv, region_csv)
        if region not in VALID_REGIONS:
            # If region is still not valid, try to infer from original or skip
            self.stderr.write(self.style.WARNING(
                f'    Warning: Unknown region "{region_csv}" for row {row_num}, setting to empty'
            ))
            region = None

        # Map country to ISO code
        country_code = COUNTRY_MAP.get(country_name)
        if not country_code:
            # Skip rows without a valid country
            self.stderr.write(self.style.WARNING(
                f'    Warning: Unknown country "{country_name}" for row {row_num}, skipping row'
            ))
            return False

        # Validate bid type
        if bid_type not in VALID_BID_TYPES:
            self.stderr.write(self.style.WARNING(
                f'    Warning: Unknown bid type "{bid_type}" for row {row_num}, skipping row'
            ))
            return False

        # 1. Get or create client
        client, _ = Client.objects.get_or_create(name=client_name)

        # 2. Create project with status 'Ongoing'
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

        # 3. Add ProjectTechnology with 3D Seismic and OBN
        tech_kwargs = {
            'project': project,
            'technology': 'OBN',
            'survey_type': '3D Seismic',
        }

        # Add OBN technique if available (NOAR or ROV from Survey_Type column)
        if survey_type_csv and survey_type_csv.upper() in VALID_OBN_TECHNIQUES:
            tech_kwargs['obn_technique'] = survey_type_csv.upper()

        ProjectTechnology.objects.create(**tech_kwargs)
        technique_msg = f' with technique {tech_kwargs.get("obn_technique")}' if tech_kwargs.get('obn_technique') else ''
        self.stdout.write(f'    Added OBN technology (3D Seismic){technique_msg}')

        # 4. Add water depth to Scope of Work if provided
        water_depth_min = None
        water_depth_max = None

        if water_depth_min_str:
            try:
                water_depth_min = int(water_depth_min_str)
            except ValueError:
                self.stderr.write(self.style.WARNING(
                    f'    Warning: Could not parse Water_Depth_Min "{water_depth_min_str}" as integer'
                ))

        if water_depth_max_str:
            try:
                water_depth_max = int(water_depth_max_str)
            except ValueError:
                self.stderr.write(self.style.WARNING(
                    f'    Warning: Could not parse Water_Depth_Max "{water_depth_max_str}" as integer'
                ))

        if water_depth_min is not None or water_depth_max is not None:
            scope_kwargs = {'project': project}
            if water_depth_min is not None:
                scope_kwargs['water_depth_min'] = water_depth_min
            if water_depth_max is not None:
                scope_kwargs['water_depth_max'] = water_depth_max

            ScopeOfWork.objects.create(**scope_kwargs)
            self.stdout.write(
                f'    Added Scope of Work: min_depth={water_depth_min}, max_depth={water_depth_max}'
            )

        return True
