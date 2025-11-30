#!/usr/bin/env python
"""
Script to import OBN Lost Bid data from CSV into the database.

This script reads the CSV file 'OBN_Pricing - Lost Copilot.csv' and processes
lost bid records following these rules:

1. Use Client and Survey Name to find existing records using fuzzy logic
2. If record exists:
   - Set status to Submitted with Bid Submitted Date
   - Set status to Lost (Award Date left blank)
   - Select competitor from Winner column if available (using fuzzy matching)
3. If record does not exist:
   - Create new record with bid status 'RFP'
   - Follow the rest of the indications

Data imported to:
- Financial table (P&L): Total Direct Cost, Total Revenue, GP$, GM%,
  Bid Duration, Total Overhead, Total Depreciation, EBIT$, EBIT%, EBIT$/Day,
  Taxes, Net $, Net %, Net/Day
- Project Technology: Bid Node (Node System)
- Scope of Work: Bid Node Count (Total Node Count)

Usage:
    python import_obn_lost_bids.py [csv_file]
"""

import argparse
import os
import sys
import re
import csv
from datetime import datetime
from decimal import Decimal, InvalidOperation

# Configuration constants
DEFAULT_CSV_FILENAME = 'OBN_Pricing - Lost Copilot.csv'
DEFAULT_DJANGO_SETTINGS = 'BIApp.settings'
DEFAULT_SURVEY_TYPE = '3D Seismic'

# Fuzzy matching thresholds
HIGH_CONFIDENCE_THRESHOLD = 0.85  # Auto-match without confirmation
MEDIUM_CONFIDENCE_THRESHOLD = 0.5  # Needs confirmation
LOW_CONFIDENCE_THRESHOLD = 0.3   # Report as ambiguous

# Set up Django environment
os.environ.setdefault('DJANGO_SETTINGS_MODULE', os.environ.get('DJANGO_SETTINGS_MODULE', DEFAULT_DJANGO_SETTINGS))

import django
django.setup()

from django.utils import timezone
from market_analysis.models import (
    Client, Project, Financial, ScopeOfWork, ProjectTechnology, Competitor
)


def parse_currency(value):
    """Parse currency string to Decimal, handling formats like '$1,234.56' or '($1,234.56)'."""
    if not value or value.strip() in ('', '-', '$-', '$ -   ', '$ -'):
        return None
    
    # Remove whitespace
    value = value.strip()
    
    # Check for negative values in parentheses
    is_negative = value.startswith('(') and value.endswith(')')
    if is_negative:
        value = value[1:-1]
    
    # Remove currency symbols and commas
    value = re.sub(r'[$,]', '', value).strip()
    
    if not value or value == '-':
        return None
    
    try:
        result = Decimal(value)
        return -result if is_negative else result
    except (InvalidOperation, ValueError):
        return None


def parse_percentage(value):
    """Parse percentage string to Decimal, e.g., '29.00%' -> 29.00."""
    if not value or value.strip() in ('', '-'):
        return None
    
    value = value.strip().rstrip('%').strip()
    
    # Handle negative percentages in parentheses
    is_negative = value.startswith('(') and value.endswith(')')
    if is_negative:
        value = value[1:-1]
    
    # Handle negative percentages with minus sign
    is_negative = is_negative or value.startswith('-')
    if value.startswith('-'):
        value = value[1:]
    
    try:
        result = Decimal(value)
        return -result if is_negative else result
    except (InvalidOperation, ValueError):
        return None


def parse_integer(value):
    """Parse integer from string, handling comma separators."""
    if not value or value.strip() in ('', '-'):
        return None
    
    value = re.sub(r'[,]', '', value.strip())
    
    try:
        # Handle float values by converting to int
        return int(float(value))
    except (ValueError, TypeError):
        return None


def parse_date(value):
    """Parse date from string in various formats."""
    if not value or value.strip() == '':
        return None
    
    value = value.strip()
    
    # Try different date formats
    formats = [
        '%d-%b-%y',   # 1-Mar-19
        '%d-%b-%Y',   # 1-Mar-2019
        '%m/%d/%Y',   # 03/01/2019
        '%Y-%m-%d',   # 2019-03-01
    ]
    
    for fmt in formats:
        try:
            return datetime.strptime(value, fmt).date()
        except ValueError:
            continue
    
    return None


