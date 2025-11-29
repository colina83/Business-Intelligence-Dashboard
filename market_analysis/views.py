from datetime import datetime
from django.shortcuts import render, redirect, get_object_or_404
from django.contrib.auth.decorators import login_required
from django.contrib import messages
from django.db.models import Exists, OuterRef, Prefetch
from django.forms import ModelForm, NumberInput, Select
from django.core.paginator import Paginator, EmptyPage, PageNotAnInteger

from .forms import ProjectForm, ProjectTechnologyFormSet, FinancialForm, ProjectEditForm
from .models import (
    Project, Client, ProjectTechnology, Financial, BidTypeHistory,
    ProjectContract, Competitor, ScopeOfWork, ProjectSnapshot,
    ProjectStatusHistory, ChangeLog
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
    Dashboard showing summary cards and two independent tables:
      - Projects (paginated via ?projects_page=)
      - Cycle Time (only Won projects, paginated via ?cycle_page=)
    """
    projects_count = Project.objects.count()
    clients_count = Client.objects.count()
    technologies_count = ProjectTechnology.objects.count()
    financials_count = Financial.objects.count()

    projects_qs = (
        Project.objects.select_related('client', 'contract')
        .prefetch_related(Prefetch('competitors'))
        .annotate(
            has_financial=Exists(Financial.objects.filter(project=OuterRef('pk'))),
            has_scope=Exists(ScopeOfWork.objects.filter(project=OuterRef('pk')))
        )
        .order_by('-date_received', '-project_id')
    )

    # Separate pagination parameters
    projects_page = request.GET.get('projects_page', 1)
    cycle_page = request.GET.get('cycle_page', 1)

    # Projects paginator
    paginator_projects = Paginator(projects_qs, 20)
    try:
        page_obj_projects = paginator_projects.page(projects_page)
    except PageNotAnInteger:
        page_obj_projects = paginator_projects.page(1)
    except EmptyPage:
        page_obj_projects = paginator_projects.page(paginator_projects.num_pages)

    # Won projects paginator (Cycle Time table)
    won_qs = projects_qs.filter(status='Won').order_by('-award_date', '-project_id')
    paginator_won = Paginator(won_qs, 20)
    try:
        page_obj_won = paginator_won.page(cycle_page)
    except PageNotAnInteger:
        page_obj_won = paginator_won.page(1)
    except EmptyPage:
        page_obj_won = paginator_won.page(paginator_won.num_pages)

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

    page_range_display_projects = compact_page_range(paginator_projects, page_obj_projects.number)
    page_range_display_won = compact_page_range(paginator_won, page_obj_won.number)

    # helper to compute days between dates (accepts date/datetime)
    def _days_between(later, earlier):
        try:
            if not later or not earlier:
                return 0
            if hasattr(later, 'date'):
                later = later.date()
            if hasattr(earlier, 'date'):
                earlier = earlier.date()
            return max(0, (later - earlier).days)
        except Exception:
            return 0

    # Compute cycle metrics only for the current won-page (efficient)
    for p in page_obj_won.object_list:
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

    context = {
        'projects_count': projects_count,
        'clients_count': clients_count,
        'technologies_count': technologies_count,
        'financials_count': financials_count,
        'projects': projects_qs,
        'page_obj_projects': page_obj_projects,
        'paginator_projects': paginator_projects,
        'page_range_display_projects': page_range_display_projects,
        'page_obj_won': page_obj_won,
        'paginator_won': paginator_won,
        'page_range_display_won': page_range_display_won,
        'competitor_choices': getattr(Competitor, 'COMPETITOR_CHOICES', []),
        'status_choices': Project.STATUS,  # list of (key, label)
    }
    return render(request, 'market_analysis/dashboard.html', context)


@login_required
def create_project(request):
    """
    Create project and (optionally) open the technology modal afterwards.
    """
    if request.method == 'POST':
        form = ProjectForm(request.POST)
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
        form = ProjectEditForm(request.POST, instance=project)
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