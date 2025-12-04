from django.urls import path
from . import views

app_name = 'market_analysis'

urlpatterns = [
    path('', views.dashboard, name='dashboard'),
    path('projects/', views.project_opportunities, name='project_opportunities'),
    path('projects/add/', views.create_project, name='project_add'),
    path('projects/<int:project_id>/edit/', views.edit_project, name='project_edit'),
    path('projects/<int:project_id>/technology/add/', views.add_technology, name='project_add_technology'),
    path('projects/<int:project_id>/financial/', views.add_or_edit_financial, name='project_add_financial'),
    path('projects/<int:project_id>/contract/', views.update_contract, name='project_contract'),  # contract modal endpoint
    path('projects/<int:project_id>/scope/', views.manage_scope, name='project_scope'),  # new scope view
]