def normalize_name(name):
    """Normalize a name for comparison by removing extra spaces, lowercase, etc."""
    if not name:
        return ''
    # Remove leading asterisks or special chars
    name = re.sub(r'^[*\s]+', '', name)
    # Remove extra whitespace
    name = ' '.join(name.split())
    # Lowercase and strip
    name = name.lower().strip()
    return name


def calculate_similarity(s1, s2):
    """
    Calculate similarity between two strings using multiple methods.
    Returns a score between 0 and 1, where 1 is an exact match.
    """
    s1_norm = normalize_name(s1)
    s2_norm = normalize_name(s2)
    
    if not s1_norm or not s2_norm:
        return 0.0
    
    if s1_norm == s2_norm:
        return 1.0
    
    # Check if one contains the other completely
    if s1_norm in s2_norm or s2_norm in s1_norm:
        # Higher score for closer length matches
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
    
    # Character-level similarity (simple Jaccard on character n-grams)
    def get_ngrams(s, n=2):
        return set(s[i:i+n] for i in range(len(s) - n + 1))
    
    ngrams1 = get_ngrams(s1_norm)
    ngrams2 = get_ngrams(s2_norm)
    
    if ngrams1 and ngrams2:
        intersection = len(ngrams1.intersection(ngrams2))
        union = len(ngrams1.union(ngrams2))
        return 0.3 * (intersection / union) if union > 0 else 0.0
    
    return 0.0


def find_matching_project(csv_client, csv_survey, all_projects):
    """
    Find a matching project in the database based on client and survey name.
    
    Returns a tuple of (project, match_score, match_type).
    match_type can be: 'exact', 'high', 'medium', 'low', 'none'
    """
    best_match = None
    best_score = 0.0
    
    for project in all_projects:
        # Get client name from project
        db_client_name = project.client.name if project.client else ''
        db_project_name = project.name
        
        # Calculate similarity for client
        client_score = calculate_similarity(csv_client, db_client_name)
        
        # Calculate similarity for project/survey
        project_score = calculate_similarity(csv_survey, db_project_name)
        
        # Combined score (weighted average - project name is more important)
        combined_score = (client_score * 0.4 + project_score * 0.6)
        
        # Boost score if both client and project match reasonably well
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
    elif best_score >= LOW_CONFIDENCE_THRESHOLD:
        match_type = 'low'
    else:
        match_type = 'none'
    
    return best_match, best_score, match_type


def find_matching_competitor(winner_name):
    """
    Find a matching competitor from the predefined list using fuzzy matching.
    
    Returns the competitor choice value or None if not found with confidence.
    """
    if not winner_name or winner_name.strip() == '':
        return None
    
    competitor_choices = dict(Competitor.COMPETITOR_CHOICES)
    winner_norm = normalize_name(winner_name)
    
    best_match = None
    best_score = 0.0
    
    for choice_value, choice_label in Competitor.COMPETITOR_CHOICES:
        choice_norm = normalize_name(choice_value)
        label_norm = normalize_name(choice_label)
        
        # Check similarity with both value and label
        score_value = calculate_similarity(winner_norm, choice_norm)
        score_label = calculate_similarity(winner_norm, label_norm)
        score = max(score_value, score_label)
        
        if score > best_score:
            best_score = score
            best_match = choice_value
    
    # Only return if we have a reasonably confident match
    if best_score >= 0.6:
        return best_match
    
    return None


def get_or_create_client(client_name):
    """Get or create a Client record."""
    if not client_name or client_name.strip() == '':
        return None
    
    # Clean client name
    client_name = client_name.strip()
    # Remove leading asterisks
    client_name = re.sub(r'^[*\s]+', '', client_name)
    
    # Try to find existing client with fuzzy matching
    all_clients = list(Client.objects.all())
    best_match = None
    best_score = 0.0
    
    for client in all_clients:
        score = calculate_similarity(client_name, client.name)
        if score > best_score:
            best_score = score
            best_match = client
    
    # If high confidence match, use existing client
    if best_score >= HIGH_CONFIDENCE_THRESHOLD and best_match:
        return best_match
    
    # Otherwise create new client
    client, created = Client.objects.get_or_create(
        name=client_name
    )
    return client


