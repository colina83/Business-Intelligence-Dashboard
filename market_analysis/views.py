from datetime import datetime, date
import json
from django.shortcuts import render, redirect, get_object_or_404
from django.contrib.auth.decorators import login_required
from django.contrib import messages
from django.db.models import Exists, OuterRef, Prefetch, Q
from django.db.models.functions import Coalesce
from django.forms import ModelForm, NumberInput, Select
from django.core.paginator import Paginator, EmptyPage, PageNotAnInteger

from .forms import ProjectForm, ProjectTechnologyFormSet, FinancialForm, ProjectEditForm
from .models import (
    Project, Client, ProjectTechnology, Financial, BidTypeHistory,
    ProjectContract, Competitor, ScopeOfWork, ProjectSnapshot,
    ProjectStatusHistory, ChangeLog, _build_snapshot_from_instance
)


class ScopeOfWorkForm(ModelForm):
    class Meta:
        model = ScopeOfWork
        # keep fields in sync with the model
        fields = (
            'total_rx_locs', 'total_sx_locs', 'max_active_spread', 'crew_node_count',
            'node_area', 'source_area', 'node_grid_IL', 'node_grid_XL',
            'source_grid_IL', 'source_grid_XL', 'water_depth_min', 'water_depth_max',
            'node_category'
        )

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)

        # default compact class for all fields
        for name, field in self.fields.items():
            field.widget.attrs.setdefault('class', 'form-control form-control-sm')

        # make the IL/XL inputs small for node/source grids
        small_inputs = ['node_grid_IL', 'node_grid_XL', 'source_grid_IL', 'source_grid_XL']
        for fname in small_inputs:
            if fname in self.fields:
                self.fields[fname].widget.attrs.update({
                    'style': 'width:110px; display: inline-block;',
                    'inputmode': 'numeric',
                    'placeholder': self.fields[fname].label or ''
                })


@login_required
def dashboard(request):
    """
    Dashboard with GitHub-style layout showing:
    - ROW 1: Active Projects (Ongoing/Submitted) with pagination
    - ROW 2: Win/Lost Pie Chart and EBIT/Day Bar Chart
    - ROW 3: Latest Surveys (Won projects) data table
    """
    today = date.today()
    current_year = today.year
    
    # ROW 1: Active Projects (Ongoing or Submitted)
    active_projects_qs = (
        Project.objects.select_related('client', 'contract')
        .prefetch_related(Prefetch('competitors'))
        .filter(Q(status='Ongoing') | Q(status='Submitted'))
        .annotate(
            has_financial=Exists(Financial.objects.filter(project=OuterRef('pk'))),
            has_scope=Exists(ScopeOfWork.objects.filter(project=OuterRef('pk')))
        )
        .order_by('-date_received', '-project_id')
    )
    
    # Add days to deadline calculation for each project
    active_projects_list = []
    for p in active_projects_qs:
        if p.deadline_date:
            delta = (p.deadline_date - today).days
            p.days_to_deadline = delta
        else:
            p.days_to_deadline = None
        active_projects_list.append(p)
    
    # Pagination for active projects
    active_page = request.GET.get('active_page', 1)
    active_paginator = Paginator(active_projects_list, 10)
    try:
        active_projects_page_obj = active_paginator.page(active_page)
    except PageNotAnInteger:
        active_projects_page_obj = active_paginator.page(1)
    except EmptyPage:
        active_projects_page_obj = active_paginator.page(active_paginator.num_pages)
    
    # Build compact page range helper
    def compact_page_range(paginator, current, window=3):
        total = paginator.num_pages
        if total <= 10:
            return list(range(1, total + 1))
        start = max(1, current - window)
        end = min(total, current + window)
        pages = [1]
        if start > 2:
            pages.append('...')
        for i in range(start, end + 1):
            pages.append(i)
        if end < total - 1:
            pages.append('...')
        pages.append(total)
        # remove duplicates while preserving order
        seen = set()
        out = []
        for p in pages:
            if p not in seen:
                out.append(p)
                seen.add(p)
        return out
    
    active_projects_page_range = compact_page_range(active_paginator, active_projects_page_obj.number)
    
    # ROW 2: Win/Lost statistics (from 2021 to current year)
    win_count = Project.objects.filter(
        status='Won',
        award_date__year__gte=2021,
        award_date__year__lte=current_year
    ).count()
    
    lost_count = Project.objects.filter(
        status='Lost',
        lost_date__year__gte=2021,
        lost_date__year__lte=current_year
    ).count()
    
    # EBIT/Day data for latest 6 Won or Lost projects with financial data
    # Use Coalesce to order by the most recent relevant date (award_date or lost_date)
    ebit_projects = (
        Project.objects.select_related('financials')
        .filter(
            Q(status='Won') | Q(status='Lost'),
            financials__ebit_day__isnull=False
        )
        .annotate(
            result_date=Coalesce('award_date', 'lost_date')
        )
        .order_by('-result_date', '-project_id')[:6]
    )
    
    ebit_data = []
    for p in ebit_projects:
        try:
            ebit_day = float(p.financials.ebit_day) if p.financials and p.financials.ebit_day else 0
            ebit_data.append({
                'name': p.name,
                'ebit_day': ebit_day,
                'status': p.status
            })
        except (AttributeError, TypeError):
            pass
    
    # Reverse to show oldest first (for chart display)
    ebit_data = list(reversed(ebit_data))
    
    # ROW 3: Latest Surveys (Won projects with contract info)
    latest_surveys = (
        Project.objects.select_related('client', 'contract')
        .filter(status='Won')
        .order_by('-award_date', '-project_id')[:10]
    )
    
    context = {
        # ROW 1: Active Projects
        'active_projects': active_projects_page_obj.object_list,
        'active_projects_page_obj': active_projects_page_obj,
        'active_projects_paginator': active_paginator,
        'active_projects_page_range': active_projects_page_range,
        
        # ROW 2: Charts data
        'current_year': current_year,
        'win_count': win_count,
        'lost_count': lost_count,
        'ebit_data': json.dumps(ebit_data),
        
        # ROW 3: Latest Surveys
        'latest_surveys': latest_surveys,
        
        # Modal data
        'competitor_choices': getattr(Competitor, 'COMPETITOR_CHOICES', []),
        'status_choices': Project.STATUS,
    }
    return render(request, 'market_analysis/dashboard.html', context)


