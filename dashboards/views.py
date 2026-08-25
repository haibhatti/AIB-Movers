import threading
from datetime import timedelta
from django.shortcuts import render, redirect, get_object_or_404
from django.contrib.auth.decorators import login_required
from django.contrib import messages
from django.core.mail import send_mail
from django.conf import settings
from django.utils import timezone
from django.db import models
from django.db.models import Sum, Count, Q, Max
from django.core.exceptions import ValidationError

from bookings.models import Trip, Ticket, TripExpense, TripTracking, Bus, Route, TripLog, PassengerComplaint
from users.models import Passenger, EmployeeProfile
from bookings.forms import TicketForm
from users.forms import PassengerForm
from bookings.views import auto_assign_agent, send_booking_confirmation, send_smart_cancellation_email
from bookings.utils import log_system_action  

@login_required(login_url='/employee/login/')
def dashboard_view(request):
    if hasattr(request.user, 'employee_profile'):
        role = request.user.employee_profile.role
        if role == EmployeeProfile.RoleChoices.ADMIN:
            return redirect('admin_dashboard')
        elif role == EmployeeProfile.RoleChoices.CONDUCTOR:
            return redirect('conductor_dashboard')
        elif role == EmployeeProfile.RoleChoices.DRIVER:
            return redirect('driver_dashboard')
        elif role == EmployeeProfile.RoleChoices.SUPPORT:
            return redirect('support_dashboard')
        else:
            return redirect('agent_dashboard')
            
    elif hasattr(request.user, 'passenger_profile'):
        return redirect('passenger_profile')
        
    return redirect('home')

@login_required(login_url='/employee/login/')
def passenger_profile(request):
    try:
        passenger = request.user.passenger_profile
    except Passenger.DoesNotExist:
        return redirect('home')

    if passenger.assigned_agent and not passenger.assigned_agent.is_active:
        passenger.assigned_agent = auto_assign_agent()
        passenger.save()

    if request.method == 'POST':
        passenger.blood_group = request.POST.get('blood_group')
        passenger.emergency_contact_name = request.POST.get('emergency_contact_name')
        passenger.emergency_contact_phone = request.POST.get('emergency_contact_phone')
        passenger.address = request.POST.get('address')
        passenger.save()
        messages.success(request, "Your medical and emergency details have been updated.")
        return redirect('passenger_profile')

    my_tickets = Ticket.objects.filter(passenger=passenger).order_by('-id')
    
    cancelled_statuses = [
        Ticket.TicketStatus.CANCELLED_BY_PASSENGER,
        Ticket.TicketStatus.CANCELLED_BY_COMPANY,
        Ticket.TicketStatus.CANCELLED
    ]
    
    completed_trips = my_tickets.filter(
        trip__status=Trip.TripStatus.COMPLETED
    ).exclude(ticket_status__in=cancelled_statuses).count()
    
    upcoming_trips = my_tickets.filter(
        trip__status__in=[Trip.TripStatus.SCHEDULED, Trip.TripStatus.DELAYED]
    ).exclude(ticket_status__in=cancelled_statuses).count()
    
    amount_spent = my_tickets.filter(
        payment_status__in=[Ticket.PaymentStatus.PAID_ONLINE, Ticket.PaymentStatus.PAID_TERMINAL]
    ).aggregate(Sum('fare_paid'))['fare_paid__sum'] or 0

    context = {
        'passenger': passenger,
        'my_tickets': my_tickets,
        'completed_trips': completed_trips,
        'upcoming_trips': upcoming_trips,
        'amount_spent': amount_spent,
    }

    return render(request, 'passenger_profile.html', context )

