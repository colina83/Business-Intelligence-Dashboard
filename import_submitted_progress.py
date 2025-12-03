#!/usr/bin/env python
"""
Script to import Submitted-Progress data from CSV into the database.

This script reads the CSV file 'Submitted-Progress .csv' and creates new
opportunity records ONLY for records with status "Submitted-Complete" or 
"In Progress". These are new opportunities that need to be created.

CSV columns mapped:
- Client -> Client (lookup or create)
- Bid_Type -> bid_type
- Project -> name
- Bid_Status -> status (only Submitted-Complete or In Progress)
- Region -> region (mapped to Project.REGIONS choices)
- Country -> country
- Water_Depth_Min -> ScopeOfWork.water_depth_min
- Water_Depth_Max -> ScopeOfWork.water_depth_max
- Survey_Type -> ProjectTechnology.obn_technique
- Date_Received -> date_received
- Date_Submitted -> submission_date
- Node_Type -> ProjectTechnology.obn_system
- Crew Node -> ScopeOfWork.crew_node_count

NOTE: Unique_Op_ID is NOT imported (as per requirements).

Processing rules:
1. Only process records with Bid_Status = "Submitted-Complete" or "In Progress"
2. Always create new records (records don't exist in the database)
3. Leave blank dates if information is not available

Usage:
    python import_submitted_progress.py [csv_file]

Arguments:
    csv_file    Path to CSV file (default: Submitted-Progress .csv)
"""

import argparse
import os
import sys
import re
import csv
from datetime import datetime

# Configuration constants
DEFAULT_CSV_FILENAME = 'Submitted-Progress .csv'
DEFAULT_DJANGO_SETTINGS = 'BIApp.settings'
DEFAULT_SURVEY_TYPE = '3D Seismic'

# Status values to import (only these will be processed)
IMPORT_STATUS_VALUES = ['Submitted-Complete', 'In Progress']

# Set up Django environment
os.environ.setdefault('DJANGO_SETTINGS_MODULE', os.environ.get('DJANGO_SETTINGS_MODULE', DEFAULT_DJANGO_SETTINGS))

import django
django.setup()

from market_analysis.models import (
    Client, Project, ScopeOfWork, ProjectTechnology
)


# Region mapping from CSV format to database format
REGION_MAP = {
    'WAF': 'AMME',       # West Africa -> Africa Middle East
    'SAM': 'NSA',        # South America -> NSA
    'GOM': 'NSA',        # Gulf of Mexico -> NSA
    'North Sea': 'Europe',
    'APAC': 'Asia',
    'Middle East': 'AMME',
}

# Country code mapping
COUNTRY_MAP = {
    'Nigeria': 'NG',
    'Brazil': 'BR',
    'Mexico': 'MX',
    'UK': 'GB',
    'USA': 'US',
    'Norway': 'NO',
    'Malaysia': 'MY',
    'India': 'IN',
    'Egypt': 'EG',
    'Guyana': 'GY',
    'DRC': 'CD',
    'Ivory Coast': 'CI',
    'Saudi Arabia': 'SA',
    'Ghana': 'GH',
    'Angola': 'AO',
    'Suriname': 'SR',
    'Australia': 'AU',
    'Trinidad': 'TT',
    'Cameroon': 'CM',
    'Israel': 'IL',
    'Senegal': 'SN',
    'Equatorial Guinea': 'GQ',
    'Qatar': 'QA',
    'Vietnam': 'VN',
    'Worldwide': 'US',  # Default to US for worldwide/global projects
}

# Bid status mapping from CSV to database choices
BID_STATUS_MAP = {
    'Lost': 'Lost',
    'Award': 'Won',
    'Won': 'Won',
    'No Sale': 'Cancelled',
    'Submitted-Complete': 'Submitted',
    'In Progress': 'Ongoing',
    'See RFP opp': 'Ongoing',
}

# Bid type mapping from CSV to database choices
BID_TYPE_MAP = {
    'RFP': 'RFP',
    'RFQ': 'RFQ',
    'RFI': 'RFI',
    'MC': 'MC',
    'DIR': 'DR',  # Direct Award
    'BQ': 'BQ',
}