@login_required
def create_project(request):
    """
    Create project and (optionally) open the technology modal afterwards.
    """
    if request.method == 'POST':
        form = ProjectForm(request.POST, request.FILES)
        if form.is_valid():
            project = form.save()
            messages.success(request, 'Project created successfully. Add technology now.')
            tech_formset = ProjectTechnologyFormSet(instance=project, prefix='tech')
            return render(request, 'market_analysis/project_form.html', {
                'form': ProjectForm(),
                'show_tech_modal': True,
                'tech_formset': tech_formset,
                'project': project,
            })
        else:
            messages.error(request, 'Please correct the errors below.')
    else:
        form = ProjectForm()

    return render(request, 'market_analysis/project_form.html', {
        'form': form,
    })


@login_required
def add_technology(request, project_id):
    """
    Handle POST of the inline formset (multiple technologies).
    """
    project = get_object_or_404(Project, pk=project_id)

    if request.method != 'POST':
        tech_formset = ProjectTechnologyFormSet(instance=project, prefix='tech')
        return render(request, 'market_analysis/technology_form.html', {
            'tech_formset': tech_formset,
            'project': project,
        })

    tech_formset = ProjectTechnologyFormSet(request.POST, instance=project, prefix='tech')
    if tech_formset.is_valid():
        tech_formset.save()
        messages.success(request, 'Technologies saved for project.')
        return redirect('market_analysis:dashboard')
    else:
        messages.error(request, 'Please fix the errors below.')
        return render(request, 'market_analysis/project_form.html', {
            'form': ProjectForm(),
            'show_tech_modal': True,
            'tech_formset': tech_formset,
            'project': project,
        })


@login_required
def add_or_edit_financial(request, project_id):
    """
    Create or edit Financial record for a project.
    """
    project = get_object_or_404(Project, pk=project_id)
    try:
        financial = project.financials
    except Financial.DoesNotExist:
        financial = None

    if request.method == 'POST':
        form = FinancialForm(request.POST, request.FILES, instance=financial)
        if form.is_valid():
            fin = form.save(commit=False)
            fin.project = project
            fin.save()
            messages.success(request, 'Financials saved successfully.')
            return redirect('market_analysis:dashboard')
        else:
            messages.error(request, 'Please correct the errors below.')
    else:
        form = FinancialForm(instance=financial)

    computed = None
    if financial:
        computed = {
            'total_direct_cost': financial.total_direct_cost,
            'depreciation': financial.depreciation,
            'total_revenue': financial.total_revenue,
            'gp': financial.gp,
            'total_overhead': financial.total_overhead,
            'ebitda_amount': financial.ebitda_amount,
            'ebitda_pct': financial.ebitda_pct,
            'ebit_amount': financial.ebit_amount,
            'ebit_pct': financial.ebit_pct,
            'net_amount': financial.net_amount,
            'net_pct': financial.net_pct,
            'ebit_day': financial.ebit_day,
            'net_day': financial.net_day,
        }

    return render(request, 'market_analysis/financial_form.html', {
        'form': form,
        'project': project,
        'computed': computed,
    })


