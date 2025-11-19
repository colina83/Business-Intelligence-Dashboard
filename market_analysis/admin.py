from django.contrib import admin
from .models import (
    Client, Project, BidTypeHistory, ProjectTechnology, Financial,
    ProjectStatusHistory, ProjectContract, ChangeLog
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
        'total_direct_cost', 'gm', 'duration_raw', 'duration_with_dt',
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
        Always show Financial, ProjectTechnology, BidTypeHistory, StatusHistory and ChangeLog inlines.
        """
        inlines = [FinancialInline, ProjectTechnologyInline, BidTypeHistoryInline, ProjectStatusHistoryInline, ChangeLogInline]
        inline_instances = []
        for inline_class in inlines:
            inline = inline_class(self.model, self.admin_site)
            inline_instances.append(inline)

        # include contract inline only when editing an existing Won project
        if obj and getattr(obj, 'status', None) == 'Won':
            inline_instances.append(ProjectContractInline(self.model, self.admin_site))

        return inline_instances


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
            'fields': ('project', 'total_direct_cost', 'gm', 'duration_raw', 'duration_with_dt', 'depreciation', 'taxes', 'file_upload_TMA')
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


@admin.register(ProjectContract)
class ProjectContractAdmin(admin.ModelAdmin):
    list_display = ('project', 'contract_date', 'actual_start', 'actual_end', 'actual_duration')
    search_fields = ('project__name', 'project__internal_id')
    readonly_fields = ('actual_duration',)