@login_required(login_url='/employee/login/')
def admin_dashboard(request):
    if request.user.employee_profile.role != EmployeeProfile.RoleChoices.ADMIN:
        return redirect('dashboard')
        
    time_filter = request.GET.get('time_filter', 'all')
    now = timezone.now()
    ticket_qs = Ticket.objects.filter(
        payment_status__in=[Ticket.PaymentStatus.PAID_ONLINE, Ticket.PaymentStatus.PAID_TERMINAL]
    )
    expense_qs = TripExpense.objects.all()
    
    if time_filter == 'weekly':
        start_date = now - timedelta(days=now.weekday())
        ticket_qs = ticket_qs.filter(trip__date__gte=start_date.date())
        expense_qs = expense_qs.filter(date_logged__gte=start_date)
    elif time_filter == 'monthly':
        start_date = now.replace(day=1)
        ticket_qs = ticket_qs.filter(trip__date__gte=start_date.date())
        expense_qs = expense_qs.filter(date_logged__gte=start_date)
    elif time_filter == 'yearly':
        start_date = now.replace(month=1, day=1)
        ticket_qs = ticket_qs.filter(trip__date__gte=start_date.date())
        expense_qs = expense_qs.filter(date_logged__gte=start_date)

    total_rev = ticket_qs.aggregate(total=Sum('fare_paid'))['total'] or 0
    total_exp = expense_qs.aggregate(total=Sum('amount'))['total'] or 0
    
    chart_data = [['Route', 'Gross Revenue', 'Expenses', 'Net Profit']]
    
    for route in Route.objects.all():
        trips = Trip.objects.filter(route=route)
        r_rev = ticket_qs.filter(trip__in=trips).aggregate(Sum('fare_paid'))['fare_paid__sum'] or 0
        r_exp = expense_qs.filter(trip__in=trips).aggregate(Sum('amount'))['amount__sum'] or 0
        
        if r_rev > 0 or r_exp > 0:
            chart_data.append([route.route_number, float(r_rev), float(r_exp), float(r_rev) - float(r_exp)])

    if len(chart_data) == 1:
        chart_data.append(['No Activity', 0, 0, 0])

    context = {
        'total_revenue': total_rev,
        'total_expenses': total_exp,
        'net_profit': float(total_rev) - float(total_exp),
        'chart_data': chart_data,
        'employee_stats': EmployeeProfile.objects.all().select_related('user').order_by('role'),
        'current_filter': time_filter,
        'active_complaints': PassengerComplaint.objects.filter(is_resolved=False).order_by('-created_at')[:15]
    }
        
    return render(request, 'admin_dashboard.html', context)