@login_required
def edit_project(request, project_id):
    """
    Edit project. Create snapshots and attach changed_by where possible.
    """
    project = get_object_or_404(Project, pk=project_id)
    try:
        prev = Project.objects.get(pk=project.pk)
    except Project.DoesNotExist:
        prev = None

    if request.method == 'POST':
        form = ProjectEditForm(request.POST, request.FILES, instance=project)
        if form.is_valid():
            new_bid = form.cleaned_data.get('bid_type')
            new_status = form.cleaned_data.get('status')

            try:
                if prev:
                    if prev.bid_type != new_bid:
                        ProjectSnapshot.objects.create(
                            project=project,
                            change_type='BID',
                            snapshot=_build_snapshot_from_instance(prev),
                            snapshot_name=(prev.internal_id or prev.name),
                            created_by=request.user
                        )
                    if prev.status != new_status:
                        ProjectSnapshot.objects.create(
                            project=project,
                            change_type='STATUS',
                            snapshot=_build_snapshot_from_instance(prev),
                            snapshot_name=(prev.internal_id or prev.name),
                            created_by=request.user
                        )
            except Exception:
                pass

            project = form.save()

            try:
                if prev and prev.status != project.status:
                    cl = ChangeLog.objects.filter(project=project, change_type='STATUS', new_value=project.status, changed_by__isnull=True).order_by('-changed_at').first()
                    if cl:
                        cl.changed_by = request.user
                        cl.save(update_fields=['changed_by'])
                if prev and prev.bid_type != project.bid_type:
                    cl2 = ChangeLog.objects.filter(project=project, change_type='BID', new_value=project.bid_type, changed_by__isnull=True).order_by('-changed_at').first()
                    if cl2:
                        cl2.changed_by = request.user
                        cl2.save(update_fields=['changed_by'])
            except Exception:
                pass

            competitor_name = form.cleaned_data.get('competitor_name')
            if project.status == 'Lost' and competitor_name:
                try:
                    Competitor.objects.create(
                        project=project,
                        name=competitor_name,
                    )
                except Exception:
                    pass

            messages.success(request, 'Project updated successfully.')
            return redirect('market_analysis:dashboard')
        else:
            messages.error(request, 'Please correct the errors below.')
    else:
        initial = {}
        recent_comp = project.competitors.order_by('-created_at').first()
        if recent_comp:
            initial['competitor_name'] = recent_comp.name
        form = ProjectEditForm(instance=project, initial=initial)

    return render(request, 'market_analysis/project_edit.html', {
        'form': form,
        'project': project,
    })


