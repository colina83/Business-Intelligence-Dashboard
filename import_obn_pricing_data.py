#!/usr/bin/env python
"""
Script to import OBN Pricing data from CSV into the database.

This script reads the CSV file 'OBN_Pricing_Bubble_Charts - FC Version - Copilot.csv'
and imports the financial and scope of work data into the database.

For matching records:
- Uses regex/fuzzy matching to find Client and Project records in the database
- Prompts user for confirmation when match is ambiguous

Imported data:
1. Financial table:
   - Total Revenue, Total Direct Costs, GP $, GM%, Total Overhead, Depreciation
   - EBIT $, EBIT %, EBIT Day, Taxes, NET $, NET %, NET/Day

2. Scope of Work table:
   - Bid_Node_Count -> crew_node_count

3. Project Technologies table:
   - Bid_Node_Type -> obn_system
"""

import os
import sys
import re
import csv
from decimal import Decimal, InvalidOperation

# Set up Django environment
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'BIApp.settings')

import django
django.setup()

from market_analysis.models import Client, Project, Financial, ScopeOfWork, ProjectTechnology


def parse_currency(value):
    """Parse currency string to Decimal, handling formats like '$1,234.56' or '($1,234.56)'."""
    if not value or value.strip() in ('', '-', '$-', '$ -   '):
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
        return int(value)
    except (ValueError, TypeError):
        return None


def normalize_name(name):
    """Normalize a name for comparison by removing extra spaces, lowercase, etc."""
    if not name:
        return ''
    # Remove extra whitespace
    name = ' '.join(name.split())
    # Remove common suffixes/prefixes that might differ
    name = name.lower().strip()
    return name


def calculate_similarity(s1, s2):
    """
    Calculate similarity between two strings.
    Returns a score between 0 and 1, where 1 is an exact match.
    """
    s1_norm = normalize_name(s1)
    s2_norm = normalize_name(s2)
    
    if s1_norm == s2_norm:
        return 1.0
    
    # Check if one contains the other
    if s1_norm in s2_norm or s2_norm in s1_norm:
        return 0.8
    
    # Check for partial word matches
    words1 = set(s1_norm.split())
    words2 = set(s2_norm.split())
    
    if words1 and words2:
        common = words1.intersection(words2)
        total = words1.union(words2)
        if common:
            return len(common) / len(total)
    
    return 0.0


