from django.contrib import admin
from .models import (
    Client, Project, BidTypeHistory, ProjectTechnology, Financial,
    ProjectStatusHistory, ProjectContract, ChangeLog, ProjectSnapshot, Competitor
)


@admin.register(Client)
class ClientAdmin(admin.ModelAdmin):
    list_display = ('client_id', 'name')
    search_fields = ('name',)


class FinancialInline(admin.StackedInline):
    model = Financial
    can_delete = False
    max_num = 1
    extra = 0
    fk_name = 'project'
    fields = (
        # Inputs
        'total_direct_cost', 'gm', 'overhead_dayrate', 'duration_raw', 'duration_with_dt',
        'depreciation', 'taxes', 'file_upload_TMA',
        # Calculated (read-only)
        'total_revenue', 'gp', 'total_overhead',
        'ebitda_amount', 'ebitda_pct',
        'ebit_amount', 'ebit_pct',
        'net_amount', 'net_pct',
        'ebit_day', 'net_day',
    )
    readonly_fields = (
        'total_revenue', 'gp', 'total_overhead',
        'ebitda_amount', 'ebitda_pct',
        'ebit_amount', 'ebit_pct',
        'net_amount', 'net_pct',
        'ebit_day', 'net_day',
    )


class ProjectTechnologyInline(admin.TabularInline):
    model = ProjectTechnology
    extra = 0
    fk_name = 'project'
    fields = ('technology', 'survey_type', 'obn_technique', 'obn_system', 'streamer')


class BidTypeHistoryInline(admin.TabularInline):
    model = BidTypeHistory
    fields = ('previous_bid_type', 'new_bid_type', 'changed_at', 'notes')
    readonly_fields = ('previous_bid_type', 'new_bid_type', 'changed_at', 'notes')
    can_delete = False
    extra = 0
    ordering = ('-changed_at',)
    fk_name = 'project'


class ProjectStatusHistoryInline(admin.TabularInline):
    model = ProjectStatusHistory
    fields = ('previous_status', 'new_status', 'changed_at', 'notes')
    readonly_fields = ('previous_status', 'new_status', 'changed_at', 'notes')
    can_delete = False
    extra = 0
    ordering = ('-changed_at',)
    fk_name = 'project'


class ChangeLogInline(admin.TabularInline):
    model = ChangeLog
    fields = ('change_type', 'field_name', 'previous_value', 'new_value', 'event_date', 'changed_at', 'changed_by', 'notes')
    readonly_fields = ('change_type', 'field_name', 'previous_value', 'new_value', 'event_date', 'changed_at', 'changed_by', 'notes')
    can_delete = False
    extra = 0
    ordering = ('-changed_at',)
    fk_name = 'project'


class ProjectSnapshotInline(admin.TabularInline):
    model = ProjectSnapshot
    fields = ('change_type', 'snapshot_name', 'created_at', 'created_by', 'notes')
    readonly_fields = ('change_type', 'snapshot_name', 'created_at', 'created_by', 'notes')
    can_delete = False
    extra = 0
    ordering = ('-created_at',)
    fk_name = 'project'


class CompetitorInline(admin.TabularInline):
    model = Competitor
    extra = 0
    fk_name = 'project'
    fields = ('name', 'notes', 'created_at', 'created_by')
    readonly_fields = ('created_at', 'created_by')
    ordering = ('-created_at',)


class ProjectContractInline(admin.StackedInline):
    model = ProjectContract
    can_delete = False
    max_num = 1
    extra = 0
    fk_name = 'project'
    fields = ('contract_date', 'actual_start', 'actual_end', 'actual_duration')
    readonly_fields = ('actual_duration',)