@login_required
def update_contract(request, project_id):
    """
    Accept POST from dashboard modal to:
      - optionally change project.status (new_status)
      - set submission/award/lost dates when provided
      - update ProjectContract.contract_date/actual_start/actual_end (and compute duration)
      - optionally create Competitor when status becomes Lost (or is Lost)
    """
    project = get_object_or_404(Project, pk=project_id)
    contract, _ = ProjectContract.objects.get_or_create(project=project)

    if request.method == 'POST':
        # Dates from form (may be empty)
        actual_start = request.POST.get('actual_start')
        actual_end = request.POST.get('actual_end')
        submission_date = request.POST.get('submission_date')
        award_date = request.POST.get('award_date')
        lost_date = request.POST.get('lost_date')
        contract_date = request.POST.get('contract_date')
        competitor_choice = request.POST.get('competitor')
        new_status = request.POST.get('new_status')  # optional

        def parse_date(s):
            if not s:
                return None
            try:
                return datetime.strptime(s, '%Y-%m-%d').date()
            except Exception:
                return None

        # Persist any explicit project-level dates unconditionally (allows editing award_date for existing Won projects)
        if submission_date:
            project.submission_date = parse_date(submission_date)
        if award_date:
            project.award_date = parse_date(award_date)
        if lost_date:
            project.lost_date = parse_date(lost_date)

        # If a new_status was chosen, set it (status change logic will run in Project.save)
        if new_status:
            project.status = new_status

        # Save project first so any Project.save hooks run (snapshots, status history, etc.)
        try:
            project.save()
        except Exception:
            # swallow - we still want to persist contract if possible
            pass

        # Update contract fields (always allowed). contract_date only set if provided.
        contract.actual_start = parse_date(actual_start)
        contract.actual_end = parse_date(actual_end)
        if contract_date:
            contract.contract_date = parse_date(contract_date)
        try:
            contract.save()
        except Exception:
            messages.warning(request, 'Could not save contract details; check server logs.')

        # If project is Lost (either newly set or already lost), optionally record competitor
        if (new_status == 'Lost' or project.status == 'Lost') and competitor_choice is not None:
            try:
                Competitor.objects.create(project=project, name=competitor_choice or None, created_by=request.user)
            except Exception:
                pass

        messages.success(request, 'Saved status/contract/competitor changes.')

    return redirect('market_analysis:dashboard')


@login_required
def manage_scope(request, project_id):
    """
    View / add / edit ScopeOfWork for a project.
    - If a ScopeOfWork exists for the project, show form pre-filled (edit).
    - If none exists, present an empty form to create one.
    After save redirect back to dashboard.
    """
    project = get_object_or_404(Project, pk=project_id)
    scope = project.scopes_of_work.order_by('-created_at').first()

    if request.method == 'POST':
        form = ScopeOfWorkForm(request.POST, instance=scope)
        if form.is_valid():
            inst = form.save(commit=False)
            inst.project = project
            if scope is None:
                inst.created_by = request.user
            inst.save()
            messages.success(request, 'Scope of Work saved.')
            return redirect('market_analysis:dashboard')
        else:
            messages.error(request, 'Please correct the errors below.')
    else:
        form = ScopeOfWorkForm(instance=scope)

    return render(request, 'market_analysis/scope_form.html', {
        'form': form,
        'project': project,
        'scope': scope,
    })


@login_required
def project_opportunities(request):
    """
    Project Opportunities page showing all projects with full details.
    Features:
    - All columns from legacy table (Internal ID, Client, Project Name, Bid Type, Country, Region,
      Bid Received, Deadline, Days to Deadline, Bid Submitted, Status, Comments, Actions)
    - Horizontal scrollbar for extensive data
    - Pagination with 20 records per page
    - Powerful search functionality across all columns
    """
    today = date.today()
    
    # Get search query
    search_query = request.GET.get('q', '').strip()
    
    # Base queryset with all projects
    projects_qs = (
        Project.objects.select_related('client', 'contract')
        .prefetch_related(Prefetch('competitors'))
        .annotate(
            has_financial=Exists(Financial.objects.filter(project=OuterRef('pk'))),
            has_scope=Exists(ScopeOfWork.objects.filter(project=OuterRef('pk')))
        )
        .order_by('-date_received', '-project_id')
    )
    
    # Apply search filter if search query is provided
    if search_query:
        projects_qs = projects_qs.filter(
            Q(internal_id__icontains=search_query) |
            Q(name__icontains=search_query) |
            Q(client__name__icontains=search_query) |
            Q(bid_type__icontains=search_query) |
            Q(country__icontains=search_query) |
            Q(region__icontains=search_query) |
            Q(status__icontains=search_query) |
            Q(comments__icontains=search_query)
        )
    
    # Add days to deadline calculation for each project
    projects_list = []
    for p in projects_qs:
        if p.deadline_date:
            delta = (p.deadline_date - today).days
            p.days_to_deadline = delta
        else:
            p.days_to_deadline = None
        projects_list.append(p)
    
    # Pagination - 20 records per page
    page = request.GET.get('page', 1)
    paginator = Paginator(projects_list, 20)
    try:
        projects_page_obj = paginator.page(page)
    except PageNotAnInteger:
        projects_page_obj = paginator.page(1)
    except EmptyPage:
        projects_page_obj = paginator.page(paginator.num_pages)
    
    # Build compact page range helper
    def compact_page_range(paginator, current, window=3):
        total = paginator.num_pages
        if total <= 10:
            return list(range(1, total + 1))
        start = max(1, current - window)
        end = min(total, current + window)
        pages = [1]
        if start > 2:
            pages.append('...')
        for i in range(start, end + 1):
            pages.append(i)
        if end < total - 1:
            pages.append('...')
        pages.append(total)
        # remove duplicates while preserving order
        seen = set()
        out = []
        for p in pages:
            if p not in seen:
                out.append(p)
                seen.add(p)
        return out
    
    projects_page_range = compact_page_range(paginator, projects_page_obj.number)
    
    context = {
        'projects': projects_page_obj.object_list,
        'projects_page_obj': projects_page_obj,
        'projects_paginator': paginator,
        'projects_page_range': projects_page_range,
        'search_query': search_query,
        'total_projects': paginator.count,
        'competitor_choices': getattr(Competitor, 'COMPETITOR_CHOICES', []),
        'status_choices': Project.STATUS,
    }
    return render(request, 'market_analysis/project_opportunities.html', context)