# Survey type / OBN technique mapping
SURVEY_TYPE_MAP = {
    'ROV': 'ROV',
    'NOAR': 'NOAR',
    'ROV-NOAR': 'ROV',
    'TS-NOAR': 'NOAR',
    'TS-NOAR-ROV': 'ROV',
    'PRM': None,  # Not an OBN technique
    'CCS': None,  # Not an OBN technique
    'NE-UHR': None,  # Not an OBN technique
}

# Node type / OBN system mapping
NODE_TYPE_MAP = {
    'ZXPLR': 'ZXPLR',
    'Z700': 'Z700',
    'MASS': 'MASS',
    'GPR': 'GPR300',
    'GPR300': 'GPR300',
}


def parse_date(value):
    """Parse date from string in various formats."""
    if not value or value.strip() in ('', '?', 'n/a'):
        return None
    
    value = value.strip()
    
    # Try different date formats
    formats = [
        '%d-%b-%Y',   # 1-Mar-2019
        '%d-%b-%y',   # 1-Mar-19
        '%m/%d/%Y',   # 03/01/2019
        '%Y-%m-%d',   # 2019-03-01
    ]
    
    for fmt in formats:
        try:
            return datetime.strptime(value, fmt).date()
        except ValueError:
            continue
    
    return None


def parse_integer(value):
    """Parse integer from string, handling comma separators and variable values.
    
    For range values like "3500-8200", takes the minimum value (first number).
    This is consistent with how water depth ranges are typically interpreted
    where the minimum depth is the more relevant operational constraint.
    """
    if not value or value.strip() in ('', '-', 'Variable', 'n/a'):
        return None
    
    # Handle ranges like "3500-8200" - take the minimum (first) value
    # The minimum is used as it represents the baseline operational constraint
    if '-' in value and not value.startswith('-'):
        parts = value.split('-')
        if len(parts) >= 2:
            value = parts[0]
    
    value = re.sub(r'[,]', '', value.strip())
    
    try:
        return int(float(value))
    except (ValueError, TypeError):
        return None


def get_or_create_client(client_name):
    """Get or create a Client record by exact name match."""
    if not client_name or client_name.strip() == '':
        return None
    
    client_name = client_name.strip()
    
    # Get or create client by exact name
    client, created = Client.objects.get_or_create(name=client_name)
    return client


def get_country_code(country_name):
    """Get country code from country name.
    
    Returns a 2-letter ISO country code. Uses 'US' as default fallback for
    unknown countries or when country is 'Worldwide' (global scope).
    """
    if not country_name or country_name.strip() == '':
        return 'US'  # Default
    
    country_name = country_name.strip()
    return COUNTRY_MAP.get(country_name, 'US')


def get_region(region_value):
    """Map CSV region to database region."""
    if not region_value:
        return None
    
    region_value = region_value.strip()
    
    # First check if it's already a valid region
    valid_regions = [r[0] for r in Project.REGIONS]
    if region_value in valid_regions:
        return region_value
    
    # Otherwise try to map it
    return REGION_MAP.get(region_value)


def get_bid_type(bid_type_value):
    """Map CSV bid type to database bid type."""
    if not bid_type_value:
        return 'BQ'  # Default
    
    bid_type_value = bid_type_value.strip().upper()
    return BID_TYPE_MAP.get(bid_type_value, 'BQ')


def get_bid_status(status_value):
    """Map CSV bid status to database status."""
    if not status_value or status_value.strip() == '':
        return 'Ongoing'
    
    status_value = status_value.strip()
    return BID_STATUS_MAP.get(status_value, 'Ongoing')


def get_obn_technique(survey_type):
    """Map survey type to OBN technique."""
    if not survey_type:
        return None
    
    survey_type = survey_type.strip().upper()
    return SURVEY_TYPE_MAP.get(survey_type)


def get_obn_system(node_type):
    """Map node type to OBN system."""
    if not node_type:
        return None
    
    node_type = node_type.strip().upper()
    return NODE_TYPE_MAP.get(node_type)