def get_country_from_region(region):
    """Map region to a default country code."""
    region_country_map = {
        'NSA': 'BR',  # North South America -> Brazil
        'AMME': 'NG',  # Africa Middle East -> Nigeria
        'Asia': 'MY',  # Asia -> Malaysia
        'Europe': 'NO',  # Europe -> Norway
        'Australasia': 'AU',  # Australasia -> Australia
        'Global': 'US',  # Global -> USA
    }
    return region_country_map.get(region, 'US')


def get_obn_system_choice(bid_node_type):
    """Map bid node type to OBN_SYSTEM choices."""
    if not bid_node_type:
        return None
    
    mapping = {
        'ZXPLR': 'ZXPLR',
        'Z700': 'Z700',
        'MASS': 'MASS',
        'GPR300': 'GPR300',
    }
    
    normalized = bid_node_type.strip().upper()
    return mapping.get(normalized, 'OTHER' if normalized else None)


def create_new_project(csv_client, csv_survey, row):
    """
    Create a new project record with RFP bid status.
    """
    # Get or create client
    client = get_or_create_client(csv_client)
    
    # Parse region
    region = row.get('Region', '').strip()
    if region and region not in [r[0] for r in Project.REGIONS]:
        region = None
    
    # Get country from region
    country = get_country_from_region(region)
    
    # Parse bid submitted date
    bid_submitted = parse_date(row.get('Bid Submitted', ''))
    
    # Clean survey name (remove leading asterisks)
    survey_name = csv_survey
    survey_name = re.sub(r'^[*\s]+', '', survey_name)
    
    # Create project with RFP bid status
    project = Project.objects.create(
        name=survey_name,
        client=client,
        bid_type='RFP',
        region=region if region else None,
        country=country,
        date_received=bid_submitted,
        status='Ongoing'  # Start as Ongoing, will transition to Submitted then Lost
    )
    
    return project


def update_project_to_submitted(project, bid_submitted_date):
    """
    Update project status to Submitted with the bid submitted date.
    """
    project.status = 'Submitted'
    project.submission_date = bid_submitted_date
    project.save()


def update_project_to_lost(project, winner_name=None):
    """
    Update project status to Lost and create competitor record if winner is known.
    Award date is left blank as per requirements.
    """
    project.status = 'Lost'
    project.award_date = None  # Leave blank as per requirements
    project.save()
    
    # Try to find and add competitor if winner name is provided
    if winner_name:
        competitor_choice = find_matching_competitor(winner_name)
        if competitor_choice:
            Competitor.objects.get_or_create(
                project=project,
                defaults={'name': competitor_choice}
            )
        else:
            # If competitor not found in list, we might need to add it
            # For now, we'll skip unknown competitors and report them
            print(f"    Note: Competitor '{winner_name}' not found in predefined list")


def import_financial_data(project, row):
    """
    Create or update Financial record for a project with P&L data.
    """
    financial, created = Financial.objects.get_or_create(project=project)
    
    # Parse duration for Bid Duration = Project Duration
    duration = parse_integer(row.get('Bid_Duration'))
    
    # Update financial record using QuerySet.update() to bypass auto-calculation
    update_fields = {
        'total_direct_cost': parse_currency(row.get('Total Direct Cost')),
        'total_revenue': parse_currency(row.get('Total Revenue')),
        'gp': parse_currency(row.get(' GP$ ', row.get('GP$', row.get('GP $')))),
        'gm': parse_percentage(row.get('GM%')),
        'total_overhead': parse_currency(row.get('Total Overhead')),
        'depreciation': parse_currency(row.get('Total Depreciation')),
        'ebit_amount': parse_currency(row.get('EBIT$')),
        'ebit_pct': parse_percentage(row.get('EBIT%')),
        'ebit_day': parse_currency(row.get('EBIT$/Day')),
        'taxes': parse_currency(row.get('Taxes')),
        'net_amount': parse_currency(row.get('Net $')),
        'net_pct': parse_percentage(row.get('Net %')),
        'net_day': parse_currency(row.get('Net/Day')),
        'duration_raw': duration,
        'duration_with_dt': duration,
    }
    
    # Remove None values to avoid overwriting existing data
    update_fields = {k: v for k, v in update_fields.items() if v is not None}
    
    if update_fields:
        Financial.objects.filter(pk=financial.pk).update(**update_fields)
    
    return financial, created