@login_required
def update_comment(request, project_id):
    """Handle AJAX/modal POST to update a project's comments (HTML allowed).
    - GET returns a small form fragment for the modal body.
    - POST saves the comment and redirects back to project_opportunities.
    """
    project = get_object_or_404(Project, pk=project_id)

    if request.method == 'POST':
        new_comment = request.POST.get('comments')
        project.comments = new_comment
        try:
            project.save()
            messages.success(request, 'Comments saved.')
        except Exception:
            messages.error(request, 'Could not save comments; check server logs.')
        # redirect back to opportunities (could be AJAX)
        return redirect('market_analysis:project_opportunities')

    # GET -> render fragment
    return render(request, 'market_analysis/comment_modal_fragment.html', {
        'project': project,
    })


@login_required
def project_detail(request, project_id):
    """
    Project Detail page with card layout showing all project information.
    
    Layout:
    - Project Name header
    - Map Image / Placeholder
    - Row 1: Card 1 (Date Information + Competitor if Lost), Card 2 (Technology)
    - Row 2: Card 1 (Scope of Work), Card 2 (Financial Information)
    - Row 3: Card 1 (Contract Dates), Card 2 (Comments During Bid)
    """
    project = get_object_or_404(
        Project.objects.select_related('client', 'contract')
        .prefetch_related('technologies', 'scopes_of_work', 'competitors'),
        pk=project_id
    )
    
    # Get related data
    technology = project.technologies.first()
    scope = project.scopes_of_work.order_by('-created_at').first()
    
    try:
        financial = project.financials
    except Financial.DoesNotExist:
        financial = None
    
    try:
        contract = project.contract
    except ProjectContract.DoesNotExist:
        contract = None
    
    # Get competitor if project is Lost
    competitor = None
    if project.status == 'Lost':
        competitor = project.competitors.order_by('-created_at').first()
    
    context = {
        'project': project,
        'technology': technology,
        'scope': scope,
        'financial': financial,
        'contract': contract,
        'competitor': competitor,
    }
    return render(request, 'market_analysis/project_detail.html', context)