def create_new_project(row):
    """Create a new project record from CSV row.
    
    Implementation Note: Uses a two-step process (create then update) because:
    1. The Project model's save() method auto-populates submission_date, award_date,
       and lost_date when the status transitions (see models.py lines 132-147).
    2. The problem requirement specifies to "leave blank dates if not available",
       so we must bypass this auto-population behavior.
    3. We first create with 'Ongoing' status (which has no date auto-population),
       then use QuerySet.update() to set the actual status and dates directly,
       which bypasses the model's save() method entirely.
    """
    client = get_or_create_client(row.get('Client', '').strip())
    
    project_name = row.get('Project', '').strip()
    if not project_name:
        return None
    
    # Get mapped values
    region = get_region(row.get('Region', ''))
    country = get_country_code(row.get('Country', ''))
    bid_type = get_bid_type(row.get('Bid_Type', ''))
    status = get_bid_status(row.get('Bid_Status', ''))
    
    # Parse dates - leave blank (None) if not available
    date_received = parse_date(row.get('Date_Received', ''))
    date_submitted = parse_date(row.get('Date_Submitted', ''))
    
    # Step 1: Create project with 'Ongoing' status to get a pk
    # We use 'Ongoing' because it doesn't trigger date auto-population
    project = Project.objects.create(
        name=project_name,
        client=client,
        bid_type=bid_type,
        region=region,
        country=country,
        date_received=date_received,
        status='Ongoing',
    )
    
    # Step 2: Use QuerySet.update() to set the final status and dates,
    # bypassing the model's save() which auto-populates missing dates
    update_fields = {'status': status}
    
    # Only set submission_date if we have it from CSV (leave blank otherwise)
    if date_submitted:
        update_fields['submission_date'] = date_submitted
    
    # Update the project directly in database
    Project.objects.filter(pk=project.pk).update(**update_fields)
    
    # Refresh from database to get updated values
    project.refresh_from_db()
    
    return project


def create_scope_of_work(project, row):
    """Create ScopeOfWork record for a new project."""
    water_depth_min = parse_integer(row.get('Water_Depth_Min', ''))
    water_depth_max = parse_integer(row.get('Water_Depth_Max', ''))
    crew_node_count = parse_integer(row.get('Crew Node', ''))
    
    # Check if there's any data to add
    if water_depth_min is None and water_depth_max is None and crew_node_count is None:
        return None
    
    scope = ScopeOfWork.objects.create(
        project=project,
        water_depth_min=water_depth_min,
        water_depth_max=water_depth_max,
        crew_node_count=crew_node_count,
    )
    
    return scope


def create_technology(project, row):
    """Create ProjectTechnology record for a new project."""
    survey_type = row.get('Survey_Type', '').strip()
    node_type = row.get('Node_Type', '').strip()
    
    obn_technique = get_obn_technique(survey_type)
    obn_system = get_obn_system(node_type)
    
    # Check if there's any data to add
    if obn_technique is None and obn_system is None:
        return None
    
    tech = ProjectTechnology.objects.create(
        project=project,
        technology='OBN',
        survey_type=DEFAULT_SURVEY_TYPE,
        obn_technique=obn_technique,
        obn_system=obn_system,
    )
    return tech


