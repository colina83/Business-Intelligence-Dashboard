#!/usr/bin/env python
"""
Script to import Submitted-Progress data from CSV into the database.

This script reads the CSV file 'Submitted-Progress .csv' and creates opportunity
records for each field using the information in the CSV file.

CSV columns mapped:
- Unique_Op_ID -> internal_id (if not already set)
- Bid_Status -> status (mapped to Project.STATUS choices)
- Last_Update -> used for tracking update date
- Update_Comment -> notes/comments
- Bid_Type -> bid_type (mapped to Project.BID_TYPE choices)
- Client -> Client (FK lookup or create)
- Project -> name
- Region -> region (mapped to Project.REGIONS choices)
- Country -> country
- Water_Depth_Min -> ScopeOfWork.water_depth_min
- Water_Depth_Max -> ScopeOfWork.water_depth_max
- Survey_Type -> ProjectTechnology.obn_technique
- Date_Received -> date_received
- Date_Submitted -> submission_date
- Node_Type -> ProjectTechnology.obn_system
- Crew Node -> ScopeOfWork.crew_node_count

Processing rules:
1. Use Client and Project name to find existing records using fuzzy logic
2. If record exists: update fields with new data
3. If record does not exist: create new record
4. Leave blank dates if information is not available

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

# Fuzzy matching thresholds
HIGH_CONFIDENCE_THRESHOLD = 0.85  # Auto-match without confirmation
MEDIUM_CONFIDENCE_THRESHOLD = 0.5  # Needs confirmation

# Set up Django environment
os.environ.setdefault('DJANGO_SETTINGS_MODULE', os.environ.get('DJANGO_SETTINGS_MODULE', DEFAULT_DJANGO_SETTINGS))

import django
django.setup()

from market_analysis.models import (
    Client, Project, ScopeOfWork, ProjectTechnology, Competitor
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
    'Worldwide': '',  # Empty code for worldwide/global projects
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


def normalize_name(name):
    """Normalize a name for comparison."""
    if not name:
        return ''
    name = re.sub(r'^[*\s]+', '', name)
    name = ' '.join(name.split())
    name = name.lower().strip()
    return name


def calculate_similarity(s1, s2):
    """
    Calculate similarity between two strings.
    Returns a score between 0 and 1, where 1 is an exact match.
    """
    s1_norm = normalize_name(s1)
    s2_norm = normalize_name(s2)
    
    if not s1_norm or not s2_norm:
        return 0.0
    
    if s1_norm == s2_norm:
        return 1.0
    
    # Check if one contains the other
    if s1_norm in s2_norm or s2_norm in s1_norm:
        len_ratio = min(len(s1_norm), len(s2_norm)) / max(len(s1_norm), len(s2_norm))
        return 0.7 + (0.2 * len_ratio)
    
    # Check for partial word matches
    words1 = set(s1_norm.split())
    words2 = set(s2_norm.split())
    
    if words1 and words2:
        common = words1.intersection(words2)
        total = words1.union(words2)
        if common:
            return 0.5 + (0.4 * len(common) / len(total))
    
    # Character-level similarity using n-grams
    def get_ngrams(s, n=2):
        return set(s[i:i+n] for i in range(len(s) - n + 1))
    
    ngrams1 = get_ngrams(s1_norm)
    ngrams2 = get_ngrams(s2_norm)
    
    if ngrams1 and ngrams2:
        intersection = len(ngrams1.intersection(ngrams2))
        union = len(ngrams1.union(ngrams2))
        return 0.3 * (intersection / union) if union > 0 else 0.0
    
    return 0.0


def find_matching_project(csv_client, csv_project, all_projects):
    """
    Find a matching project in the database based on client and project name.
    
    Returns a tuple of (project, match_score, match_type).
    """
    best_match = None
    best_score = 0.0
    
    for project in all_projects:
        db_client_name = project.client.name if project.client else ''
        db_project_name = project.name
        
        client_score = calculate_similarity(csv_client, db_client_name)
        project_score = calculate_similarity(csv_project, db_project_name)
        
        # Combined score
        combined_score = (client_score * 0.4 + project_score * 0.6)
        
        if client_score > 0.7 and project_score > 0.7:
            combined_score = min(1.0, combined_score * 1.1)
        
        if combined_score > best_score:
            best_score = combined_score
            best_match = project
    
    # Determine match type
    if best_score >= 0.95:
        match_type = 'exact'
    elif best_score >= HIGH_CONFIDENCE_THRESHOLD:
        match_type = 'high'
    elif best_score >= MEDIUM_CONFIDENCE_THRESHOLD:
        match_type = 'medium'
    else:
        match_type = 'none'
    
    return best_match, best_score, match_type


def find_matching_competitor(winner_name):
    """Find a matching competitor from the predefined list."""
    if not winner_name or winner_name.strip() == '':
        return None
    
    winner_norm = normalize_name(winner_name)
    
    # Check for known competitors in the comment
    competitor_keywords = {
        'PXGEO': 'PXGEO',
        'Shearwater': 'SHEARWATER',
        'SAE': 'SAE',
        'BGP': 'BGP',
        'SLB': 'SLB',
        'Viridien': 'VIRIDIEN',
    }
    
    for keyword, choice in competitor_keywords.items():
        if keyword.lower() in winner_norm:
            return choice
    
    return None


# Global cache for clients
_client_cache = None


def get_cached_clients():
    """Get cached list of all clients."""
    global _client_cache
    if _client_cache is None:
        _client_cache = list(Client.objects.all())
    return _client_cache


def refresh_client_cache():
    """Refresh the client cache after creating new clients."""
    global _client_cache
    _client_cache = list(Client.objects.all())


def get_or_create_client(client_name):
    """Get or create a Client record."""
    if not client_name or client_name.strip() == '':
        return None
    
    client_name = client_name.strip()
    client_name = re.sub(r'^[*\s]+', '', client_name)
    
    # Try to find existing client using cache
    all_clients = get_cached_clients()
    best_match = None
    best_score = 0.0
    
    for client in all_clients:
        score = calculate_similarity(client_name, client.name)
        if score > best_score:
            best_score = score
            best_match = client
    
    if best_score >= HIGH_CONFIDENCE_THRESHOLD and best_match:
        return best_match
    
    # Create new client
    client, created = Client.objects.get_or_create(name=client_name)
    if created:
        refresh_client_cache()
    return client


def get_country_code(country_name):
    """Get country code from country name.
    
    Returns a 2-letter ISO country code. Uses 'US' as default fallback for
    unknown countries or when country is 'Worldwide' (global scope).
    """
    if not country_name or country_name.strip() == '':
        return 'US'  # Default
    
    country_name = country_name.strip()
    code = COUNTRY_MAP.get(country_name, 'US')
    # Handle worldwide/empty codes by defaulting to US
    return code if code else 'US'


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


def update_project(project, row):
    """Update an existing project with new data from CSV row.
    
    Uses QuerySet.update() to set status and dates directly, bypassing the
    model's save() method which auto-populates dates when missing.
    """
    update_fields = {}
    
    # Update region if not set
    region = get_region(row.get('Region', ''))
    if region and not project.region:
        update_fields['region'] = region
    
    # Update dates if not set and available in CSV
    date_received = parse_date(row.get('Date_Received', ''))
    if date_received and not project.date_received:
        update_fields['date_received'] = date_received
    
    date_submitted = parse_date(row.get('Date_Submitted', ''))
    if date_submitted and not project.submission_date:
        update_fields['submission_date'] = date_submitted
    
    # Update status if provided and different
    new_status = get_bid_status(row.get('Bid_Status', ''))
    if new_status and new_status != project.status:
        update_fields['status'] = new_status
    
    if update_fields:
        # Use QuerySet.update() to bypass model save() auto-population
        Project.objects.filter(pk=project.pk).update(**update_fields)
        project.refresh_from_db()
        return True
    
    return False


def create_or_update_scope_of_work(project, row):
    """Create or update ScopeOfWork record."""
    water_depth_min = parse_integer(row.get('Water_Depth_Min', ''))
    water_depth_max = parse_integer(row.get('Water_Depth_Max', ''))
    crew_node_count = parse_integer(row.get('Crew Node', ''))
    
    # Check if there's any data to add
    if water_depth_min is None and water_depth_max is None and crew_node_count is None:
        return None, False
    
    scope, created = ScopeOfWork.objects.get_or_create(
        project=project,
        defaults={
            'water_depth_min': water_depth_min,
            'water_depth_max': water_depth_max,
            'crew_node_count': crew_node_count,
        }
    )
    
    if not created:
        updated = False
        if water_depth_min is not None and scope.water_depth_min is None:
            scope.water_depth_min = water_depth_min
            updated = True
        if water_depth_max is not None and scope.water_depth_max is None:
            scope.water_depth_max = water_depth_max
            updated = True
        if crew_node_count is not None and scope.crew_node_count is None:
            scope.crew_node_count = crew_node_count
            updated = True
        if updated:
            scope.save()
    
    return scope, created


def create_or_update_technology(project, row):
    """Create or update ProjectTechnology record."""
    survey_type = row.get('Survey_Type', '').strip()
    node_type = row.get('Node_Type', '').strip()
    
    obn_technique = get_obn_technique(survey_type)
    obn_system = get_obn_system(node_type)
    
    # Check if there's any data to add
    if obn_technique is None and obn_system is None:
        return None, False
    
    # Try to find existing technology record
    existing_tech = ProjectTechnology.objects.filter(project=project).first()
    
    if existing_tech:
        updated = False
        if obn_technique and not existing_tech.obn_technique:
            existing_tech.obn_technique = obn_technique
            updated = True
        if obn_system and not existing_tech.obn_system:
            existing_tech.obn_system = obn_system
            updated = True
        if updated:
            existing_tech.save()
        return existing_tech, False
    else:
        tech = ProjectTechnology.objects.create(
            project=project,
            technology='OBN',
            survey_type=DEFAULT_SURVEY_TYPE,
            obn_technique=obn_technique,
            obn_system=obn_system,
        )
        return tech, True


def create_competitor_if_lost(project, row):
    """Create competitor record if project is lost and winner info is available."""
    if project.status != 'Lost':
        return None, False
    
    update_comment = row.get('Update_Comment', '').strip()
    if not update_comment:
        return None, False
    
    competitor_choice = find_matching_competitor(update_comment)
    if not competitor_choice:
        return None, False
    
    competitor, created = Competitor.objects.get_or_create(
        project=project,
        defaults={'name': competitor_choice, 'notes': update_comment}
    )
    
    return competitor, created


def process_row(row, all_projects, stats):
    """
    Process a single CSV row.
    
    Returns the newly created project if one was created, None otherwise.
    """
    csv_client = row.get('Client', '').strip()
    csv_project = row.get('Project', '').strip()
    
    if not csv_client or not csv_project:
        stats['skipped'] += 1
        return None
    
    # Find matching project
    match, score, match_type = find_matching_project(csv_client, csv_project, all_projects)
    
    if match_type in ('exact', 'high'):
        # Update existing project
        project = match
        is_new = False
        db_client_name = project.client.name if project.client else 'N/A'
        print(f"  Match found: {db_client_name}/{project.name} (score: {score:.2f})")
        
        updated = update_project(project, row)
        if updated:
            stats['updated'] += 1
            print(f"    -> Updated project fields")
        else:
            stats['matched'] += 1
    elif match_type == 'medium':
        # Medium confidence - create new to be safe
        project = create_new_project(row)
        if project is None:
            stats['skipped'] += 1
            return None
        is_new = True
        print(f"  Created new project: {project.name} (ambiguous match, score: {score:.2f})")
        stats['created'] += 1
    else:
        # No match - create new project
        project = create_new_project(row)
        if project is None:
            stats['skipped'] += 1
            return None
        is_new = True
        print(f"  Created new project: {project.name}")
        stats['created'] += 1
    
    # Create/update scope of work
    scope, scope_created = create_or_update_scope_of_work(project, row)
    if scope:
        if scope_created:
            stats['scope_created'] += 1
            print(f"    -> Created Scope of Work")
        else:
            stats['scope_updated'] += 1
            print(f"    -> Updated Scope of Work")
    
    # Create/update technology
    tech, tech_created = create_or_update_technology(project, row)
    if tech:
        if tech_created:
            stats['tech_created'] += 1
            print(f"    -> Created Technology (technique: {tech.obn_technique}, system: {tech.obn_system})")
        else:
            stats['tech_updated'] += 1
            print(f"    -> Updated Technology")
    
    # Create competitor if lost
    competitor, comp_created = create_competitor_if_lost(project, row)
    if competitor:
        if comp_created:
            stats['competitor_created'] += 1
            print(f"    -> Created Competitor: {competitor.name}")
    
    return project if is_new else None


def main():
    """Main function to import Submitted-Progress data from CSV."""
    parser = argparse.ArgumentParser(
        description='Import Submitted-Progress data from CSV into the database.',
        epilog='Creates opportunity records for each field using the CSV data.'
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
    
    # Load all existing projects for matching
    all_projects = list(Project.objects.select_related('client').all())
    print(f"Found {len(all_projects)} existing projects in database.")
    
    # Read CSV file with error handling
    try:
        with open(csv_file, 'r', encoding='utf-8-sig') as f:
            reader = csv.DictReader(f)
            rows = list(reader)
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
    
    print(f"Found {len(rows)} rows in CSV file.\n")
    
    # Statistics
    stats = {
        'total': len(rows),
        'matched': 0,
        'updated': 0,
        'created': 0,
        'skipped': 0,
        'scope_created': 0,
        'scope_updated': 0,
        'tech_created': 0,
        'tech_updated': 0,
        'competitor_created': 0,
    }
    
    # Process each row
    for i, row in enumerate(rows, 1):
        csv_client = row.get('Client', '').strip()
        csv_project = row.get('Project', '').strip()
        
        print(f"\n[{i}/{len(rows)}] Processing: Client='{csv_client}', Project='{csv_project}'")
        
        new_project = process_row(row, all_projects, stats)
        
        # Append newly created project to cache
        if new_project is not None:
            all_projects.append(new_project)
    
    # Print summary
    print("\n" + "=" * 70)
    print("IMPORT SUMMARY")
    print("=" * 70)
    print(f"Total rows processed:     {stats['total']}")
    print(f"Matched (no update):      {stats['matched']}")
    print(f"Updated existing:         {stats['updated']}")
    print(f"Created new:              {stats['created']}")
    print(f"Skipped:                  {stats['skipped']}")
    print(f"Scope of Work created:    {stats['scope_created']}")
    print(f"Scope of Work updated:    {stats['scope_updated']}")
    print(f"Technology created:       {stats['tech_created']}")
    print(f"Technology updated:       {stats['tech_updated']}")
    print(f"Competitors created:      {stats['competitor_created']}")
    print("=" * 70)


if __name__ == '__main__':
    main()