@login_required(login_url='/employee/login/')
def fleet_management(request):
    if request.user.employee_profile.role != EmployeeProfile.RoleChoices.ADMIN:
        return redirect('dashboard')

    if request.method == 'POST' and 'reassign_bus' in request.POST:
        target_trip = get_object_or_404(Trip, id=request.POST.get('trip_id'))
        new_bus = get_object_or_404(Bus, id=request.POST.get('new_bus_id'))
        
        if not new_bus.is_active:
            messages.error(request, f"Reassignment Failed: Bus '{new_bus.bus_name}' is currently marked inactive.")
            return redirect('fleet_management')

        cancelled_statuses = [
            Ticket.TicketStatus.CANCELLED_BY_PASSENGER,
            Ticket.TicketStatus.CANCELLED_BY_COMPANY,
            Ticket.TicketStatus.CANCELLED
        ]
        active_tickets = Ticket.objects.filter(trip=target_trip).exclude(ticket_status__in=cancelled_statuses)
        highest_seat_booked = active_tickets.aggregate(Max('seat_number'))['seat_number__max'] or 0

        if new_bus.total_seats < active_tickets.count():
            messages.error(request, "Reassignment Failed: New bus capacity cannot accommodate current headcount.")
        elif new_bus.total_seats < highest_seat_booked:
            messages.error(request, f"Reassignment Failed: A passenger holds Seat #{highest_seat_booked}, exceeding new bus capacity.")
        else:
            target_trip.bus = new_bus
            try:
                target_trip.full_clean()
                target_trip.save()
                
                # AUDIT LOG
                log_system_action(request.user.employee_profile, "BUS_REASSIGNED", f"Trip #{target_trip.id} reassigned to {new_bus.bus_name}")
                
                passenger_emails = list(active_tickets.filter(
                    ticket_status__in=[Ticket.TicketStatus.CONFIRMED, Ticket.TicketStatus.RESERVED]
                ).values_list('passenger__email', flat=True))
                
                if passenger_emails:
                    subject = f"AIB Movers - Fleet Update: Trip #{target_trip.id}"
                    email_body = (
                        f"Dear Passenger,\n\n"
                        f"This is a notice regarding your upcoming journey with AIB Movers.\n\n"
                        f"--- FLEET REASSIGNMENT NOTICE ---\n"
                        f"Trip ID: #{target_trip.id}\n"
                        f"Route: {target_trip.route.origin} to {target_trip.route.destination}\n"
                        f"Date: {target_trip.date}\n"
                        f"New Assigned Fleet: {new_bus.bus_name}\n\n"
                        f"Your seat assignment and schedule remain unchanged. "
                        f"Please arrive at the terminal at least 30 minutes prior to departure.\n\n"
                        f"Regards,\n"
                        f"AIB Movers Fleet Operations"
                    )
                    for email in passenger_emails:
                        if email:
                            send_mail(subject, email_body, settings.DEFAULT_FROM_EMAIL, [email], fail_silently=True)
                            
                messages.success(request, f"Trip #{target_trip.id} reassigned to {new_bus.bus_name} successfully.")
            except ValidationError as e:
                error_message = list(e.message_dict.values())[0][0] if hasattr(e, 'message_dict') else e.messages[0]
                messages.error(request, f"Schedule Conflict: {error_message}")
        
        return redirect('fleet_management')

    if request.method == 'POST' and 'update_bus' in request.POST:
        bus = get_object_or_404(Bus, id=request.POST.get('bus_id'))
        bus.bus_name = request.POST.get('bus_name')
        new_is_active = (request.POST.get('is_active') == 'on')
        
        if not new_is_active:
            active_trips = Trip.objects.filter(
                bus=bus,
                status__in=[Trip.TripStatus.SCHEDULED, Trip.TripStatus.DELAYED]
            )
            if active_trips.exists():
                messages.error(
                    request,
                    f"Cannot deactivate {bus.bus_name}. It is assigned to {active_trips.count()} upcoming/active trip(s). "
                    f"Reassign those trips first."
                )
                return redirect('fleet_management')
        
        bus.is_active = new_is_active
        bus.save()
        log_system_action(request.user.employee_profile, "FLEET_STATUS_UPDATED", f"{bus.bus_name} marked as {'Active' if new_is_active else 'Inactive'}")
        messages.success(request, f"Fleet status for {bus.bus_name} updated.")
        return redirect('fleet_management')

    route_stats = {}
    for route in Route.objects.all():
        rev = Ticket.objects.filter(
            trip__route=route, 
            payment_status__in=[Ticket.PaymentStatus.PAID_ONLINE, Ticket.PaymentStatus.PAID_TERMINAL]
        ).aggregate(Sum('fare_paid'))['fare_paid__sum'] or 0
        
        exp = TripExpense.objects.filter(trip__route=route).aggregate(Sum('amount'))['amount__sum'] or 0
        route_stats[route.id] = {
            'revenue': float(rev),
            'expense': float(exp),
            'profit': float(rev) - float(exp)
        }

    active_trips = Trip.objects.filter(
        status__in=[Trip.TripStatus.SCHEDULED, Trip.TripStatus.DELAYED]
    ).order_by('date', 'departure_time')
    
    for trip in active_trips:
        stats = route_stats.get(trip.route.id, {'revenue': 0, 'expense': 0, 'profit': 0})
        trip.historical_revenue = stats['revenue']
        trip.historical_expense = stats['expense']
        trip.historical_profit = stats['profit']

    context = {
        'active_trips': active_trips,
        'available_buses': Bus.objects.filter(is_active=True),
        'all_buses': Bus.objects.all().order_by('-is_active', 'bus_name'),
        'live_tracking': TripTracking.objects.all().select_related('trip', 'trip__route', 'logged_by').order_by('-timestamp')[:30]
    }

    return render(request, 'fleet_management.html', context)

@login_required(login_url='/employee/login/')
def staff_roster(request):
    if request.user.employee_profile.role != EmployeeProfile.RoleChoices.ADMIN:
        return redirect('dashboard')
        
    employees = EmployeeProfile.objects.annotate(
        total_tickets=Count('tickets_sold'),
        revenue=Sum(
            'tickets_sold__fare_paid', 
            filter=Q(tickets_sold__payment_status__in=[Ticket.PaymentStatus.PAID_ONLINE, Ticket.PaymentStatus.PAID_TERMINAL])
        )
    ).order_by('role')
    
    return render(request, 'staff_roster.html', {'employees': employees})

