from django.shortcuts import render
from django.contrib.auth.decorators import login_required
from .models import Opportunity, Client, Project

@login_required
def dashboard(request):
    opportunities_count = Opportunity.objects.count()
    clients_count = Client.objects.count()
    projects_count = Project.objects.count()
    recent_opportunities = Opportunity.objects.select_related('client', 'project').order_by('-unique_op_id')[:5]
    
    context = {
        'opportunities_count': opportunities_count,
        'clients_count': clients_count,
        'projects_count': projects_count,
        'recent_opportunities': recent_opportunities,
    }
    return render(request, 'market_analysis/dashboard.html', context)