@login_required
def tendering_cycle(request):
    """
    Tendering Cycle Time page: comprehensive dashboard with charts and table.
    Shows cycle time data for Won tenders with various visualizations.
    """
    from django.http import JsonResponse
    from collections import defaultdict
    import statistics
    
    # Maximum reasonable cycle time in days (for filtering outliers)
    MAX_CYCLE_DAYS = 365
    
    # Check if this is a JSON request for chart data
    if request.GET.get('format') == 'json':
        selected_year = request.GET.get('year', 'all')
        
        # Get all won projects with OBN technology
        qs = (
            Project.objects.select_related('client', 'contract')
            .prefetch_related('technologies')
            .filter(status='Won')
        )
        
        # Filter by year if specified
        if selected_year != 'all':
            try:
                year = int(selected_year)
                qs = qs.filter(date_received__year=year)
            except ValueError:
                pass
        
        # helper to compute days between two dates
        def _days_between(later, earlier):
            if not earlier or not later:
                return None
            try:
                return (later - earlier).days
            except Exception:
                return None
        
        # Collect cycle time data
        cycle_data = {
            'rec_to_sub': [],
            'sub_to_award': [],
            'award_to_contract': [],
            'contract_to_start': []
        }
        
        # For OBN tenders count by year
        obn_by_year = defaultdict(lambda: {'received': 0, 'started': 0})
        client_counts = defaultdict(int)
        
        for p in qs:
            date_received = getattr(p, 'date_received', None)
            submission_date = getattr(p, 'submission_date', None)
            award_date = getattr(p, 'award_date', None)
            contract = getattr(p, 'contract', None)
            contract_date = getattr(contract, 'contract_date', None) if contract else None
            start_date = getattr(contract, 'actual_start', None) if contract else None
            
            # Check if project is OBN
            is_obn = p.technologies.filter(technology='OBN').exists()
            
            # Collect cycle times (remove outliers using IQR method)
            rec_to_sub = _days_between(submission_date, date_received)
            sub_to_award = _days_between(award_date, submission_date)
            award_to_contract = _days_between(contract_date, award_date)
            contract_to_start = _days_between(start_date, contract_date)
            
            # Only add non-null, positive values within reasonable limits
            if rec_to_sub and rec_to_sub > 0 and rec_to_sub < MAX_CYCLE_DAYS:
                cycle_data['rec_to_sub'].append(rec_to_sub)
            if sub_to_award and sub_to_award > 0 and sub_to_award < MAX_CYCLE_DAYS:
                cycle_data['sub_to_award'].append(sub_to_award)
            if award_to_contract and award_to_contract > 0 and award_to_contract < MAX_CYCLE_DAYS:
                cycle_data['award_to_contract'].append(award_to_contract)
            if contract_to_start and contract_to_start > 0 and contract_to_start < MAX_CYCLE_DAYS:
                cycle_data['contract_to_start'].append(contract_to_start)
            
            # Count OBN tenders by year
            if is_obn:
                if date_received:
                    obn_by_year[date_received.year]['received'] += 1
                if start_date:
                    obn_by_year[start_date.year]['started'] += 1
                
                # Count by client
                if p.client:
                    client_counts[p.client.name] += 1
        
        # Remove outliers using IQR method for each metric
        def remove_outliers(data):
            if len(data) < 4:
                return data
            # Sort data for quartile calculation
            sorted_data = sorted(data)
            n = len(sorted_data)
            # Calculate Q1 and Q3 manually for Python 3.7+ compatibility
            q1_pos = n * 0.25
            q3_pos = n * 0.75
            q1 = sorted_data[int(q1_pos)] if q1_pos == int(q1_pos) else (
                sorted_data[int(q1_pos)] + sorted_data[int(q1_pos) + 1]) / 2
            q3 = sorted_data[int(q3_pos)] if q3_pos == int(q3_pos) else (
                sorted_data[int(q3_pos)] + sorted_data[int(q3_pos) + 1]) / 2
            iqr = q3 - q1
            lower_bound = q1 - 1.5 * iqr
            upper_bound = q3 + 1.5 * iqr
            return [x for x in data if lower_bound <= x <= upper_bound]
        
        for key in cycle_data:
            cycle_data[key] = remove_outliers(cycle_data[key])
        
        # Calculate statistics
        stats = {}
        for key, values in cycle_data.items():
            if values:
                stats[key] = {
                    'mean': round(statistics.mean(values), 1),
                    'median': round(statistics.median(values), 1),
                    'min': min(values),
                    'max': max(values),
                    'count': len(values)
                }
        
        # Prepare OBN by year data
        obn_years = sorted(obn_by_year.keys())
        obn_received_counts = [obn_by_year[year]['received'] for year in obn_years]
        obn_started_counts = [obn_by_year[year]['started'] for year in obn_years]
        
        # Prepare client data (top 10)
        client_data = sorted(client_counts.items(), key=lambda x: x[1], reverse=True)[:10]
        client_names = [name for name, _ in client_data]
        client_values = [count for _, count in client_data]
        
        return JsonResponse({
            'cycle_data': cycle_data,
            'stats': stats,
            'obn_by_year': {
                'years': obn_years,
                'received': obn_received_counts,
                'started': obn_started_counts
            },
            'client_data': {
                'names': client_names,
                'counts': client_values
            }
        })
    
    # Regular page view
    qs = (
        Project.objects.select_related('client', 'contract')
        .filter(status='Won')
        .order_by('-award_date', '-project_id')
    )

    # helper to compute days between two dates (later - earlier)
    def _days_between(later, earlier):
        if not earlier or not later:
            return None
        try:
            return (later - earlier).days
        except Exception:
            return None

    # annotate each Project instance with cycle attributes used in template
    won_list = []
    for p in qs:
        date_received = getattr(p, 'date_received', None)
        submission_date = getattr(p, 'submission_date', None)
        award_date = getattr(p, 'award_date', None)
        contract = getattr(p, 'contract', None)
        contract_date = getattr(contract, 'contract_date', None) if contract else None
        start_date = getattr(contract, 'actual_start', None) if contract else None

        p.cycle_rec_to_sub = _days_between(submission_date, date_received)
        p.cycle_sub_to_award = _days_between(award_date, submission_date)
        p.cycle_award_to_contract = _days_between(contract_date, award_date)
        p.cycle_contract_to_start = _days_between(start_date, contract_date)
        p.cycle_rec_to_start = _days_between(start_date, date_received)
        p.cycle_award_to_start = _days_between(start_date, award_date)

        won_list.append(p)

    # Get available years for filter
    available_years = sorted(set(
        p.date_received.year for p in qs if p.date_received
    ), reverse=True)

    # pagination for won list
    from django.core.paginator import Paginator, EmptyPage, PageNotAnInteger
    page = request.GET.get('page', 1)
    paginator = Paginator(won_list, 25)
    try:
        page_obj_won = paginator.page(page)
    except PageNotAnInteger:
        page_obj_won = paginator.page(1)
    except EmptyPage:
        page_obj_won = paginator.page(paginator.num_pages)

    context = {
        'page_obj_won': page_obj_won,
        'available_years': available_years,
    }
    return render(request, 'market_analysis/tendering_cycle.html', context)