@login_required(login_url='/employee/login/')
def employee_performance(request, emp_id):
    if request.user.employee_profile.role != EmployeeProfile.RoleChoices.ADMIN:
        return redirect('dashboard')
        
    target_emp = get_object_or_404(EmployeeProfile, id=emp_id)
    context = {'target_emp': target_emp}
    cancelled_statuses = [
        Ticket.TicketStatus.CANCELLED_BY_PASSENGER,
        Ticket.TicketStatus.CANCELLED_BY_COMPANY,
        Ticket.TicketStatus.CANCELLED
    ]
    
    if target_emp.role == EmployeeProfile.RoleChoices.BOOKING_AGENT:
        context['cancelled_count'] = Ticket.objects.filter(
            booked_by=target_emp, 
            ticket_status__in=cancelled_statuses
        ).count()
        context['sales_count'] = Ticket.objects.filter(booked_by=target_emp).count()
        context['revenue'] = Ticket.objects.filter(
            booked_by=target_emp, 
            payment_status__in=[Ticket.PaymentStatus.PAID_ONLINE, Ticket.PaymentStatus.PAID_TERMINAL]
        ).aggregate(Sum('fare_paid'))['fare_paid__sum'] or 0
        
        context['assigned_passengers'] = Passenger.objects.filter(assigned_agent=target_emp).order_by('-id')
        # [NEW] Admin can now see the exact tickets this agent sold
        context['recent_sales'] = Ticket.objects.filter(booked_by=target_emp).order_by('-created_at')[:15]
        
    elif target_emp.role == EmployeeProfile.RoleChoices.CONDUCTOR:
        context['trips_conducted'] = Trip.objects.filter(
            conductor=target_emp, 
            status=Trip.TripStatus.COMPLETED
        ).count()
        context['total_expense_amount'] = TripExpense.objects.filter(
            logged_by=target_emp
        ).aggregate(Sum('amount'))['amount__sum'] or 0
        context['logged_expenses'] = TripExpense.objects.filter(
            logged_by=target_emp
        ).order_by('-date_logged')[:10]
        context['logged_pings'] = TripTracking.objects.filter(logged_by=target_emp).order_by('-timestamp')[:15]
        
    elif target_emp.role == EmployeeProfile.RoleChoices.DRIVER:
        context['trips_driven'] = Trip.objects.filter(
            driver=target_emp, 
            status=Trip.TripStatus.COMPLETED
        ).count()
        upcoming = Trip.objects.filter(
            driver=target_emp, 
            status__in=[Trip.TripStatus.SCHEDULED, Trip.TripStatus.DELAYED]
        ).order_by('date')
        context['upcoming_trips'] = upcoming[:5]
        context['upcoming_trips_count'] = upcoming.count()
        
        # [NEW] Admin can actually see the list of completed trips
        context['completed_trips_list'] = Trip.objects.filter(
            driver=target_emp, 
            status=Trip.TripStatus.COMPLETED
        ).order_by('-date', '-departure_time')[:15]
        
    elif target_emp.role == EmployeeProfile.RoleChoices.SUPPORT:
        context['assigned_passengers'] = Passenger.objects.filter(assigned_agent=target_emp).order_by('-id')
        context['assigned_cases'] = context['assigned_passengers'].count()

    elif target_emp.role == EmployeeProfile.RoleChoices.ADMIN:
        context['total_revenue'] = Ticket.objects.filter(
            payment_status__in=[Ticket.PaymentStatus.PAID_ONLINE, Ticket.PaymentStatus.PAID_TERMINAL]
        ).aggregate(Sum('fare_paid'))['fare_paid__sum'] or 0
        context['total_expenses'] = TripExpense.objects.aggregate(Sum('amount'))['amount__sum'] or 0
        context['net_profit'] = float(context['total_revenue']) - float(context['total_expenses'])
        context['total_tickets'] = Ticket.objects.count()
        context['total_cancelled'] = Ticket.objects.filter(
            ticket_status__in=cancelled_statuses
        ).count()
        context['total_employees'] = EmployeeProfile.objects.count()
        context['total_active_trips'] = Trip.objects.filter(
            status__in=[Trip.TripStatus.SCHEDULED, Trip.TripStatus.DELAYED]
        ).count()
        
    from bookings.models import AuditLog
    context['recent_activity'] = AuditLog.objects.filter(actor=target_emp)[:30]

    return render(request, 'employee_performance.html', context)

@login_required(login_url='/employee/login/')
def driver_dashboard(request):
    if request.user.employee_profile.role != EmployeeProfile.RoleChoices.DRIVER:
        return redirect('dashboard')

    upcoming = Trip.objects.filter(
        driver=request.user.employee_profile, 
        status__in=[Trip.TripStatus.SCHEDULED, Trip.TripStatus.DELAYED]
    ).order_by('date', 'departure_time')
    
    completed = Trip.objects.filter(
        driver=request.user.employee_profile, 
        status=Trip.TripStatus.COMPLETED
    ).order_by('-date', '-departure_time')

    context = {
        'upcoming_trips': upcoming,
        'upcoming_trips_count': upcoming.count(),
        'completed_trips_count': completed.count(),
        'completed_trips_list': completed[:15]
    }
        
    return render(request, 'driver_dashboard.html', context)

