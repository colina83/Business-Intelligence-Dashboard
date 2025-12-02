#!/usr/bin/env python
import os
import sys
from pathlib import Path
from decimal import Decimal

# Ensure project root is on sys.path so "BIApp" can be imported.
# scripts/ is expected at <repo_root>/scripts/
PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

# Django settings module used by this project
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "BIApp.settings")

import django
django.setup()

from market_analysis.models import Project, Financial

PROJECT_NAME = "MH North"
EBIT_PCT = Decimal("18.00")      # 18%
EBIT_DAY = Decimal("116574")     # 116574 (no commas)

def main():
    try:
        project = Project.objects.get(name__iexact=PROJECT_NAME)
    except Project.DoesNotExist:
        print(f"Project not found: '{PROJECT_NAME}'")
        return

    fin, created = Financial.objects.get_or_create(project=project)
    # Use queryset.update to persist these fields exactly (bypasses Financial.save() recalculation)
    updated = Financial.objects.filter(pk=fin.pk).update(ebit_pct=EBIT_PCT, ebit_day=EBIT_DAY)
    if updated:
        print(f"Financial for project '{PROJECT_NAME}' updated: ebit_pct={EBIT_PCT}, ebit_day={EBIT_DAY}")
    else:
        print("No update performed.")

if __name__ == "__main__":
    main()