def find_matching_project(csv_client, csv_survey, all_projects):
    """
    Find a matching project in the database based on client and survey name.
    
    Returns a tuple of (project, match_score, needs_confirmation).
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
        
        # Combined score (weighted average)
        combined_score = (client_score * 0.4 + project_score * 0.6)
        
        if combined_score > best_score:
            best_score = combined_score
            best_match = project
    
    # Determine if confirmation is needed
    if best_score >= 0.9:
        needs_confirmation = False
    elif best_score >= 0.5:
        needs_confirmation = True
    else:
        needs_confirmation = True
    
    return best_match, best_score, needs_confirmation


def confirm_match(csv_client, csv_survey, db_project):
    """
    Ask user to confirm a match.
    
    Returns True if user confirms, False otherwise.
    """
    db_client_name = db_project.client.name if db_project.client else 'N/A'
    db_project_name = db_project.name
    
    print("\n" + "=" * 70)
    print("MATCH CONFIRMATION REQUIRED")
    print("=" * 70)
    print(f"CSV Data:")
    print(f"  - Client:  {csv_client}")
    print(f"  - Survey:  {csv_survey}")
    print(f"\nDatabase Record:")
    print(f"  - Client:  {db_client_name}")
    print(f"  - Project: {db_project_name}")
    print("=" * 70)
    
    while True:
        response = input("Is this a valid match? (yes/no/skip): ").strip().lower()
        if response in ('yes', 'y'):
            return True
        elif response in ('no', 'n', 'skip', 's'):
            return False
        else:
            print("Please enter 'yes', 'no', or 'skip'")


def get_obn_system_choice(bid_node_type):
    """Map bid node type to OBN_SYSTEM choices."""
    mapping = {
        'ZXPLR': 'ZXPLR',
        'Z700': 'Z700',
        'MASS': 'MASS',
        'GPR300': 'GPR300',
    }
    
    normalized = bid_node_type.strip().upper() if bid_node_type else None
    return mapping.get(normalized, 'OTHER' if normalized else None)


def import_financial_data(project, row):
    """
    Create or update Financial record for a project.
    
    CSV columns to map:
    - Total Revenue -> total_revenue
    - Total Direct Costs -> total_direct_cost
    - GP $ -> gp
    - GM% -> gm
    - Total Overhead -> total_overhead
    - Total Depreciation -> depreciation
    - EBIT$ -> ebit_amount
    - EBIT% -> ebit_pct
    - EBIT$/Day -> ebit_day
    - Taxes -> taxes
    - Net $ -> net_amount
    - Net % -> net_pct
    - Net/Day -> net_day
    - Bid_Duration -> duration_raw and duration_with_dt
    """
    financial, created = Financial.objects.get_or_create(project=project)
    
    # Parse and set values - don't use the model's auto-calculation
    # since we're importing pre-calculated values
    financial.total_revenue = parse_currency(row.get('Total Revenue'))
    financial.total_direct_cost = parse_currency(row.get('Total Direct Costs'))
    financial.gp = parse_currency(row.get('GP $'))
    financial.gm = parse_percentage(row.get('GM%'))
    financial.total_overhead = parse_currency(row.get('Total Overhead'))
    financial.depreciation = parse_currency(row.get('Total Depreciation'))
    financial.ebit_amount = parse_currency(row.get('EBIT$'))
    financial.ebit_pct = parse_percentage(row.get('EBIT%'))
    financial.ebit_day = parse_currency(row.get('EBIT$/Day'))
    financial.taxes = parse_currency(row.get('Taxes'))
    financial.net_amount = parse_currency(row.get('Net $'))
    financial.net_pct = parse_percentage(row.get('Net %'))
    financial.net_day = parse_currency(row.get('Net/Day'))
    
    # Duration
    duration = parse_integer(row.get('Bid_Duration'))
    if duration:
        financial.duration_raw = duration
        financial.duration_with_dt = duration
    
    # Use update to bypass the auto-calculation in save()
    Financial.objects.filter(pk=financial.pk).update(
        total_revenue=financial.total_revenue,
        total_direct_cost=financial.total_direct_cost,
        gp=financial.gp,
        gm=financial.gm,
        total_overhead=financial.total_overhead,
        depreciation=financial.depreciation,
        ebit_amount=financial.ebit_amount,
        ebit_pct=financial.ebit_pct,
        ebit_day=financial.ebit_day,
        taxes=financial.taxes,
        net_amount=financial.net_amount,
        net_pct=financial.net_pct,
        net_day=financial.net_day,
        duration_raw=financial.duration_raw if duration else None,
        duration_with_dt=financial.duration_with_dt if duration else None,
    )
    
    return financial, created


def import_scope_of_work(project, row):
    """
    Create ScopeOfWork record for a project.
    
    CSV columns to map:
    - Bid_Node_Count -> crew_node_count
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


