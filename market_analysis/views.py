from django.shortcuts import render
from django.contrib.auth.decorators import login_required
from .models import Client, Project, BidTypeHistory, ProjectTechnology, Financial

@login_required
def dashboard(request):
    projects_count = Project.objects.count()
    clients_count = Client.objects.count()
    technologies_count = ProjectTechnology.objects.count()
    financials_count = Financial.objects.count()
    bid_history_count = BidTypeHistory.objects.count()

    # Efficiently load related objects used in the template
    recent_projects = (
        Project.objects.select_related('client')
        .prefetch_related('technologies', 'financials')
        .order_by('-date_received', '-project_id')[:5]
    )

    recent_bid_history = (
        BidTypeHistory.objects.select_related('project', 'project__client')
        .order_by('-changed_at')[:5]
    )

    context = {
        'projects_count': projects_count,
        'clients_count': clients_count,
        'technologies_count': technologies_count,
        'financials_count': financials_count,
        'bid_history_count': bid_history_count,
        'recent_projects': recent_projects,
        'recent_bid_history': recent_bid_history,
    }
    return render(request, 'market_analysis/dashboard.html', context)