def import_project_technology(project, row):
    """
    Create or update ProjectTechnology record with OBN system (Bid Node).
    """
    bid_node_type = row.get('Bid_Node_Type', '').strip()
    obn_system = get_obn_system_choice(bid_node_type)
    
    if not obn_system:
        return None, False
    
    # Try to find an existing technology record for this project
    existing_tech = ProjectTechnology.objects.filter(project=project).first()
    
    if existing_tech:
        existing_tech.obn_system = obn_system
        existing_tech.save()
        return existing_tech, False
    else:
        # Create new technology record
        tech = ProjectTechnology.objects.create(
            project=project,
            technology='OBN',
            survey_type=DEFAULT_SURVEY_TYPE,
            obn_system=obn_system
        )
        return tech, True


def import_scope_of_work(project, row):
    """
    Create or update ScopeOfWork record with Bid Node Count.
    """
    crew_node_count = parse_integer(row.get('Bid_Node_Count'))
    
    if crew_node_count is None:
        return None, False
    
    scope, created = ScopeOfWork.objects.get_or_create(
        project=project,
        defaults={'crew_node_count': crew_node_count}
    )
    
    if not created:
        scope.crew_node_count = crew_node_count
        scope.save()
    
    return scope, created


def process_row(row, all_projects, stats, ambiguous_records):
    """
    Process a single CSV row.
    """
    csv_client = row.get('Client', '').strip()
    csv_survey = row.get('Survey', '').strip()
    
    # Skip if no client or survey name
    if not csv_client or not csv_survey:
        stats['skipped'] += 1
        return
    
    # Clean leading asterisks from client name
    csv_client_clean = re.sub(r'^[*\s]+', '', csv_client)
    csv_survey_clean = re.sub(r'^[*\s]+', '', csv_survey)
    
    # Find matching project
    match, score, match_type = find_matching_project(
        csv_client_clean, csv_survey_clean, all_projects
    )
    
    # Parse bid submitted date
    bid_submitted = parse_date(row.get('Bid Submitted', ''))
    
    # Handle based on match type
    if match_type in ('exact', 'high'):
        # High confidence match - proceed
        project = match
        is_new = False
        db_client_name = project.client.name if project.client else 'N/A'
        print(f"  Match found: {db_client_name}/{project.name} (score: {score:.2f})")
        stats['matched'] += 1
    elif match_type == 'medium':
        # Medium confidence - report as ambiguous and skip
        ambiguous_records.append({
            'csv_client': csv_client,
            'csv_survey': csv_survey,
            'db_match': f"{match.client.name if match and match.client else 'N/A'}/{match.name if match else 'N/A'}",
            'score': score,
            'reason': 'Medium confidence match - needs confirmation'
        })
        stats['ambiguous'] += 1
        return
    elif match_type == 'low':
        # Low confidence - report as ambiguous and create new
        ambiguous_records.append({
            'csv_client': csv_client,
            'csv_survey': csv_survey,
            'db_match': f"{match.client.name if match and match.client else 'N/A'}/{match.name if match else 'N/A'}",
            'score': score,
            'reason': 'Low confidence match - creating new record'
        })
        project = create_new_project(csv_client_clean, csv_survey_clean, row)
        is_new = True
        print(f"  Created new project: {project.name}")
        stats['created'] += 1
    else:
        # No match - create new project
        project = create_new_project(csv_client_clean, csv_survey_clean, row)
        is_new = True
        print(f"  Created new project: {project.name}")
        stats['created'] += 1
    
    # Update status flow: Ongoing -> Submitted -> Lost
    if project.status == 'Ongoing':
        update_project_to_submitted(project, bid_submitted)
        print(f"    -> Status: Submitted (Date: {bid_submitted})")
    
    # Then transition to Lost
    update_project_to_lost(project, row.get('Winner'))
    print(f"    -> Status: Lost")
    
    # Import financial data
    financial, fin_created = import_financial_data(project, row)
    if fin_created:
        stats['financial_created'] += 1
        print("    -> Created Financial record")
    else:
        stats['financial_updated'] += 1
        print("    -> Updated Financial record")
    
    # Import technology (Bid Node = Node System)
    tech, tech_created = import_project_technology(project, row)
    if tech:
        if tech_created:
            stats['tech_created'] += 1
            print(f"    -> Created Technology record (OBN System: {tech.obn_system})")
        else:
            stats['tech_updated'] += 1
            print(f"    -> Updated Technology record (OBN System: {tech.obn_system})")
    
    # Import scope of work (Bid Node Count = Total Node Count)
    scope, scope_created = import_scope_of_work(project, row)
    if scope:
        if scope_created:
            stats['scope_created'] += 1
            print(f"    -> Created Scope of Work (Node Count: {scope.crew_node_count})")
        else:
            stats['scope_updated'] += 1
            print(f"    -> Updated Scope of Work (Node Count: {scope.crew_node_count})")