@admin.register(Project)
class ProjectAdmin(admin.ModelAdmin):
    list_display = (
        'project_id', 'internal_id', 'project_portal_id', 'name',
        'client', 'country', 'region', 'bid_type', 'status',
        'submission_date', 'award_date', 'lost_date',
    )
    search_fields = ('name', 'internal_id', 'project_portal_id', 'client__name')
    list_filter = ('bid_type', 'status', 'region', 'country')
    readonly_fields = ('internal_id', 'submission_date', 'award_date', 'lost_date')

    def get_inline_instances(self, request, obj=None):
        """
        Show ProjectContractInline only when the project status is 'Won'.
        Show CompetitorInline only when the project status is 'Lost'.
        Always show Financial, ProjectTechnology, BidTypeHistory, StatusHistory, ChangeLog and Snapshot inlines.
        """
        inlines = [FinancialInline, ProjectTechnologyInline, BidTypeHistoryInline, ProjectStatusHistoryInline, ChangeLogInline, ProjectSnapshotInline]
        inline_instances = []
        for inline_class in inlines:
            inline = inline_class(self.model, self.admin_site)
            inline_instances.append(inline)

        # include contract inline only when editing an existing Won project
        if obj and getattr(obj, 'status', None) == 'Won':
            inline_instances.append(ProjectContractInline(self.model, self.admin_site))

        # include competitor inline only when editing an existing Lost project
        if obj and getattr(obj, 'status', None) == 'Lost':
            inline_instances.append(CompetitorInline(self.model, self.admin_site))

        return inline_instances

    def save_model(self, request, obj, form, change):
        """
        Before saving in admin, create snapshots for bid_type/status changes with created_by=request.user.
        After save, attach changed_by to the generated ChangeLog entries (if any).
        """
        prev = None
        try:
            if change and obj.pk:
                prev = Project.objects.get(pk=obj.pk)
        except Project.DoesNotExist:
            prev = None

        # create snapshots (of previous state) with created_by
        if prev:
            try:
                new_bid = form.cleaned_data.get('bid_type', obj.bid_type)
                new_status = form.cleaned_data.get('status', obj.status)

                if prev.bid_type != new_bid:
                    ProjectSnapshot.objects.create(
                        project=obj,
                        change_type='BID',
                        snapshot=_build_snapshot_from_instance(prev),
                        snapshot_name=(prev.internal_id or prev.name),
                        created_by=request.user
                    )

                if prev.status != new_status:
                    ProjectSnapshot.objects.create(
                        project=obj,
                        change_type='STATUS',
                        snapshot=_build_snapshot_from_instance(prev),
                        snapshot_name=(prev.internal_id or prev.name),
                        created_by=request.user
                    )
            except Exception:
                # don't block admin save if snapshot creation fails
                pass

        # proceed with normal save (this triggers model.save and history/changelog creation)
        super().save_model(request, obj, form, change)

        # attempt to update the latest ChangeLog entries to include changed_by for admin actions
        try:
            # status changelog
            if prev and prev.status != obj.status:
                cl = ChangeLog.objects.filter(project=obj, change_type='STATUS', new_value=obj.status, changed_by__isnull=True).order_by('-changed_at').first()
                if cl:
                    cl.changed_by = request.user
                    cl.save(update_fields=['changed_by'])

            # bid changelog
            if prev and prev.bid_type != obj.bid_type:
                cl2 = ChangeLog.objects.filter(project=obj, change_type='BID', new_value=obj.bid_type, changed_by__isnull=True).order_by('-changed_at').first()
                if cl2:
                    cl2.changed_by = request.user
                    cl2.save(update_fields=['changed_by'])
        except Exception:
            # swallow to avoid breaking admin flow
            pass

    def save_formset(self, request, form, formset, change):
        """
        Ensure Competitor.created_by is set to the admin user when created via inline.
        Delegate other formsets to default behavior.
        """
        if formset.model is Competitor:
            # Save instances manually so we can set created_by
            instances = formset.save(commit=False)
            for inst in instances:
                if not inst.created_by:
                    inst.created_by = request.user
                inst.save()
            # handle deletions
            for obj in formset.deleted_objects:
                obj.delete()
            # m2m (not used here) but keep parity
            try:
                formset.save_m2m()
            except Exception:
                pass
        else:
            super().save_formset(request, form, formset, change)


@admin.register(Financial)
class FinancialAdmin(admin.ModelAdmin):
    list_display = ('project', 'total_direct_cost', 'total_revenue', 'gp', 'ebitda_amount', 'net_amount')
    search_fields = ('project__name', 'project__internal_id')
    readonly_fields = (
        'total_revenue', 'gp', 'total_overhead',
        'ebitda_amount', 'ebitda_pct',
        'ebit_amount', 'ebit_pct',
        'net_amount', 'net_pct',
        'ebit_day', 'net_day',
    )
    fieldsets = (
        ('Inputs', {
            'fields': ('project', 'total_direct_cost', 'gm', 'overhead_dayrate', 'duration_raw', 'duration_with_dt', 'depreciation', 'taxes', 'file_upload_TMA')
        }),
        ('Calculated', {
            'fields': ('total_revenue', 'gp', 'total_overhead', 'ebitda_amount', 'ebitda_pct', 'ebit_amount', 'ebit_pct', 'net_amount', 'net_pct', 'ebit_day', 'net_day'),
        }),
    )


@admin.register(ProjectTechnology)
class ProjectTechnologyAdmin(admin.ModelAdmin):
    list_display = ('project', 'technology', 'survey_type', 'obn_system', 'streamer')
    search_fields = ('project__name', 'technology')
    list_filter = ('technology', 'survey_type')


@admin.register(BidTypeHistory)
class BidTypeHistoryAdmin(admin.ModelAdmin):
    list_display = ('project', 'previous_bid_type', 'new_bid_type', 'changed_at')
    readonly_fields = ('previous_bid_type', 'new_bid_type', 'changed_at', 'notes')
    search_fields = ('project__name',)
    list_filter = ('new_bid_type',)


@admin.register(ProjectStatusHistory)
class ProjectStatusHistoryAdmin(admin.ModelAdmin):
    list_display = ('project', 'previous_status', 'new_status', 'changed_at')
    readonly_fields = ('previous_status', 'new_status', 'changed_at', 'notes')
    search_fields = ('project__name',)
    list_filter = ('new_status',)


@admin.register(ChangeLog)
class ChangeLogAdmin(admin.ModelAdmin):
    list_display = ('project', 'change_type', 'field_name', 'previous_value', 'new_value', 'event_date', 'changed_at', 'changed_by')
    readonly_fields = ('project', 'change_type', 'field_name', 'previous_value', 'new_value', 'event_date', 'changed_at', 'changed_by', 'notes')
    search_fields = ('project__name', 'previous_value', 'new_value')
    list_filter = ('change_type',)


@admin.register(ProjectSnapshot)
class ProjectSnapshotAdmin(admin.ModelAdmin):
    list_display = ('project', 'change_type', 'snapshot_name', 'created_at', 'created_by')
    readonly_fields = ('project', 'change_type', 'snapshot', 'snapshot_name', 'created_at', 'created_by', 'notes')
    search_fields = ('project__name', 'snapshot_name')
    list_filter = ('change_type',)


@admin.register(Competitor)
class CompetitorAdmin(admin.ModelAdmin):
    list_display = ('project', 'name', 'created_at', 'created_by')
    readonly_fields = ('created_at', 'created_by')
    search_fields = ('project__name', 'name')
    list_filter = ('created_by',)


@admin.register(ProjectContract)
class ProjectContractAdmin(admin.ModelAdmin):
    list_display = ('project', 'contract_date', 'actual_start', 'actual_end', 'actual_duration')
    search_fields = ('project__name', 'project__internal_id')
    readonly_fields = ('actual_duration',)