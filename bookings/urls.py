from django.urls import path
from bookings import views as booking_views
from dashboards import views as dashboard_views

urlpatterns = [
    # Public (Routed to bookings app)
    path('', booking_views.home, name='home'),
    path('search/', booking_views.search_route, name='search_route'),
    path('book/<int:trip_id>/', booking_views.book_ticket_step_1, name='book_ticket_step_1'),
    path('verify-otp/', booking_views.verify_otp_step_2, name='verify_otp_step_2'),
    path('success/<str:pnr>/', booking_views.booking_success, name='booking_success'),
    path('track/', booking_views.track_booking, name='track_booking'),
    path('cancel/<str:pnr>/', booking_views.cancel_booking_passenger, name='cancel_booking_passenger'),

    path('helpdesk/', booking_views.passenger_support, name='passenger_support'),
    
    # Login & Logout (Routed to bookings app)
    path('employee/login/', booking_views.user_login, name='login'),
    path('employee/logout/', booking_views.user_logout, name='logout'),
    path('passenger/login/', booking_views.passenger_login, name='passenger_login'),
    path('passenger/verify/', booking_views.passenger_verify_login, name='passenger_verify_login'),

    # Enterprise Dashboards (Routed to the new dashboards app!)
    path('employee/dashboard/', dashboard_views.dashboard_view, name='dashboard'),
    path('employee/bookings/', dashboard_views.view_bookings, name='view_bookings'),
    path('employee/edit/<int:ticket_id>/', dashboard_views.edit_ticket, name='edit_ticket'),
    path('employee/delete/<int:ticket_id>/', dashboard_views.delete_ticket, name='delete_ticket'),
    path('profile/', dashboard_views.passenger_profile, name='passenger_profile'),

    # Role-Specific Dashboards
    path('dashboard/admin/', dashboard_views.admin_dashboard, name='admin_dashboard'),
    path('dashboard/conductor/', dashboard_views.conductor_dashboard, name='conductor_dashboard'),
    path('dashboard/support/', dashboard_views.support_dashboard, name='support_dashboard'),
    path('dashboard/agent/', dashboard_views.agent_dashboard, name='agent_dashboard'),
    path('dashboard/driver/', dashboard_views.driver_dashboard, name='driver_dashboard'),

    # Admin sub-pages
    path('dashboard/admin/staff/', dashboard_views.staff_roster, name='staff_roster'),
    path('dashboard/admin/staff/<int:emp_id>/', dashboard_views.employee_performance, name='employee_performance'),
    path('dashboard/admin/fleet/', dashboard_views.fleet_management, name='fleet_management'),
    path('dashboard/conductor/trip/<int:trip_id>/log/', dashboard_views.create_trip_log, name='create_trip_log'),

    path('complaints/', dashboard_views.manage_complaints, name='manage_complaints'),
]