def main():
    """Main function to import OBN Lost Bid data from CSV."""
    # Parse command line arguments
    parser = argparse.ArgumentParser(
        description='Import OBN Lost Bid data from CSV into the database.',
        epilog='Records will be matched using fuzzy logic. Ambiguous matches are reported to the user.'
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
    
    # Load all projects from database for matching
    all_projects = list(Project.objects.select_related('client').all())
    print(f"Found {len(all_projects)} existing projects in database.")
    
    # Read CSV file
    with open(csv_file, 'r', encoding='utf-8-sig') as f:
        reader = csv.DictReader(f)
        rows = list(reader)
    
    print(f"Found {len(rows)} rows in CSV file.\n")
    
    # Statistics
    stats = {
        'total': len(rows),
        'matched': 0,
        'created': 0,
        'skipped': 0,
        'ambiguous': 0,
        'financial_created': 0,
        'financial_updated': 0,
        'scope_created': 0,
        'scope_updated': 0,
        'tech_created': 0,
        'tech_updated': 0,
    }
    
    ambiguous_records = []
    
    # Process each row
    for i, row in enumerate(rows, 1):
        csv_client = row.get('Client', '').strip()
        csv_survey = row.get('Survey', '').strip()
        
        print(f"\n[{i}/{len(rows)}] Processing: Client='{csv_client}', Survey='{csv_survey}'")
        
        process_row(row, all_projects, stats, ambiguous_records)
        
        # Refresh all_projects if we created a new project
        all_projects = list(Project.objects.select_related('client').all())
    
    # Print summary
    print("\n" + "=" * 70)
    print("IMPORT SUMMARY")
    print("=" * 70)
    print(f"Total rows processed:     {stats['total']}")
    print(f"Matched existing:         {stats['matched']}")
    print(f"Created new:              {stats['created']}")
    print(f"Skipped:                  {stats['skipped']}")
    print(f"Ambiguous (reported):     {stats['ambiguous']}")
    print(f"Financial created:        {stats['financial_created']}")
    print(f"Financial updated:        {stats['financial_updated']}")
    print(f"Scope of Work created:    {stats['scope_created']}")
    print(f"Scope of Work updated:    {stats['scope_updated']}")
    print(f"Technology created:       {stats['tech_created']}")
    print(f"Technology updated:       {stats['tech_updated']}")
    print("=" * 70)
    
    # Report ambiguous records
    if ambiguous_records:
        print("\n" + "=" * 70)
        print("AMBIGUOUS RECORDS (Require Manual Review)")
        print("=" * 70)
        for record in ambiguous_records:
            print(f"\nCSV: {record['csv_client']} / {record['csv_survey']}")
            print(f"  Closest DB Match: {record['db_match']}")
            print(f"  Match Score: {record['score']:.2f}")
            print(f"  Reason: {record['reason']}")
        print("=" * 70)
        print("\nPlease review the ambiguous records above and manually")
        print("update or create the appropriate records in the database.")


if __name__ == '__main__':
    main()
