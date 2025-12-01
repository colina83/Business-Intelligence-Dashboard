#!/usr/bin/env python
"""
Management command to read OBN CSV and update projects:

For each CSV row:
 - find closest Project by Client + Survey
 - if project is not Submitted -> set status 'Submitted' and set submission_date from 'Bid-Submitted Date'
 - if project is not Lost -> set status 'Lost' and create a Competitor using 'winner' column (if it maps to known choices)
   (if already Lost, competitor step is skipped)
 - update Financial values (uses logic/field mapping provided)
 - update or create ProjectTechnology (OBN system) from 'Bid_Node_Type'
 - update ScopeOfWork.crew_node_count with 'Bid_Node_Count' if present

Usage:
    python manage.py diagnose_obn_import path/to/file.csv [--dry-run]

Notes:
 - This command is conservative: supports --dry-run to preview changes without writing.
 - It attempts best-effort mapping for competitor names to COMPETITOR_CHOICES.
"""
from __future__ import annotations
import os
import csv
import re
import sys
from decimal import Decimal, InvalidOperation
from datetime import datetime
from typing import Optional, Tuple

from django.core.management.base import BaseCommand
from django.db import transaction

from market_analysis.models import (
    Project, Client, Financial, ScopeOfWork, ProjectTechnology, Competitor
)

DEFAULT_CSV = 'OBN_Pricing_Bubble_Charts - FC Version - Copilot.csv'


def parse_currency(value: Optional[str]) -> Optional[Decimal]:
    if not value:
        return None
    s = value.strip()
    if s in ('', '-', 'NA', 'N/A'):
        return None
    # parentheses indicate negative
    is_neg = s.startswith('(') and s.endswith(')')
    if is_neg:
        s = s[1:-1]
    s = re.sub(r'[$,]', '', s).strip()
    if s == '':
        return None
    try:
        d = Decimal(s)
        return -d if is_neg else d
    except (InvalidOperation, ValueError):
        return None


def parse_percentage(value: Optional[str]) -> Optional[Decimal]:
    if not value:
        return None
    s = value.strip()
    if s in ('', '-', 'NA', 'N/A'):
        return None
    is_neg = s.startswith('(') and s.endswith(')')
    if is_neg:
        s = s[1:-1]
    s = s.rstrip('%').strip()
    if s.startswith('-'):
        neg = True
        s = s.lstrip('-').strip()
    else:
        neg = False
    try:
        d = Decimal(s)
        return -d if (is_neg or neg) else d
    except (InvalidOperation, ValueError):
        return None


def parse_integer(value: Optional[str]) -> Optional[int]:
    if not value:
        return None
    s = value.strip()
    if s in ('', '-', 'NA', 'N/A'):
        return None
    s = re.sub(r'[,]', '', s)
    try:
        return int(s)
    except (ValueError, TypeError):
        return None


def parse_date(value: Optional[str]) -> Optional[datetime.date]:
    if not value:
        return None
    s = value.strip()
    if s in ('', '-', 'NA', 'N/A'):
        return None
    # try multiple common formats
    fmts = ['%Y-%m-%d', '%d/%m/%Y', '%m/%d/%Y', '%d-%m-%Y', '%Y/%m/%d']
    for f in fmts:
        try:
            return datetime.strptime(s, f).date()
        except Exception:
            continue
    # fallback: try ISO parse
    try:
        return datetime.fromisoformat(s).date()
    except Exception:
        return None


def normalize_name(name: Optional[str]) -> str:
    if not name:
        return ''
    return ' '.join(name.split()).lower()


def calculate_similarity(s1: Optional[str], s2: Optional[str]) -> float:
    a = normalize_name(s1)
    b = normalize_name(s2)
    if not a or not b:
        return 0.0
    if a == b:
        return 1.0
    if a in b or b in a:
        return 0.8
    wa = set(a.split())
    wb = set(b.split())
    if wa and wb:
        common = wa.intersection(wb)
        total = wa.union(wb)
        return len(common) / len(total)
    return 0.0


def find_best_project(csv_client: str, csv_survey: str, projects) -> Tuple[Optional[Project], float]:
    best = None
    best_score = 0.0
    for p in projects:
        db_client = p.client.name if p.client else ''
        db_name = p.name or ''
        client_score = calculate_similarity(csv_client, db_client)
        proj_score = calculate_similarity(csv_survey, db_name)
        score = client_score * 0.4 + proj_score * 0.6
        if score > best_score:
            best_score = score
            best = p
    return best, best_score


def map_competitor_choice(winner_raw: Optional[str]) -> Optional[str]:
    """
    Try to map a free-text winner to Competitor.COMPONENT_CHOICES code.
    Returns the choice code if found, otherwise None.
    """
    if not winner_raw:
        return None
    w = winner_raw.strip().upper()
    # Try exact code match
    for code, label in Competitor.COMPETITOR_CHOICES:
        if w == code or w == label.upper():
            return code
    # fuzzy: check if label substring
    for code, label in Competitor.COMPETITOR_CHOICES:
        if label.upper() in w or w in label.upper():
            return code
    return None


