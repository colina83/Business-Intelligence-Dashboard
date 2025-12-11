"""
Management command to populate test data for pricing graphs visualization.
"""
from django.core.management.base import BaseCommand
from django.utils import timezone
from datetime import date, timedelta
from decimal import Decimal
from market_analysis.models import Client, Project, ProjectTechnology, Financial


class Command(BaseCommand):
    help = 'Populate test data for pricing graphs visualization'

    def handle(self, *args, **options):
        self.stdout.write('Creating test data for pricing graphs...')
        
        # Create or get test clients
        client1, _ = Client.objects.get_or_create(name='Shell')
        client2, _ = Client.objects.get_or_create(name='BP')
        client3, _ = Client.objects.get_or_create(name='Exxon')
        client4, _ = Client.objects.get_or_create(name='TGS')
        
        # Create test projects with different statuses and dates
        test_projects = [
            {
                'client': client1,
                'name': 'Whale Project',
                'status': 'Won',
                'submission_date': date(2020, 6, 15),
                'award_date': date(2020, 8, 1),
                'ebit_day': 117485,
                'ebit_pct': 21.00,
            },
            {
                'client': client2,
                'name': 'Atlantis Survey',
                'status': 'Lost',
                'submission_date': date(2021, 3, 10),
                'lost_date': date(2021, 5, 15),
                'ebit_day': -32665,
                'ebit_pct': -21.00,
            },
            {
                'client': client3,
                'name': 'Guyana OBN',
                'status': 'Won',
                'submission_date': date(2021, 9, 20),
                'award_date': date(2021, 11, 5),
                'ebit_day': 38654,
                'ebit_pct': 10.00,
            },
            {
                'client': client4,
                'name': 'NOAKA Project',
                'status': 'Won',
                'submission_date': date(2022, 2, 14),
                'award_date': date(2022, 4, 1),
                'ebit_day': 31660,
                'ebit_pct': 10.00,
            },
            {
                'client': client1,
                'name': 'Mars Ursa',
                'status': 'Won',
                'submission_date': date(2022, 7, 5),
                'award_date': date(2022, 9, 1),
                'ebit_day': 61181,
                'ebit_pct': 12.00,
            },
            {
                'client': client2,
                'name': 'Mad Dog',
                'status': 'Won',
                'submission_date': date(2022, 11, 15),
                'award_date': date(2023, 1, 10),
                'ebit_day': 4835,
                'ebit_pct': 2.00,
            },
            {
                'client': client3,
                'name': 'Liza Project',
                'status': 'Cancelled',
                'submission_date': date(2023, 2, 28),
                'ebit_day': 15000,
                'ebit_pct': 5.00,
            },
            {
                'client': client4,
                'name': 'A2 Multi-Client',
                'status': 'Won',
                'submission_date': date(2023, 6, 10),
                'award_date': date(2023, 8, 1),
                'ebit_day': 62966,
                'ebit_pct': 16.00,
            },
            {
                'client': client1,
                'name': 'Leopard Survey',
                'status': 'Lost',
                'submission_date': date(2023, 10, 5),
                'lost_date': date(2023, 12, 1),
                'ebit_day': 18368,
                'ebit_pct': 8.00,
            },
            {
                'client': client2,
                'name': 'Thunderhorse',
                'status': 'Won',
                'submission_date': date(2024, 1, 20),
                'award_date': date(2024, 3, 15),
                'ebit_day': 45000,
                'ebit_pct': 13.50,
            },
        ]
        
        projects_created = 0
        
        for proj_data in test_projects:
            # Create project
            project, created = Project.objects.get_or_create(
                name=proj_data['name'],
                defaults={
                    'client': proj_data['client'],
                    'bid_type': 'RFP',
                    'country': 'US',
                    'region': 'NSA',
                    'status': proj_data['status'],
                    'date_received': proj_data['submission_date'] - timedelta(days=45),
                    'submission_date': proj_data['submission_date'],
                    'award_date': proj_data.get('award_date'),
                    'lost_date': proj_data.get('lost_date'),
                }
            )
            
            if created:
                projects_created += 1
                
                # Add OBN technology
                ProjectTechnology.objects.get_or_create(
                    project=project,
                    defaults={
                        'technology': 'OBN',
                        'survey_type': '3D Seismic',
                        'obn_technique': 'ROV',
                        'obn_system': 'ZXPLR',
                    }
                )
                
                # Add financial data
                # Calculate total direct cost based on EBIT$/day
                # Assuming duration of 60 days for simplicity
                duration = Decimal('60.00')
                ebit_day = Decimal(str(proj_data['ebit_day']))
                ebit_pct = Decimal(str(proj_data['ebit_pct']))
                
                # EBIT$ = EBIT$/Day * Duration
                ebit_amount = ebit_day * duration
                
                # For simplicity, calculate revenue from EBIT% and EBIT$
                # EBIT% = (EBIT$ / Revenue) * 100
                # Revenue = EBIT$ / (EBIT% / 100)
                if ebit_pct != 0:
                    total_revenue = ebit_amount / (ebit_pct / Decimal('100'))
                else:
                    total_revenue = Decimal('1000000')  # Default for zero EBIT%
                
                # GM = 25% for simplicity
                gm = Decimal('25.00')
                
                # Cost = Revenue * (1 - GM/100)
                total_direct_cost = total_revenue * (Decimal('1') - gm / Decimal('100'))
                
                Financial.objects.get_or_create(
                    project=project,
                    defaults={
                        'total_direct_cost': total_direct_cost,
                        'gm': gm,
                        'duration_with_dt': duration,
                        'depreciation': Decimal('50000'),  # Fixed for simplicity
                    }
                )
                
                self.stdout.write(
                    self.style.SUCCESS(f'  ✓ Created project: {project.name} ({project.status})')
                )
        
        self.stdout.write(
            self.style.SUCCESS(
                f'\nSuccessfully created {projects_created} projects with financial and technology data!'
            )
        )
        
        # Display summary
        total_projects = Project.objects.filter(technologies__technology='OBN').distinct().count()
        total_with_financial = Project.objects.filter(
            technologies__technology='OBN',
            financials__isnull=False,
            submission_date__isnull=False
        ).distinct().count()
        
        self.stdout.write(
            self.style.SUCCESS(
                f'\nSummary:'
                f'\n  Total OBN projects: {total_projects}'
                f'\n  OBN projects with financial data and submission date: {total_with_financial}'
            )
        )
