from django.shortcuts import render, redirect, get_object_or_404
from django.contrib.auth.decorators import login_required
from django.contrib import messages
from django.db.models import Exists, OuterRef, Prefetch

from .forms import ProjectForm, ProjectTechnologyFormSet, FinancialForm, ProjectEditForm
from .models import (
    Project, Client, ProjectTechnology, Financial, BidTypeHistory,
    ProjectContract, Competitor
)


@login_required
def dashboard(request):
    """
    Dashboard showing summary cards and a single Projects table.
    Added select_related('contract') and prefetch competitors so the template can
    render status pills and the contract modal.
    Pass competitor choices for the modal select.
    """
    projects_count = Project.objects.count()
    clients_count = Client.objects.count()
    technologies_count = ProjectTechnology.objects.count()
    financials_count = Financial.objects.count()

    # Prefetch competitors; select_related contract for optional contract fields
    projects = (
        Project.objects.select_related('client', 'contract')
        .prefetch_related(Prefetch('competitors'))
        .annotate(has_financial=Exists(Financial.objects.filter(project=OuterRef('pk'))))
        .order_by('-date_received', '-project_id')
    )

    context = {
        'projects_count': projects_count,
        'clients_count': clients_count,
        'technologies_count': technologies_count,
        'financials_count': financials_count,
        'projects': projects,
        'competitor_choices': getattr(Competitor, 'COMPETITOR_CHOICES', []),
    }
    return render(request, 'market_analysis/dashboard.html', context)


@login_required
def create_project(request):
    """
    Create project and open a modal allowing the user to add multiple technologies
    for the created project. Modal carries an inline formset that can submit
    many ProjectTechnology rows in a single POST.
    """
    if request.method == 'POST':
        form = ProjectForm(request.POST)
        if form.is_valid():
            project = form.save()
            messages.success(request, 'Project created successfully. Add technology now.')
            # Provide an empty inline formset instance bound to the created project
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
    Handle POST of the inline formset (multiple technologies) from modal or
    standalone page. Each valid form in formset creates/updates a ProjectTechnology.
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
    Create or edit Financial record for a project. Centered, compact Bootstrap form.
    """
    project = get_object_or_404(Project, pk=project_id)
    try:
        financial = project.financials  # OneToOne relation: may raise DoesNotExist
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

    # Pass computed values (if present) to template so they can be shown read-only
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
    Edit project (web form). Preserves snapshots/change logs and records the user
    who made the changes. If status becomes 'Lost' allows entering competitor that won.
    """
    project = get_object_or_404(Project, pk=project_id)
    # fetch previous copy for comparison
    try:
        prev = Project.objects.get(pk=project.pk)
    except Project.DoesNotExist:
        prev = None

    if request.method == 'POST':
        form = ProjectEditForm(request.POST, instance=project)
        if form.is_valid():
            # detect changes
            new_bid = form.cleaned_data.get('bid_type')
            new_status = form.cleaned_data.get('status')

            # create snapshots with created_by=request.user BEFORE save so snapshot stores previous values
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
                # avoid blocking save on snapshot failure
                pass

            # save Project (model.save will set dates, create BidTypeHistory/ProjectStatusHistory and ChangeLog rows)
            project = form.save()

            # attach changed_by to latest ChangeLog entries created by the save
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

            # If project became Lost, optionally record competitor
            competitor_name = form.cleaned_data.get('competitor_name')
            if project.status == 'Lost' and competitor_name:
                try:
                    # create competitor record (if not duplicate)
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
        # pre-fill competitor_name if a recent competitor exists
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
    Accept POST from dashboard modal to set ProjectContract.actual_start/actual_end
    (and automatically compute actual_duration via ProjectContract.save()).
    If project.status == 'Lost' a competitor choice may also be submitted and will
    create a Competitor row (can be empty/unknown).
    """
    project = get_object_or_404(Project, pk=project_id)
    contract, _ = ProjectContract.objects.get_or_create(project=project)

    if request.method == 'POST':
        actual_start = request.POST.get('actual_start')  # expected YYYY-MM-DD or empty
        actual_end = request.POST.get('actual_end')
        competitor_choice = request.POST.get('competitor')  # '' or choice key

        # parse dates safely
        from datetime import datetime
        def parse_date(s):
            if not s:
                return None
            try:
                return datetime.strptime(s, '%Y-%m-%d').date()
            except Exception:
                return None

        contract.actual_start = parse_date(actual_start)
        contract.actual_end = parse_date(actual_end)
        contract.save()

        # if lost, optionally create competitor record (allow blank/unknown)
        if project.status == 'Lost':
            if competitor_choice is not None:
                try:
                    # empty string will store name='' (allowed); include created_by for audit
                    Competitor.objects.create(project=project, name=competitor_choice or None, created_by=request.user)
                except Exception:
                    pass

        messages.success(request, 'Contract / competitor saved.')
    return redirect('market_analysis:dashboard')