def map_obn_system(bid_node_type: Optional[str]) -> Optional[str]:
    if not bid_node_type:
        return None
    m = bid_node_type.strip().upper()
    mapping = {'ZXPLR': 'ZXPLR', 'Z700': 'Z700', 'MASS': 'MASS', 'GPR300': 'GPR300'}
    return mapping.get(m, 'OTHER')


class Command(BaseCommand):
    help = "Diagnose & apply OBN CSV updates: set statuses, competitor, financials, technology, scope."

    def add_arguments(self, parser):
        parser.add_argument(
            "csv_file",
            nargs="?",
            help="CSV file path (default cwd or project-specific filename).",
            default=None
        )
        parser.add_argument(
            "--dry-run",
            action="store_true",
            help="Show planned changes without writing to DB."
        )
        parser.add_argument(
            "--threshold",
            type=float,
            default=0.3,
            help="Matching threshold (0-1) to consider a DB project a match (default 0.3)."
        )

    def handle(self, *args, **options):
        csv_file = options["csv_file"] or os.path.join(os.getcwd(), DEFAULT_CSV)
        dry_run = options["dry_run"]
        threshold = float(options["threshold"])

        if not os.path.exists(csv_file):
            self.stderr.write(f"CSV file not found: {csv_file}")
            sys.exit(1)

        # Load projects once
        all_projects = list(Project.objects.select_related("client").all())
        if not all_projects:
            self.stdout.write("No projects in DB to match against. Aborting.")
            return

        with open(csv_file, newline='', encoding='utf-8-sig') as fh:
            reader = csv.DictReader(fh)
            rows = list(reader)

        self.stdout.write(f"CSV rows: {len(rows)}  Projects in DB: {len(all_projects)}")
        stats = {
            'total': len(rows),
            'matched': 0,
            'no_match': 0,
            'updated_projects': 0,
            'created_competitors': 0,
            'financial_updates': 0,
            'tech_updates': 0,
            'scope_updates': 0,
        }

        for idx, row in enumerate(rows, start=1):
            csv_client = (row.get('Client') or '').strip()
            csv_survey = (row.get('Survey') or '').strip()
            self.stdout.write(f"\n[{idx}/{len(rows)}] CSV Client='{csv_client}' Survey='{csv_survey}'")

            best, score = find_best_project(csv_client, csv_survey, all_projects)
            if not best or score < threshold:
                self.stdout.write(f"  -> No match (best score {score:.2f}). Skipping.")
                stats['no_match'] += 1
                continue

            stats['matched'] += 1
            project = best
            self.stdout.write(f"  -> Matched ProjectID={project.project_id} '{project.name}' (score {score:.2f})")

            # Parse fields from CSV
            bid_submitted_date = parse_date(row.get('Bid-Submitted Date') or row.get('Submission Date') or row.get('Bid Submitted Date'))
            winner = (row.get('winner') or row.get('Winner') or row.get('winner_name') or '').strip()
            total_direct = parse_currency(row.get('Total Direct Cost') or row.get('Total Direct Costs'))
            total_revenue = parse_currency(row.get('Total Revenue'))
            gp_from_csv = parse_currency(row.get('GP $'))
            gm = parse_percentage(row.get('GM%'))
            total_overhead = parse_currency(row.get('Total Overhead'))
            depreciation = parse_currency(row.get('Total Depreciation'))
            ebit_amount = parse_currency(row.get('EBIT$'))
            ebit_pct = parse_percentage(row.get('EBIT%'))
            ebit_day = parse_currency(row.get('EBIT$/Day'))
            taxes = parse_currency(row.get('Taxes'))
            net_amount = parse_currency(row.get('Net $'))
            net_pct = parse_percentage(row.get('Net %'))
            net_day = parse_currency(row.get('Net/Day'))
            duration = parse_integer(row.get('Bid_Duration') or row.get('Bid Duration') or row.get('Duration'))
            bid_node_type = (row.get('Bid_Node_Type') or row.get('Bid Node Type') or '').strip()
            bid_node_count = parse_integer(row.get('Bid_Node_Count') or row.get('Bid Node Count'))

            # Compute gp_value if missing
            gp_value = gp_from_csv if gp_from_csv is not None else (total_revenue - total_direct if (total_revenue is not None and total_direct is not None) else None)

            # Prepare financial update mapping
            update_fields = {
                'total_direct_cost': total_direct,
                'total_revenue': total_revenue,
                'gp': gp_value,
                'gm': gm,
                'total_overhead': total_overhead,
                'depreciation': depreciation,
                'ebit_amount': ebit_amount,
                'ebit_pct': ebit_pct,
                'ebit_day': ebit_day,
                'taxes': taxes,
                'net_amount': net_amount,
                'net_pct': net_pct,
                'net_day': net_day,
                'duration_raw': duration,
                'duration_with_dt': duration,
            }

            planned_changes = []

            # Begin DB changes (optionally dry-run)
            if dry_run:
                self.stdout.write("  [DRY-RUN] Planned changes:")
            try:
                with transaction.atomic():
                    # 1) Ensure Submitted status and submission_date
                    if project.status != 'Submitted':
                        if not dry_run:
                            project.submission_date = bid_submitted_date or project.submission_date
                            project.status = 'Submitted'
                            project.save()
                        planned_changes.append(f"status -> Submitted (submission_date={bid_submitted_date})")
                    else:
                        # even if already submitted, accept updated submission date if provided
                        if bid_submitted_date and project.submission_date != bid_submitted_date:
                            if not dry_run:
                                project.submission_date = bid_submitted_date
                                project.save(update_fields=['submission_date'])
                            planned_changes.append(f"submission_date updated -> {bid_submitted_date}")

                    # 2) If not lost, set to Lost and add competitor (winner)
                    if project.status != 'Lost':
                        # map winner to allowed choice code
                        comp_code = map_competitor_choice(winner)
                        if comp_code:
                            if not dry_run:
                                project.status = 'Lost'
                                # Project.save() will set lost_date if transition from Submitted -> Lost if logic applies,
                                # but we can set lost_date explicitly if provided in CSV
                                # Use 'Bid-Lost Date' or 'Lost Date' if present
                                lost_date = parse_date(row.get('Bid-Lost Date') or row.get('Lost Date'))
                                if lost_date:
                                    project.lost_date = lost_date
                                project.save()
                                # create competitor record (use code)
                                try:
                                    Competitor.objects.create(project=project, name=comp_code, created_by=None)
                                except Exception:
                                    # ignore duplicates/constraints
                                    pass
                            planned_changes.append(f"status -> Lost, competitor -> {comp_code}")
                        else:
                            # If we cannot map winner to a allowed choice, skip creating competitor but still set Lost
                            if not dry_run:
                                lost_date = parse_date(row.get('Bid-Lost Date') or row.get('Lost Date'))
                                if lost_date:
                                    project.lost_date = lost_date
                                project.status = 'Lost'
                                project.save()
                            planned_changes.append(f"status -> Lost (winner '{winner}' not mapped to choice; competitor skipped)")

                    else:
                        # already lost; skip competitor creation
                        planned_changes.append("already Lost; competitor skipped")

                    # 3) Financial: update or create Financial row using update_fields (only set keys with non-None values)
                    # Remove keys with None to avoid overriding with NULL unintentionally
                    financial_updates = {k: v for k, v in update_fields.items() if v is not None}
                    if financial_updates:
                        if not dry_run:
                            fin, created = Financial.objects.update_or_create(project=project, defaults=financial_updates)
                            # If some numeric values were provided but Financial.save recalculated derived fields,
                            # we're intentionally using update_or_create to set provided values.
                        planned_changes.append(f"Financial updated keys: {', '.join(financial_updates.keys())}")
                        stats['financial_updates'] += 1

                    # 4) Technology: find or create ProjectTechnology and update obn_system from Bid_Node_Type
                    obn_system = map_obn_system(bid_node_type)
                    if obn_system:
                        if not dry_run:
                            tech = ProjectTechnology.objects.filter(project=project).first()
                            if tech:
                                tech.obn_system = obn_system
                                tech.save(update_fields=['obn_system'])
                                stats['tech_updates'] += 1
                                planned_changes.append(f"Technology obn_system updated -> {obn_system}")
                            else:
                                ProjectTechnology.objects.create(
                                    project=project,
                                    technology='OBN',
                                    survey_type='3D Seismic',
                                    obn_system=obn_system
                                )
                                stats['tech_updates'] += 1
                                planned_changes.append(f"Technology created obn_system -> {obn_system}")
                        else:
                            planned_changes.append(f"Technology obn_system would be set -> {obn_system}")

                    # 5) ScopeOfWork: set crew_node_count from Bid_Node_Count if present
                    if bid_node_count is not None:
                        if not dry_run:
                            sow, created = ScopeOfWork.objects.update_or_create(project=project, defaults={'crew_node_count': bid_node_count})
                            stats['scope_updates'] += 1
                        planned_changes.append(f"Scope crew_node_count -> {bid_node_count}")

                    # Mark update counted
                    stats['updated_projects'] += 1

            except Exception as exc:
                self.stderr.write(f"  ERROR applying updates for ProjectID={project.project_id}: {exc}")
                # continue to next row

            # Reporting planned changes
            if planned_changes:
                if dry_run:
                    for c in planned_changes:
                        self.stdout.write(f"  [DRY] {c}")
                else:
                    for c in planned_changes:
                        self.stdout.write(f"  {c}")
            else:
                self.stdout.write("  No changes planned/applied for this project.")

        # Summary
        self.stdout.write("\n=== SUMMARY ===")
        self.stdout.write(f"Rows processed: {stats['total']}")
        self.stdout.write(f"Matched rows: {stats['matched']}")
        self.stdout.write(f"No match rows: {stats['no_match']}")
        self.stdout.write(f"Projects updated: {stats['updated_projects']}")
        self.stdout.write(f"Financial updates: {stats['financial_updates']}")
        self.stdout.write(f"Technology updates: {stats['tech_updates']}")
        self.stdout.write(f"Scope updates: {stats['scope_updates']}")
        self.stdout.write("Done.")