@login_required(login_url='/employee/login/')
def conductor_dashboard(request):
    employee = request.user.employee_profile
    if employee.role != EmployeeProfile.RoleChoices.CONDUCTOR:
        return redirect('dashboard')

    if request.method == 'POST':
        if 'add_expense' in request.POST:
            target_trip = get_object_or_404(Trip, id=request.POST.get('trip_id'))
            if target_trip.conductor != employee:
                messages.error(request, "Security Alert: You are not authorized to log expenses for this trip.")
                return redirect('conductor_dashboard')
                
            TripExpense.objects.create(
                trip=target_trip,
                logged_by=employee,
                expense_type=request.POST.get('expense_type'),
                amount=request.POST.get('amount'),
                description=request.POST.get('description')
            )
            messages.success(request, "Operational expense logged successfully.")
            return redirect('conductor_dashboard')
            
        elif 'add_tracking' in request.POST:
            target_trip = get_object_or_404(Trip, id=request.POST.get('track_trip_id'))
            if target_trip.conductor != employee:
                messages.error(request, "Security Alert: You are not authorized to update location for this trip.")
                return redirect('conductor_dashboard')
                
            TripTracking.objects.create(
                trip=target_trip,
                location_name=request.POST.get('location_name'),
                logged_by=employee
            )
            messages.success(request, "Live GPS location ping updated.")
            return redirect('conductor_dashboard')
        
    context = {
        'active_trips': Trip.objects.filter(
            conductor=employee, 
            status__in=[Trip.TripStatus.SCHEDULED, Trip.TripStatus.DELAYED]
        ).order_by('date', 'departure_time'),
        'expense_types': TripExpense.ExpenseType.choices,
        'my_pings': TripTracking.objects.filter(logged_by=employee).order_by('-timestamp')[:10],
        'my_expenses': TripExpense.objects.filter(logged_by=employee).order_by('-date_logged')[:10] 
    }
    return render(request, 'conductor_dashboard.html', context)

@login_required(login_url='/employee/login/')
def support_dashboard(request):
    if request.user.employee_profile.role != EmployeeProfile.RoleChoices.SUPPORT:
        return redirect('dashboard')
        
    qs = Passenger.objects.filter(assigned_agent=request.user.employee_profile).order_by('-id')
    live_tracking = TripTracking.objects.all().select_related(
        'trip', 'trip__route', 'logged_by'
    ).order_by('-timestamp')[:15]

    context =  {
        'assigned_passengers': qs,
        'active_cases': qs.count(),
        'live_tracking': live_tracking
    }
    
    return render(request, 'support_dashboard.html',context)

@login_required(login_url='/employee/login/')
def agent_dashboard(request):
    if request.user.employee_profile.role != EmployeeProfile.RoleChoices.BOOKING_AGENT:
        return redirect('dashboard')
        
    my_tickets = Ticket.objects.filter(booked_by=request.user.employee_profile)
    
    assigned_passengers = Passenger.objects.filter(assigned_agent=request.user.employee_profile).order_by('-id')
    
    context = {
        'total_sales': my_tickets.count(),
        'revenue_generated': my_tickets.filter(
            payment_status__in=[Ticket.PaymentStatus.PAID_TERMINAL, Ticket.PaymentStatus.PAID_ONLINE]
        ).aggregate(Sum('fare_paid'))['fare_paid__sum'] or 0,
        'assigned_passengers': assigned_passengers, 
        'active_cases': assigned_passengers.count(),
    }
    return render(request, 'agent_dashboard.html', context)
