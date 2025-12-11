import os
import django
from datetime import datetime, timedelta

os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'BIApp.settings')
django.setup()

from market_analysis.models import Client, Project, ProjectContract, ProjectTechnology

# Create clients
clients = []
for i in range(3):
    client, created = Client.objects.get_or_create(name=f'Test Client {i+1}')
    clients.append(client)
    if created:
        print(f'Created client: {client.name}')

# Create projects
base_date = datetime(2023, 1, 1)
for i in range(10):
    date_received = base_date + timedelta(days=i*30)
    submission_date = date_received + timedelta(days=20)
    award_date = submission_date + timedelta(days=40)
    contract_date = award_date + timedelta(days=15)
    start_date = contract_date + timedelta(days=30)
    
    project, created = Project.objects.get_or_create(
        internal_id=f'TEST-{i+1:03d}',
        defaults={
            'name': f'Test Project {i+1}',
            'client': clients[i % len(clients)],
            'status': 'Won',
            'date_received': date_received,
            'submission_date': submission_date,
            'award_date': award_date,
            'region': 'Europe',
        }
    )
    
    if created:
        print(f'Created project: {project.name}')
        
        # Create contract
        ProjectContract.objects.get_or_create(
            project=project,
            defaults={
                'contract_date': contract_date,
                'actual_start': start_date,
                'contract_value': 100000 + i*10000,
            }
        )
        
        # Add OBN technology
        ProjectTechnology.objects.get_or_create(
            project=project,
            defaults={'technology': 'OBN', 'obn_system': 'G3i'}
        )

print(f'\nTotal projects: {Project.objects.count()}')
print(f'Won projects: {Project.objects.filter(status="Won").count()}')
