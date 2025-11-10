from django.contrib import admin
from .models import (
    Client, Location, Project, Opportunity, 
    BidMetrics, Equipment, Milestone, CycleTime
)

@admin.register(Client)
class ClientAdmin(admin.ModelAdmin):
    list_display = ['client_id', 'name']
    search_fields = ['name']

@admin.register(Location)
class LocationAdmin(admin.ModelAdmin):
    list_display = ['country_id', 'country_name', 'region_id', 'water_depth_min', 'water_depth_max']
    search_fields = ['country_name', 'region_id']
    list_filter = ['region_id']

@admin.register(Project)
class ProjectAdmin(admin.ModelAdmin):
    list_display = ['project_id', 'name', 'country', 'region_id']
    search_fields = ['name']
    list_filter = ['country', 'region_id']

class BidMetricsInline(admin.StackedInline):
    model = BidMetrics
    can_delete = False

class EquipmentInline(admin.TabularInline):
    model = Equipment
    extra = 1

class MilestoneInline(admin.StackedInline):
    model = Milestone
    can_delete = False

class CycleTimeInline(admin.StackedInline):
    model = CycleTime
    can_delete = False

@admin.register(Opportunity)
class OpportunityAdmin(admin.ModelAdmin):
    list_display = ['unique_op_id', 'client', 'project', 'bid_status', 'bid_type', 'country']
    search_fields = ['unique_op_id', 'client__name', 'project__name']
    list_filter = ['bid_status', 'bid_type', 'country']
    inlines = [BidMetricsInline, EquipmentInline, MilestoneInline, CycleTimeInline]

@admin.register(BidMetrics)
class BidMetricsAdmin(admin.ModelAdmin):
    list_display = ['opportunity', 'revenue', 'ebit', 'gp', 'gm', 'duration']
    search_fields = ['opportunity__unique_op_id']

@admin.register(Equipment)
class EquipmentAdmin(admin.ModelAdmin):
    list_display = ['id', 'opportunity', 'node_type', 'node_qty', 'vessel_name']
    search_fields = ['opportunity__unique_op_id', 'node_type', 'vessel_name']
    list_filter = ['node_type']

@admin.register(Milestone)
class MilestoneAdmin(admin.ModelAdmin):
    list_display = ['opportunity', 'date_received', 'date_deadline', 'date_award', 'actual_start', 'actual_end']
    search_fields = ['opportunity__unique_op_id']
    list_filter = ['date_received', 'date_award']

@admin.register(CycleTime)
class CycleTimeAdmin(admin.ModelAdmin):
    list_display = ['opportunity', 'rec_to_submission', 'submission_to_award', 'award_to_start', 'total_cycle']
    search_fields = ['opportunity__unique_op_id']