@login_required(login_url='/employee/login/')
def view_bookings(request):
    allowed_roles = [
        EmployeeProfile.RoleChoices.ADMIN, 
        EmployeeProfile.RoleChoices.BOOKING_AGENT, 
        EmployeeProfile.RoleChoices.SUPPORT
    ]
    
    if not hasattr(request.user, 'employee_profile') or request.user.employee_profile.role not in allowed_roles:
        return redirect('dashboard')
        
    if request.method == 'POST':
        ticket_id = request.POST.get('ticket_id')
        action = request.POST.get('action')
        
        if ticket_id and action:
            if request.user.employee_profile.role not in allowed_roles:
                messages.error(request, "Security Alert: Unauthorized access.")
                return redirect('view_bookings')

            t = get_object_or_404(Ticket, id=ticket_id)
            cancelled_statuses = [
                Ticket.TicketStatus.CANCELLED_BY_PASSENGER, 
                Ticket.TicketStatus.CANCELLED_BY_COMPANY, 
                Ticket.TicketStatus.CANCELLED
            ]
            
            if action in ['mark_paid', 'mark_paid_online']:
                if t.ticket_status in cancelled_statuses:
                    messages.error(request, "Action Blocked: This ticket is cancelled.")
                else:
                    t.payment_status = (
                        Ticket.PaymentStatus.PAID_TERMINAL if action == 'mark_paid' 
                        else Ticket.PaymentStatus.PAID_ONLINE
                    )
                    t.ticket_status = Ticket.TicketStatus.CONFIRMED
                    t.save()
                    
                    log_action_name = "PAYMENT_COLLECTED_TERMINAL" if action == "mark_paid" else "PAYMENT_VERIFIED_ONLINE"
                    log_system_action(request.user.employee_profile, log_action_name, f"Approved payment for PNR {t.pnr_number}", ticket=t)
                    
                    send_booking_confirmation(t)
                    messages.success(request, f"Payment confirmed for Ticket #{t.pnr_number}.")

            elif action == 'process_refund':
                t.payment_status = Ticket.PaymentStatus.REFUNDED
                t.save()
                
                log_system_action(request.user.employee_profile, "REFUND_PROCESSED", f"Processed refund for PNR {t.pnr_number}", ticket=t)
                
                send_smart_cancellation_email(t, "Refund approved and processed by staff.", refund_type='INSTANT_CASH_REFUNDED')
                messages.success(request, f"Refund completed for Ticket #{t.pnr_number}.")
                
        return redirect('view_bookings')

    query = request.GET.get('q', '').strip()
    status_filter = request.GET.get('status')
    
    if not status_filter:
        status_filter = 'all' if query else 'active'
    
    tickets = Ticket.objects.all().order_by('-id')
    cancelled_statuses = [
        Ticket.TicketStatus.CANCELLED_BY_PASSENGER, 
        Ticket.TicketStatus.CANCELLED_BY_COMPANY, 
        Ticket.TicketStatus.CANCELLED
    ]
    
    if query:
        tickets = tickets.filter(
            models.Q(pnr_number__icontains=query) | 
            models.Q(passenger__cnic__icontains=query) | 
            models.Q(passenger__full_name__icontains=query) | 
            models.Q(passenger__phone__icontains=query)
        )

    if status_filter == 'active':
        tickets = tickets.exclude(ticket_status__in=cancelled_statuses)
    elif status_filter == 'cancelled':
        tickets = tickets.filter(ticket_status__in=cancelled_statuses)
    elif status_filter == 'pending_payment':
        tickets = tickets.filter(
            payment_status__in=[Ticket.PaymentStatus.PENDING_TERMINAL, Ticket.PaymentStatus.PENDING_ONLINE]
        ).exclude(ticket_status__in=cancelled_statuses)
    elif status_filter == 'refund_requested':
        tickets = tickets.filter(payment_status=Ticket.PaymentStatus.REFUND_REQUESTED)

    context = {
        'tickets': tickets, 
        'query': query, 
        'status_filter': status_filter
    }

    return render(request, 'booking_list.html', context)

@login_required(login_url='/employee/login/')
def edit_ticket(request, ticket_id):
    ticket = get_object_or_404(Ticket, id=ticket_id)
    allowed_roles = [
        EmployeeProfile.RoleChoices.ADMIN, 
        EmployeeProfile.RoleChoices.BOOKING_AGENT, 
        EmployeeProfile.RoleChoices.SUPPORT
    ]
    
    if request.user.employee_profile.role not in allowed_roles:
        messages.error(request, "Security Alert: You do not have permission to edit tickets.")
        return redirect('view_bookings')

    cancelled_statuses = [
        Ticket.TicketStatus.CANCELLED_BY_PASSENGER, 
        Ticket.TicketStatus.CANCELLED_BY_COMPANY, 
        Ticket.TicketStatus.CANCELLED
    ]
    
    if ticket.ticket_status in cancelled_statuses:
        messages.error(request, "Alert: Cancelled records cannot be modified!")
        return redirect('view_bookings')
        
    if request.method == 'POST':
        ticket_form = TicketForm(request.POST, instance=ticket, passenger_gender=request.POST.get('gender', ticket.passenger.gender))
        passenger_form = PassengerForm(request.POST, instance=ticket.passenger)
        
        if ticket_form.is_valid() and passenger_form.is_valid():
            ticket_form.save()
            passenger_form.save()
            
            log_system_action(request.user.employee_profile, "TICKET_EDITED", f"Modified details for PNR {ticket.pnr_number}", ticket=ticket)
            
            messages.success(request, f"Reservation details for #{ticket.pnr_number} updated.")
            return redirect('view_bookings')
    else:
        ticket_form = TicketForm(instance=ticket, passenger_gender=ticket.passenger.gender)
        passenger_form = PassengerForm(instance=ticket.passenger)

    context = {
        'ticket': ticket, 
        'ticket_form': ticket_form, 
        'passenger_form': passenger_form
    }
        
    return render(request, 'edit_ticket.html', context)

