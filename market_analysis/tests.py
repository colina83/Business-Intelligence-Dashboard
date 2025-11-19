from decimal import Decimal
import datetime
import os
import shutil

from django.test import TestCase
from django.utils import timezone
from django.core.management import call_command
from django.contrib import admin

from .models import (
    Client, Project, Financial,
    BidTypeHistory, ProjectStatusHistory, ChangeLog, ProjectContract
)
from . import admin as ma_admin

DECIMAL_2 = Decimal("0.01")


class ComprehensiveModelsTest(TestCase):
    def setUp(self):
        self.client = Client.objects.create(name="ACME Corporation")

    def test_internal_id_generation_and_sanitization(self):
        # normal case
        d = datetime.date(2025, 1, 15)
        p = Project.objects.create(
            name="Alpha Project",
            client=self.client,
            date_received=d,
            country="US",
            bid_type="BQ",
        )
        self.assertIsNotNone(p.internal_id)
        self.assertTrue(p.internal_id.startswith(d.strftime("%Y%m")))
        self.assertIn("BQ", p.internal_id)

        # missing client -> still generates
        q = Project.objects.create(
            name="NoClient",
            date_received=d,
            country="GB",
            bid_type="RFQ",
        )
        self.assertIsNotNone(q.internal_id)
        self.assertIn("RFQ", q.internal_id)

        # name with unsafe chars -> sanitized
        r = Project.objects.create(
            name="Proj #1 @Test!",
            client=self.client,
            date_received=d,
            country="FR",
            bid_type="RFP",
        )
        self.assertIsNotNone(r.internal_id)
        # sanitized parts contain only alnum and hyphens
        for part in r.internal_id.split("-"):
            self.assertTrue(part.isalnum())

    def test_bid_type_transition_creates_history_and_changelog(self):
        p = Project.objects.create(
            name="Beta Project",
            client=self.client,
            date_received=timezone.now().date(),
            country="US",
            bid_type="BQ",
        )
        # change bid_type
        p.bid_type = "RFQ"
        p.save()

        # BidTypeHistory entry exists
        self.assertTrue(BidTypeHistory.objects.filter(project=p, new_bid_type="RFQ").exists())

        # ChangeLog entry exists for BID
        self.assertTrue(ChangeLog.objects.filter(project=p, change_type="BID", new_value="RFQ").exists())

    def test_status_transitions_create_dates_histories_and_contract_and_admin_inlines(self):
        p = Project.objects.create(
            name="Gamma Project",
            client=self.client,
            date_received=timezone.now().date(),
            country="US",
            bid_type="BQ",
        )

        # Move to Submitted
        p.status = "Submitted"
        p.save()
        p.refresh_from_db()
        self.assertIsNotNone(p.submission_date)
        self.assertTrue(ProjectStatusHistory.objects.filter(project=p, new_status="Submitted").exists())
        self.assertTrue(ChangeLog.objects.filter(project=p, change_type="STATUS", new_value="Submitted").exists())

        # Move to Won (should set award_date and create contract)
        p.status = "Won"
        p.save()
        p.refresh_from_db()
        self.assertIsNotNone(p.award_date)
        self.assertTrue(ProjectStatusHistory.objects.filter(project=p, new_status="Won").exists())
        self.assertTrue(ChangeLog.objects.filter(project=p, change_type="STATUS", new_value="Won").exists())

        # Contract object should be auto-created
        self.assertTrue(ProjectContract.objects.filter(project=p).exists())
        contract = ProjectContract.objects.get(project=p)
        self.assertIsNone(contract.contract_date)

        # Admin: ProjectAdmin.get_inline_instances should include ProjectContractInline only when status == 'Won'
        ProjectAdminClass = ma_admin.ProjectAdmin
        admin_instance = ProjectAdminClass(Project, admin.site)
        # for non-Won object
        non_won = Project.objects.create(
            name="NonWon",
            client=self.client,
            date_received=timezone.now().date(),
            country="US",
            bid_type="BQ",
        )
        non_won_inlines = admin_instance.get_inline_instances(request=None, obj=non_won)
        inline_models_non_won = [inline.opts.model for inline in non_won_inlines]
        self.assertNotIn(ProjectContract, inline_models_non_won)

        # for Won object
        won_inlines = admin_instance.get_inline_instances(request=None, obj=p)
        inline_models_won = [inline.opts.model for inline in won_inlines]
        self.assertIn(ProjectContract, inline_models_won)

        # Submitted -> Lost
        q = Project.objects.create(
            name="Delta Project",
            client=self.client,
            date_received=timezone.now().date(),
            country="US",
            bid_type="BQ",
        )
        q.status = "Submitted"
        q.save()
        q.status = "Lost"
        q.save()
        q.refresh_from_db()
        self.assertIsNotNone(q.lost_date)
        self.assertTrue(ProjectStatusHistory.objects.filter(project=q, new_status="Lost").exists())
        self.assertTrue(ChangeLog.objects.filter(project=q, change_type="STATUS", new_value="Lost").exists())

    def test_financial_calculation_regular_and_edge_cases(self):
        p = Project.objects.create(
            name="FinProject",
            client=self.client,
            date_received=timezone.now().date(),
            country="US",
            bid_type="BQ",
        )

        cost = Decimal("100000.00")
        gm = Decimal("20.00")  # percent
        duration = 10
        depreciation = Decimal("5000.00")
        taxes = Decimal("2000.00")

        # primary financial record
        f = Financial.objects.create(
            project=p,
            total_direct_cost=cost,
            gm=gm,
            duration_with_dt=duration,
            depreciation=depreciation,
            taxes=taxes,
        )
        f.refresh_from_db()

        gm_frac = gm / Decimal("100")
        total_revenue = (cost / (Decimal("1") - gm_frac)).quantize(DECIMAL_2)
        gp = (total_revenue - cost).quantize(DECIMAL_2)
        total_overhead = (Decimal("21000.00") * Decimal(duration)).quantize(DECIMAL_2)
        ebitda_amount = (gp - total_overhead).quantize(DECIMAL_2)
        ebitda_pct = ((ebitda_amount / total_revenue) * Decimal("100")).quantize(DECIMAL_2)
        ebit_amount = (ebitda_amount - depreciation).quantize(DECIMAL_2)
        ebit_pct = ((ebit_amount / total_revenue) * Decimal("100")).quantize(DECIMAL_2)
        net_amount = (ebit_amount - taxes).quantize(DECIMAL_2)
        net_pct = ((net_amount / total_revenue) * Decimal("100")).quantize(DECIMAL_2)
        ebit_day = (ebit_amount / Decimal(duration)).quantize(DECIMAL_2)
        net_day = (net_amount / Decimal(duration)).quantize(DECIMAL_2)

        self.assertEqual(f.total_revenue, total_revenue)
        self.assertEqual(f.gp, gp)
        self.assertEqual(f.total_overhead, total_overhead)
        self.assertEqual(f.ebitda_amount, ebitda_amount)
        self.assertEqual(f.ebitda_pct, ebitda_pct)
        self.assertEqual(f.ebit_amount, ebit_amount)
        self.assertEqual(f.ebit_pct, ebit_pct)
        self.assertEqual(f.net_amount, net_amount)
        self.assertEqual(f.net_pct, net_pct)
        self.assertEqual(f.ebit_day, ebit_day)
        self.assertEqual(f.net_day, net_day)

        # Edge: gm == 100% -> divide by zero handled => total_revenue should be None
        p2 = Project.objects.create(
            name="FinProject2",
            client=self.client,
            date_received=timezone.now().date(),
            country="US",
            bid_type="BQ",
        )
        f2 = Financial.objects.create(
            project=p2,
            total_direct_cost=cost,
            gm=Decimal("100.00"),
            duration_with_dt=duration,
        )
        f2.refresh_from_db()
        self.assertIsNone(f2.total_revenue)

        # Edge: duration 0 -> ebit_day/net_day None
        p3 = Project.objects.create(
            name="FinProject3",
            client=self.client,
            date_received=timezone.now().date(),
            country="US",
            bid_type="BQ",
        )
        f3 = Financial.objects.create(
            project=p3,
            total_direct_cost=cost,
            gm=Decimal("10.00"),
            duration_with_dt=0,
        )
        f3.refresh_from_db()
        self.assertIsNone(f3.ebit_day)
        self.assertIsNone(f3.net_day)

        # Edge: missing cost or gm -> derived fields None
        p4 = Project.objects.create(
            name="FinProject4",
            client=self.client,
            date_received=timezone.now().date(),
            country="US",
            bid_type="BQ",
        )
        f4 = Financial.objects.create(project=p4)
        f4.refresh_from_db()
        self.assertIsNone(f4.total_revenue)
        self.assertIsNone(f4.gp)

    def test_backfill_command_creates_changelog_entries(self):
        # create project and histories with explicit timestamps
        p = Project.objects.create(
            name="BackfillProject",
            client=self.client,
            date_received=timezone.now().date(),
            country="US",
            bid_type="BQ",
        )

        bt = BidTypeHistory.objects.create(project=p, previous_bid_type="BQ", new_bid_type="RFQ", notes="note1")
        sh = ProjectStatusHistory.objects.create(project=p, previous_status="Ongoing", new_status="Submitted", notes="note2")

        # run backfill command
        call_command('backfill_changelog')

        # expect ChangeLog entries for both
        self.assertTrue(ChangeLog.objects.filter(project=p, change_type="BID", previous_value="BQ", new_value="RFQ").exists())
        self.assertTrue(ChangeLog.objects.filter(project=p, change_type="STATUS", previous_value="Ongoing", new_value="Submitted").exists())

      