def import_project_technology(project, row):
    """
    Create or update ProjectTechnology record with OBN system.
    
    CSV columns to map:
    - Bid_Node_Type -> obn_system
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
            survey_type='3D Seismic',  # Default value
            obn_system=obn_system
        )
        return tech, True


def main():
    """Main function to import OBN pricing data from CSV."""
    csv_file = os.path.join(
        os.path.dirname(os.path.abspath(__file__)),
        'OBN_Pricing_Bubble_Charts - FC Version - Copilot.csv'
    )
    
    if not os.path.exists(csv_file):
        print(f"Error: CSV file not found: {csv_file}")
        sys.exit(1)
    
    # Load all projects from database
    all_projects = list(Project.objects.select_related('client').all())
    
    if not all_projects:
        print("Warning: No projects found in database.")
        print("Please ensure the database has been populated with projects first.")
        print("\nTo populate the database, you may need to run the import script")
        print("for 'All_OBN_Bid_Analytics - Copilot.csv' first.")
        sys.exit(1)
    
    print(f"Found {len(all_projects)} projects in database.")
    print(f"Reading CSV file: {csv_file}")
    
    # Read CSV file
    with open(csv_file, 'r', encoding='utf-8-sig') as f:
        reader = csv.DictReader(f)
        rows = list(reader)
    
    print(f"Found {len(rows)} rows in CSV file.\n")
    
    # Statistics
    stats = {
        'total': len(rows),
        'matched': 0,
        'confirmed': 0,
        'skipped': 0,
        'no_match': 0,
        'financial_created': 0,
        'financial_updated': 0,
        'scope_created': 0,
        'scope_updated': 0,
        'tech_created': 0,
        'tech_updated': 0,
    }
    
    for i, row in enumerate(rows, 1):
        csv_client = row.get('Client', '').strip()
        csv_survey = row.get('Survey', '').strip()
        
        print(f"\n[{i}/{len(rows)}] Processing: Client='{csv_client}', Survey='{csv_survey}'")
        
        # Find matching project
        match, score, needs_confirmation = find_matching_project(
            csv_client, csv_survey, all_projects
        )
        
        if match is None or score < 0.3:
            print(f"  -> No matching project found (best score: {score:.2f})")
            stats['no_match'] += 1
            continue
        
        stats['matched'] += 1
        
        db_client_name = match.client.name if match.client else 'N/A'
        print(f"  -> Found match: Client='{db_client_name}', Project='{match.name}' (score: {score:.2f})")
        
        # Confirm if needed
        if needs_confirmation:
            if not confirm_match(csv_client, csv_survey, match):
                print("  -> Skipped by user")
                stats['skipped'] += 1
                continue
            stats['confirmed'] += 1
        
        # Import data
        try:
            # Financial data
            financial, fin_created = import_financial_data(match, row)
            if fin_created:
                stats['financial_created'] += 1
                print("  -> Created Financial record")
            else:
                stats['financial_updated'] += 1
                print("  -> Updated Financial record")
            
            # Scope of Work
            scope, scope_created = import_scope_of_work(match, row)
            if scope:
                if scope_created:
                    stats['scope_created'] += 1
                    print("  -> Created Scope of Work record")
                else:
                    stats['scope_updated'] += 1
                    print("  -> Updated Scope of Work record")
            
            # Project Technology
            tech, tech_created = import_project_technology(match, row)
            if tech:
                if tech_created:
                    stats['tech_created'] += 1
                    print("  -> Created Project Technology record")
                else:
                    stats['tech_updated'] += 1
                    print("  -> Updated Project Technology record")
        
        except Exception as e:
            print(f"  -> Error importing data: {e}")
    
    # Print summary
    print("\n" + "=" * 70)
    print("IMPORT SUMMARY")
    print("=" * 70)
    print(f"Total rows processed:     {stats['total']}")
    print(f"Matched:                  {stats['matched']}")
    print(f"User confirmed:           {stats['confirmed']}")
    print(f"Skipped by user:          {stats['skipped']}")
    print(f"No match found:           {stats['no_match']}")
    print(f"Financial created:        {stats['financial_created']}")
    print(f"Financial updated:        {stats['financial_updated']}")
    print(f"Scope of Work created:    {stats['scope_created']}")
    print(f"Scope of Work updated:    {stats['scope_updated']}")
    print(f"Technology created:       {stats['tech_created']}")
    print(f"Technology updated:       {stats['tech_updated']}")
    print("=" * 70)


if __name__ == '__main__':
    main()