@login_required(login_url='/employee/login/')
def delete_ticket(request, ticket_id):
    ticket = get_object_or_404(Ticket, id=ticket_id)
    allowed_roles = [
        EmployeeProfile.RoleChoices.ADMIN, 
        EmployeeProfile.RoleChoices.BOOKING_AGENT, 
        EmployeeProfile.RoleChoices.SUPPORT
    ]
    
    if request.user.employee_profile.role not in allowed_roles:
        messages.error(request, "Security Alert: You do not have permission to cancel tickets.")
        return redirect('view_bookings')

    if request.method == 'POST':
        ticket.ticket_status = Ticket.TicketStatus.CANCELLED_BY_COMPANY
        
        if ticket.payment_status == Ticket.PaymentStatus.PAID_ONLINE:
            ticket.payment_status = Ticket.PaymentStatus.REFUND_REQUESTED
            refund_mode = 'ONLINE_REFUND_QUEUE'
        elif ticket.payment_status == Ticket.PaymentStatus.PAID_TERMINAL:
            ticket.payment_status = Ticket.PaymentStatus.REFUNDED
            refund_mode = 'INSTANT_CASH_REFUNDED'
        else:
            ticket.payment_status = Ticket.PaymentStatus.CANCELLED
            refund_mode = None
            
        ticket.save()
        
        # AUDIT LOG
        log_system_action(request.user.employee_profile, "TICKET_CANCELLED", f"Company cancelled PNR {ticket.pnr_number}", ticket=ticket)
        
        send_smart_cancellation_email(ticket, "Trip cancelled by terminal management.", refund_type=refund_mode)
        messages.success(request, f"PNR {ticket.pnr_number} has been cancelled.")
        return redirect('view_bookings')
        
    return render(request, 'delete_ticket.html', {'ticket': ticket})

@login_required(login_url='/employee/login/')
def create_trip_log(request, trip_id):
    trip = get_object_or_404(Trip, id=trip_id)
    try:
        employee = request.user.employee_profile
    except EmployeeProfile.DoesNotExist:
        return redirect('home')

    if employee.role not in [EmployeeProfile.RoleChoices.CONDUCTOR, EmployeeProfile.RoleChoices.ADMIN]:
        messages.error(request, "Unauthorized access.")
        return redirect('dashboard')
        
    if employee.role == EmployeeProfile.RoleChoices.CONDUCTOR and trip.conductor != employee:
        messages.error(request, "Security Alert: You are not authorized to log incidents for this trip.")
        return redirect('dashboard')

    if request.method == 'POST':
        message = request.POST.get('message')
        is_critical = (request.POST.get('is_critical') == 'on')
        
        TripLog.objects.create(trip=trip, logged_by=employee, message=message)
        
        # AUDIT LOG
        log_system_action(employee, "INCIDENT_LOGGED", f"Logged {'CRITICAL' if is_critical else 'ROUTINE'} incident for Trip #{trip.id}")
        
        def send_admin_alert():
            admin_emails = list(EmployeeProfile.objects.filter(
                role=EmployeeProfile.RoleChoices.ADMIN
            ).values_list('user__email', flat=True))
            
            if admin_emails:
                subject = f"AIB Movers - Incident Alert: Route {trip.route.route_number}"
                email_body = (
                    f"Operational Incident Notice\n\n"
                    f"A new log has been recorded by Conductor {employee.user.full_name or employee.user.email}.\n\n"
                    f"--- INCIDENT DETAILS ---\n"
                    f"Trip ID: #{trip.id}\n"
                    f"Route: {trip.route.origin} to {trip.route.destination} ({trip.route.route_number})\n"
                    f"Date: {trip.date} at {trip.departure_time.strftime('%I:%M %p')}\n"
                    f"Priority: {'CRITICAL / EMERGENCY' if is_critical else 'ROUTINE INCIDENT'}\n\n"
                    f"Report:\n{message}\n\n"
                    f"Timestamp: {timezone.now().strftime('%Y-%m-%d %I:%M %p')}\n"
                )
                send_mail(subject, email_body, settings.DEFAULT_FROM_EMAIL, admin_emails, fail_silently=True)

        threading.Thread(target=send_admin_alert).start()

        if is_critical:
            passenger_emails = list(Ticket.objects.filter(
                trip=trip, 
                ticket_status=Ticket.TicketStatus.CONFIRMED
            ).values_list('passenger__email', flat=True))
            
            if passenger_emails:
                subject = f"URGENT TRAVEL ALERT: AIB Movers Trip #{trip.id}"
                body = (
                    f"Dear Passenger,\n\n"
                    f"This is an urgent operational notice regarding your scheduled AIB Movers journey "
                    f"from {trip.route.origin} to {trip.route.destination}.\n\n"
                    f"--- TRAVEL ADVISORY ---\n"
                    f"{message}\n\n"
                    f"Our terminal operations team is actively addressing the situation. "
                    f"For real-time assistance, please consult your dedicated support agent on your dashboard.\n\n"
                    f"We apologize for the inconvenience.\n\n"
                    f"AIB Movers Operations Control"
                )
                for email in passenger_emails:
                    if email:
                        send_mail(subject, body, settings.DEFAULT_FROM_EMAIL, [email], fail_silently=True)
                        
            messages.warning(request, "Critical alert broadcasted to all passengers.")
        else:
            messages.success(request, "Incident recorded.")
            
        return redirect('dashboard')
        
    return render(request, 'create_trip_log.html', {'trip': trip})

