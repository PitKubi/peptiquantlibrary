from django.urls import path

from . import admin_api, views

urlpatterns = [
    path('', views.search, name='search'),
    path('peptide/<str:sequence>/', views.peptide_detail, name='peptide_detail'),
    path('selection/', views.selection, name='selection'),
    path('request/', views.quote_request, name='quote_request'),
    path('request/done/', views.request_done, name='request_done'),
    path('about/', views.about, name='about'),

    path('api/admin/health/', admin_api.health),
    path('api/admin/deploy/', admin_api.deploy),
    path('api/admin/sync/', admin_api.sync),
    path('api/admin/audit/', admin_api.audit),
    path('api/admin/annotate/', admin_api.annotate),
    path('api/admin/logs/', admin_api.logs),
    path('api/admin/test-email/', admin_api.test_email),
    path('api/admin/quote-requests/', admin_api.quote_requests),
    path('api/admin/quote-requests/mark-handled/', admin_api.mark_quote_handled),
]