def process_row(row, stats):
    """
    Process a single CSV row and create a new project.
    
    Only processes rows with Bid_Status = "Submitted-Complete" or "In Progress".
    Always creates a new record (records don't exist in the database).
    
    Returns the newly created project.
    """
    csv_client = row.get('Client', '').strip()
    csv_project = row.get('Project', '').strip()
    csv_bid_type = row.get('Bid_Type', '').strip()
    csv_status = row.get('Bid_Status', '').strip()
    
    # Validate required fields
    if not csv_client or not csv_project:
        stats['skipped'] += 1
        print(f"  Skipped: Missing client or project name")
        return None
    
    # Create new project
    project = create_new_project(row)
    if project is None:
        stats['skipped'] += 1
        return None
    
    print(f"  Created: {csv_client} / {csv_project} (Bid Type: {csv_bid_type})")
    stats['created'] += 1
    
    # Create scope of work
    scope = create_scope_of_work(project, row)
    if scope:
        stats['scope_created'] += 1
        # Format output with conditional display for None values
        depth_min = scope.water_depth_min if scope.water_depth_min is not None else 'N/A'
        depth_max = scope.water_depth_max if scope.water_depth_max is not None else 'N/A'
        nodes = scope.crew_node_count if scope.crew_node_count is not None else 'N/A'
        print(f"    -> Created Scope of Work (Water depth: {depth_min}-{depth_max}m, Nodes: {nodes})")
    
    # Create technology
    tech = create_technology(project, row)
    if tech:
        stats['tech_created'] += 1
        technique = tech.obn_technique if tech.obn_technique else 'N/A'
        system = tech.obn_system if tech.obn_system else 'N/A'
        print(f"    -> Created Technology (technique: {technique}, system: {system})")
    
    return project


def main():
    """Main function to import Submitted-Progress data from CSV."""
    parser = argparse.ArgumentParser(
        description='Import Submitted-Progress data from CSV into the database.',
        epilog='Creates new opportunity records for Submitted-Complete and In Progress status only.'
    )
    parser.add_argument(
        'csv_file',
        nargs='?',
        default=None,
        help=f'Path to CSV file (default: {DEFAULT_CSV_FILENAME})'
    )
    args = parser.parse_args()
    
    # Determine CSV file path
    if args.csv_file:
        csv_file = args.csv_file
    else:
        csv_file = os.path.join(
            os.path.dirname(os.path.abspath(__file__)),
            DEFAULT_CSV_FILENAME
        )
    
    if not os.path.exists(csv_file):
        print(f"Error: CSV file not found: {csv_file}")
        sys.exit(1)
    
    print(f"Reading CSV file: {csv_file}")
    print("=" * 70)
    print(f"Only importing records with Bid_Status: {IMPORT_STATUS_VALUES}")
    print("=" * 70)
    
    # Read CSV file with error handling
    try:
        with open(csv_file, 'r', encoding='utf-8-sig') as f:
            reader = csv.DictReader(f)
            all_rows = list(reader)
    except PermissionError:
        print(f"Error: Permission denied when reading: {csv_file}")
        sys.exit(1)
    except UnicodeDecodeError as e:
        print(f"Error: Unable to decode CSV file (encoding issue): {e}")
        sys.exit(1)
    except csv.Error as e:
        print(f"Error: CSV parsing error: {e}")
        sys.exit(1)
    except OSError as e:
        print(f"Error: Unable to read file: {e}")
        sys.exit(1)
    
    # Filter rows to only include Submitted-Complete and In Progress
    rows = [row for row in all_rows if row.get('Bid_Status', '').strip() in IMPORT_STATUS_VALUES]
    
    print(f"Found {len(all_rows)} total rows in CSV file.")
    print(f"Filtered to {len(rows)} rows with status: {IMPORT_STATUS_VALUES}\n")
    
    # Statistics
    stats = {
        'total': len(rows),
        'created': 0,
        'skipped': 0,
        'scope_created': 0,
        'tech_created': 0,
    }
    
    # Process each filtered row
    for i, row in enumerate(rows, 1):
        csv_client = row.get('Client', '').strip()
        csv_project = row.get('Project', '').strip()
        csv_status = row.get('Bid_Status', '').strip()
        
        print(f"\n[{i}/{len(rows)}] Processing: Client='{csv_client}', Project='{csv_project}', Status='{csv_status}'")
        
        process_row(row, stats)
    
    # Print summary
    print("\n" + "=" * 70)
    print("IMPORT SUMMARY")
    print("=" * 70)
    print(f"Total rows processed:     {stats['total']}")
    print(f"Projects created:         {stats['created']}")
    print(f"Skipped:                  {stats['skipped']}")
    print(f"Scope of Work created:    {stats['scope_created']}")
    print(f"Technology created:       {stats['tech_created']}")
    print("=" * 70)


if __name__ == '__main__':
    main()