def send_complaint_resolution_email(complaint):
    """Notifies the passenger when their complaint has been investigated and resolved."""
    if complaint.ticket and complaint.ticket.passenger and complaint.ticket.passenger.email:
        subject = f"AIB Movers - Complaint Resolved (PNR: {complaint.ticket.pnr_number})"
        message = (
            f"Dear {complaint.ticket.passenger.full_name},\n\n"
            f"This is an official update from AIB Movers Operations Control regarding your recent feedback.\n\n"
            f"--- CASE DETAILS ---\n"
            f"PNR Reference: {complaint.ticket.pnr_number}\n"
            f"Category: {complaint.get_category_display()}\n"
            f"Status: RESOLVED\n\n"
            f"Our terminal operations and customer experience team has reviewed your report and taken the necessary corrective actions.\n\n"
            f"We appreciate your feedback in helping us maintain our service quality standards.\n\n"
            f"Best Regards,\n"
            f"AIB Movers Customer Experience Team"
        )
        send_mail(subject, message, settings.DEFAULT_FROM_EMAIL, [complaint.ticket.passenger.email], fail_silently=True)


@login_required(login_url='/employee/login/')
def manage_complaints(request):
    """Dedicated in-app manifest to review, filter, and resolve passenger feedback."""
    allowed_roles = [
        EmployeeProfile.RoleChoices.ADMIN
    ]
    if not hasattr(request.user, 'employee_profile') or request.user.employee_profile.role not in allowed_roles:
        return redirect('dashboard')

    if request.method == 'POST' and 'resolve_complaint' in request.POST:
        complaint_id = request.POST.get('complaint_id')
        complaint = get_object_or_404(PassengerComplaint, id=complaint_id)
        complaint.is_resolved = True
        complaint.save()

        log_system_action(
            actor=request.user.employee_profile,
            action="COMPLAINT_RESOLVED",
            description=f"Resolved complaint #{complaint.id} ({complaint.get_category_display()}) for PNR {complaint.ticket.pnr_number}",
            ticket=complaint.ticket
        )

        send_complaint_resolution_email(complaint)
        messages.success(request, f"Complaint for PNR #{complaint.ticket.pnr_number} marked as resolved. Confirmation emailed to passenger.")
        
        return redirect(request.META.get('HTTP_REFERER', 'manage_complaints'))

    query = request.GET.get('q', '').strip()
    status_filter = request.GET.get('status', 'pending')

    complaints = PassengerComplaint.objects.all().select_related(
        'ticket', 'ticket__passenger', 'ticket__trip', 'ticket__trip__route'
    ).order_by('-created_at')

    if status_filter == 'pending':
        complaints = complaints.filter(is_resolved=False)
    elif status_filter == 'resolved':
        complaints = complaints.filter(is_resolved=True)

    if query:
        complaints = complaints.filter(
            models.Q(ticket__pnr_number__icontains=query) |
            models.Q(ticket__passenger__full_name__icontains=query) |
            models.Q(ticket__passenger__cnic__icontains=query) |
            models.Q(description__icontains=query)
        )

    context = {
        'complaints': complaints,
        'query': query,
        'status_filter': status_filter,
        'pending_count': PassengerComplaint.objects.filter(is_resolved=False).count(),
        'resolved_count': PassengerComplaint.objects.filter(is_resolved=True).count(),
    }
    return render(request, 'complaint_list.html', context)