@login_required
def pricing_graphs(request):
    """
    Pricing Graphs page with bubble charts showing OBN bid results.
    
    Features:
    - Four bubble charts in two rows:
      Row 1: EBIT$/Day and EBIT%
      Row 2: Net$/Day and Net%
    - X-axis: Bid submission date with year scale
    - Y-axis: EBIT$/Day, EBIT%, Net$/Day, or Net%
    - Bubble colors: Gray (Pending/Cancelled/Postpone), Blue (Won), Orange (Lost)
    - Filters: Start Year, End Year, Client, Competitor (for Lost), Region, Country
    - Download functionality
    """
    from django.http import JsonResponse  # Used only in this view for JSON response
    
    # Check if this is a JSON request for chart data
    if request.GET.get('format') == 'json':
        start_year = request.GET.get('start_year')
        end_year = request.GET.get('end_year')
        client_id = request.GET.get('client')
        competitor = request.GET.get('competitor')
        region = request.GET.get('region')
        country = request.GET.get('country')
        
        # Get all projects with OBN technology and financial data
        # Only include results that are Won, Lost, Cancelled or No Bid
        # (exclude Ongoing and Submitted from plots)
        qs = (
            Project.objects.select_related('client', 'financials')
            .prefetch_related('technologies', 'competitors')
            .filter(
                technologies__technology='OBN',
                financials__isnull=False,
                submission_date__isnull=False,
                status__in=['Won', 'Lost', 'Cancelled', 'No Bid']
            )
        )
        
        # Filter by year range if specified
        if start_year:
            try:
                qs = qs.filter(submission_date__year__gte=int(start_year))
            except ValueError:
                pass
        
        if end_year:
            try:
                qs = qs.filter(submission_date__year__lte=int(end_year))
            except ValueError:
                pass
        
        # Filter by client if specified
        if client_id:
            try:
                qs = qs.filter(client_id=int(client_id))
            except ValueError:
                pass
        
        # Filter by competitor if specified (for Lost projects)
        if competitor:
            qs = qs.filter(competitors__name=competitor).distinct()
        
        # Filter by region if specified
        if region:
            qs = qs.filter(region=region)
        
        # Filter by country if specified
        if country:
            qs = qs.filter(country=country)
        
        # Collect bubble data
        ebit_day_data = []
        ebit_pct_data = []
        net_day_data = []
        net_pct_data = []
        
        for p in qs:
            try:
                financial = p.financials
                
                # Determine bubble color based on status
                # Won bids are blue, Lost bids are orange, all others (Ongoing, Submitted, Cancelled, No Bid) are gray
                if p.status == 'Won':
                    color = 'rgba(54, 162, 235, 0.6)'  # Blue
                    status_label = 'Won'
                elif p.status == 'Lost':
                    color = 'rgba(255, 159, 64, 0.6)'  # Orange
                    status_label = 'Lost'
                else:
                    # Only Cancelled and No Bid should be treated as 'Other' (gray)
                    if p.status in ('Cancelled', 'No Bid'):
                        color = 'rgba(128, 128, 128, 0.6)'
                        status_label = 'Other'
                    else:
                        # Any other status (should be excluded by queryset) - skip
                        continue
                
                # EBIT$/Day data point
                if financial.ebit_day is not None:
                    ebit_day_data.append({
                        'x': p.submission_date.isoformat(),
                        'y': float(financial.ebit_day),
                        'r': 15,  # bubble radius - increased for visibility
                        'label': p.name,
                        'client': p.client.name if p.client else 'N/A',
                        'status': status_label,
                        'color': color,
                        'ebit_day': float(financial.ebit_day) if financial.ebit_day else 0,
                        'ebit_pct': float(financial.ebit_pct) if financial.ebit_pct else 0,
                        'net_day': float(financial.net_day) if financial.net_day else 0,
                        'net_pct': float(financial.net_pct) if financial.net_pct else 0,
                    })
                
                # EBIT% data point
                if financial.ebit_pct is not None:
                    ebit_pct_data.append({
                        'x': p.submission_date.isoformat(),
                        'y': float(financial.ebit_pct),
                        'r': 15,  # bubble radius - increased for visibility
                        'label': p.name,
                        'client': p.client.name if p.client else 'N/A',
                        'status': status_label,
                        'color': color,
                        'ebit_day': float(financial.ebit_day) if financial.ebit_day else 0,
                        'ebit_pct': float(financial.ebit_pct) if financial.ebit_pct else 0,
                        'net_day': float(financial.net_day) if financial.net_day else 0,
                        'net_pct': float(financial.net_pct) if financial.net_pct else 0,
                    })
                
                # Net$/Day data point
                if financial.net_day is not None:
                    net_day_data.append({
                        'x': p.submission_date.isoformat(),
                        'y': float(financial.net_day),
                        'r': 15,  # bubble radius - increased for visibility
                        'label': p.name,
                        'client': p.client.name if p.client else 'N/A',
                        'status': status_label,
                        'color': color,
                        'ebit_day': float(financial.ebit_day) if financial.ebit_day else 0,
                        'ebit_pct': float(financial.ebit_pct) if financial.ebit_pct else 0,
                        'net_day': float(financial.net_day) if financial.net_day else 0,
                        'net_pct': float(financial.net_pct) if financial.net_pct else 0,
                    })
                
                # Net% data point
                if financial.net_pct is not None:
                    net_pct_data.append({
                        'x': p.submission_date.isoformat(),
                        'y': float(financial.net_pct),
                        'r': 15,  # bubble radius - increased for visibility
                        'label': p.name,
                        'client': p.client.name if p.client else 'N/A',
                        'status': status_label,
                        'color': color,
                        'ebit_day': float(financial.ebit_day) if financial.ebit_day else 0,
                        'ebit_pct': float(financial.ebit_pct) if financial.ebit_pct else 0,
                        'net_day': float(financial.net_day) if financial.net_day else 0,
                        'net_pct': float(financial.net_pct) if financial.net_pct else 0,
                    })
                    
            except (AttributeError, TypeError, ValueError) as e:
                # Skip projects with missing or invalid data
                continue
        
        return JsonResponse({
            'ebit_day_data': ebit_day_data,
            'ebit_pct_data': ebit_pct_data,
            'net_day_data': net_day_data,
            'net_pct_data': net_pct_data,
        })
    
    # Regular page view - get available filter options
    obn_projects = Project.objects.filter(
        technologies__technology='OBN',
        submission_date__isnull=False
    ).distinct()
    
    available_years = sorted(set(
        p.submission_date.year for p in obn_projects if p.submission_date
    ))
    
    # Get unique clients from OBN projects
    available_clients = Client.objects.filter(
        projects__technologies__technology='OBN',
        projects__submission_date__isnull=False
    ).distinct().order_by('name')
    
    # Get unique regions
    available_regions = Project.REGIONS
    
    # Get unique countries from OBN projects
    available_countries = sorted(set(
        str(p.country) for p in obn_projects if p.country
    ))
    
    # Get competitor choices
    available_competitors = Competitor.COMPETITOR_CHOICES
    
    context = {
        'available_years': available_years,
        'available_clients': available_clients,
        'available_regions': available_regions,
        'available_countries': available_countries,
        'available_competitors': available_competitors,
    }
    return render(request, 'market_analysis/pricing_graphs.html', context)
