import random
import threading
from django.shortcuts import render, redirect, get_object_or_404
from django.contrib import messages
from django.core.mail import send_mail
from django.conf import settings
from django.contrib.auth import login, logout
from django.contrib.auth.forms import AuthenticationForm
from django.utils import timezone
from django.db import models
from django.db.models import Q
from django.db import transaction, IntegrityError
from .utils import calculate_dynamic_fare, calculate_segment_timing
from .models import Trip, Location, Ticket, PaymentConfig, PassengerComplaint, TripLog
from .forms import RouteSearchForm, TicketForm
from users.models import Passenger, EmployeeProfile, CustomUser
from users.forms import PassengerForm

def generate_otp():
    return str(random.randint(100000, 999999))

def send_booking_confirmation(ticket):
    display_time = ticket.trip.departure_time.strftime('%I:%M %p')

    subject = f"AIB Movers - Booking Confirmed (PNR: {ticket.pnr_number})"
    message = (
        f"Dear {ticket.passenger.full_name},\n\n"
        f"Thank you for choosing AIB Movers. We are pleased to confirm your upcoming journey.\n\n"
        f"--- TRIP DETAILS ---\n"
        f"PNR Number: {ticket.pnr_number}\n"
        f"Route: {ticket.origin} to {ticket.destination}\n"
        f"Date: {ticket.trip.date}\n"
        f"Departure Time: {display_time}\n"
        f"Seat Number: {ticket.seat_number}\n"
        f"Fare Paid: Rs. {ticket.fare_paid}\n\n"
        f"--- BOARDING INSTRUCTIONS ---\n"
        f"Please arrive at the terminal at least 30 minutes before your scheduled departure. "
        f"Have your PNR number and original CNIC ready for verification at the boarding gate.\n\n"
        f"If you require any assistance, please reach out to your dedicated support agent or our terminal help desk.\n\n"
        f"Safe travels,\n"
        f"The AIB Movers Team"
    )
    send_mail(subject, message, settings.DEFAULT_FROM_EMAIL, [ticket.passenger.email], fail_silently=True)

def send_smart_cancellation_email(ticket, reason_text, refund_type=None):
    subject = f"AIB Movers - Reservation Update (PNR: {ticket.pnr_number})"
    message_body = (
        f"Dear {ticket.passenger.full_name},\n\n"
        f"This is a notification from AIB Movers regarding your reservation (PNR: {ticket.pnr_number}).\n\n"
        f"Your ticket has been CANCELLED.\n"
        f"Reason for Cancellation: {reason_text}\n\n"
        f"--- REFUND INFORMATION ---\n"
    )
    
    if refund_type == 'ONLINE_REFUND_QUEUE': 
        message_body += "Your ticket is eligible for an online refund. Please REPLY to this email with your Bank Name and Account Number (IBAN) so our finance team can process your refund.\n\n"
    elif refund_type == 'CASH_REFUND_QUEUE': 
        message_body += "Your ticket is eligible for a cash refund. Please visit our physical terminal desk with your original CNIC to collect your refund.\n\n"
    elif refund_type == 'INSTANT_CASH_REFUNDED':
        message_body += "Your cash refund has already been successfully processed across the terminal counter.\n\n"
    else:
        message_body += "No active payment was found, so no refund is required for this transaction.\n\n"
        
    message_body += (
        f"We apologize for any inconvenience this may cause and hope to serve you again in the future.\n\n"
        f"Regards,\n"
        f"The AIB Movers Team"
    )
    send_mail(subject, message_body, settings.DEFAULT_FROM_EMAIL, [ticket.passenger.email], fail_silently=True)

def auto_assign_agent():
    agents = EmployeeProfile.objects.filter(role=EmployeeProfile.RoleChoices.SUPPORT, is_active=True)
    if not agents.exists(): 
        return None
        
    return agents.annotate(
        workload=models.Count('passenger_set', filter=Q(passenger_set__tickets_booked__trip__status__in=[Trip.TripStatus.SCHEDULED, Trip.TripStatus.DELAYED]))
    ).order_by('workload').first()

def home(request):
    today = timezone.now().date()
    upcoming_trips = Trip.objects.filter(
        status=Trip.TripStatus.SCHEDULED, 
        date__gte=today, 
        route__nodes__isnull=False
    ).distinct().order_by('date', 'departure_time')[:8]
    
    return render(request, 'home.html', {'upcoming_trips': upcoming_trips})

from .utils import calculate_dynamic_fare, calculate_segment_timing


def search_route(request):
    form = RouteSearchForm()
    dynamic_fares = []

    if request.method == 'POST':
        form = RouteSearchForm(request.POST)
        if form.is_valid():
            origin = form.cleaned_data['origin']
            destination = form.cleaned_data['destination']

            request.session['search_origin_id'] = origin.id
            request.session['search_destination_id'] = destination.id

            trips = Trip.objects.filter(
                status=Trip.TripStatus.SCHEDULED
            ).select_related('route', 'bus')

            for trip in trips:
                calculated_price = calculate_dynamic_fare(trip.route, origin, destination)

                if calculated_price > 0:
                    timing = calculate_segment_timing(trip, origin, destination)

                    dynamic_fares.append({
                        'trip': trip,
                        'origin': origin,
                        'destination': destination,
                        'price': calculated_price,
                        'segment_departure_time': timing['dep_time'],
                        'segment_arrival_time': timing['arr_time'],
                        'segment_departure_date': timing['dep_date'],
                        'segment_arrival_date': timing['arr_date'],
                    })

            return render(request, 'search_route.html', {'form': form, 'fares': dynamic_fares})

    return render(request, 'search_route.html', {'form': form})

def book_ticket_step_1(request, trip_id):
    if request.user.is_authenticated and hasattr(request.user, 'employee_profile'):
        if request.user.employee_profile.role not in [
            EmployeeProfile.RoleChoices.ADMIN,
            EmployeeProfile.RoleChoices.BOOKING_AGENT,
        ]:
            messages.error(request, "You are not authorized to create bookings.")
            return redirect('dashboard')

    trip = get_object_or_404(Trip, id=trip_id)
    
    origin_id = request.session.get('search_origin_id')
    dest_id = request.session.get('search_destination_id')

    valid_route_node_ids = list(trip.route.nodes.values_list('location_id', flat=True))

    if origin_id and dest_id and (int(origin_id) in valid_route_node_ids) and (int(dest_id) in valid_route_node_ids):
        origin = get_object_or_404(Location, id=origin_id)
        destination = get_object_or_404(Location, id=dest_id)
    else:
        origin = trip.route.origin
        destination = trip.route.destination
        request.session['search_origin_id'] = origin.id
        request.session['search_destination_id'] = destination.id

    calculated_price = calculate_dynamic_fare(trip.route, origin, destination)
    
    if calculated_price <= 0:
        messages.error(request, f"Pricing not configured for {origin.name} to {destination.name}. Please ensure Route Nodes are mapped in the Admin Panel.")
        return redirect('search_route')

    booked_seats = Ticket.objects.filter(trip=trip).exclude(
        ticket_status__in=[Ticket.TicketStatus.CANCELLED_BY_PASSENGER, Ticket.TicketStatus.CANCELLED_BY_COMPANY, Ticket.TicketStatus.CANCELLED]
    ).values_list('seat_number', flat=True)
    
    current_passenger = getattr(request.user, 'passenger_profile', None) if request.user.is_authenticated else None

    if request.method == 'POST':
        passenger_form = PassengerForm(request.POST, instance=current_passenger)
        ticket_form = TicketForm(request.POST, passenger_gender=request.POST.get('gender'))
        payment_method = request.POST.get('payment_method', 'TERMINAL')

        if passenger_form.is_valid() and ticket_form.is_valid():
            with transaction.atomic():
                seat_number = ticket_form.cleaned_data['seat_number']
                trip_lock = Trip.objects.select_for_update().get(id=trip.id)
                
                seat_taken = Ticket.objects.filter(
                    trip=trip_lock, seat_number=seat_number
                ).exclude(ticket_status__in=[
                    Ticket.TicketStatus.CANCELLED_BY_PASSENGER, 
                    Ticket.TicketStatus.CANCELLED_BY_COMPANY,
                    Ticket.TicketStatus.CANCELLED
                ]).exists()

                if seat_taken:
                    messages.error(request, f"Seat #{seat_number} was just booked by someone else.")
                    return redirect('book_ticket_step_1', trip_id=trip.id)

                passenger = passenger_form.save(commit=False)
                if not passenger.assigned_agent:
                    if request.user.is_authenticated and hasattr(request.user, 'employee_profile'):
                        passenger.assigned_agent = request.user.employee_profile
                    else:
                        passenger.assigned_agent = auto_assign_agent()
                passenger.save()

                try:
                    if request.user.is_authenticated:
                        if hasattr(request.user, 'employee_profile'):
                            is_cash = (payment_method == 'TERMINAL')
                            ticket = Ticket.objects.create(
                                trip=trip, origin=origin, destination=destination, seat_number=seat_number, fare_paid=str(calculated_price), 
                                passenger=passenger, booked_by=request.user.employee_profile, is_verified=True, 
                                ticket_status=Ticket.TicketStatus.CONFIRMED if is_cash else Ticket.TicketStatus.RESERVED, 
                                payment_status=Ticket.PaymentStatus.PAID_TERMINAL if is_cash else Ticket.PaymentStatus.PENDING_ONLINE
                            )
                            messages.success(request, f"Ticket confirmed for {passenger.full_name}!")
                            return redirect('booking_success', pnr=ticket.pnr_number)
                            
                        elif current_passenger:
                            is_cash = (payment_method == 'TERMINAL')
                            ticket = Ticket.objects.create(
                                trip=trip, origin=origin, destination=destination, seat_number=seat_number, fare_paid=str(calculated_price), 
                                passenger=passenger, is_verified=True, ticket_status=Ticket.TicketStatus.RESERVED, 
                                payment_status=Ticket.PaymentStatus.PENDING_TERMINAL if is_cash else Ticket.PaymentStatus.PENDING_ONLINE
                            )
                            messages.success(request, f"Seat #{seat_number} reserved! Please proceed with payment.")
                            return redirect('booking_success', pnr=ticket.pnr_number)
                    else:
                        otp = generate_otp()
                        request.session['temp_booking'] = {
                            'trip_id': trip.id, 'origin_id': origin.id, 'destination_id': destination.id, 
                            'passenger_id': passenger.id, 'seat_number': seat_number, 
                            'fare_paid': str(calculated_price), 'payment_method': payment_method, 'otp': otp
                        }
                        
                        subject = "AIB Movers - Seat Verification Code"
                        email_body = (
                            f"Dear {passenger.full_name},\n\n"
                            f"You are one step away from securing your ticket with AIB Movers.\n\n"
                            f"Your one-time verification code (OTP) is: {otp}\n\n"
                            f"Please enter this code on the website to confirm your seat reservation. "
                            f"For your security, do not share this code with anyone. This code will expire shortly.\n\n"
                            f"Thank you,\n"
                            f"The AIB Movers Team"
                        )
                        send_mail(subject, email_body, settings.DEFAULT_FROM_EMAIL, [passenger.email], fail_silently=False)
                        return redirect('verify_otp_step_2')
                        
                except IntegrityError:
                    messages.error(request, "Seat was just taken. Please choose another.")
                    return redirect('book_ticket_step_1', trip_id=trip.id)
                except Exception:
                    transaction.set_rollback(True)
                    if 'temp_booking' in request.session:
                        del request.session['temp_booking']
                    messages.error(request, "Email delivery failed. Transaction aborted.")
                    return redirect('book_ticket_step_1', trip_id=trip.id)
        else:
            if not current_passenger and 'cnic' in passenger_form.errors:
                messages.error(request, "This CNIC is already registered. Please log in.")
    else:
        passenger_form = PassengerForm(instance=current_passenger)
        ticket_form = TicketForm(initial={'trip': trip})

    context = {
        'trip': trip, 'origin': origin, 'destination': destination, 
        'fare': calculated_price, 'booked_seats': list(booked_seats), 
        'total_seats_range': range(1, trip.bus.total_seats + 1), 
        'payment_config': PaymentConfig.objects.first(), 
        'passenger_form': passenger_form, 'ticket_form': ticket_form
    }

    return render(request, 'book_step_1.html', context)

def verify_otp_step_2(request):
    temp = request.session.get('temp_booking')
    if not temp:
        messages.error(request, "Session expired.")
        return redirect('home')

    passenger = get_object_or_404(Passenger, id=temp['passenger_id'])

    if request.method == 'POST':
        if request.POST.get('otp_code') == temp['otp']:
            with transaction.atomic():
                trip_lock = Trip.objects.select_for_update().get(id=temp['trip_id'])
                seat_taken = Ticket.objects.filter(trip=trip_lock, seat_number=temp['seat_number']).exclude(
                    ticket_status__in=[Ticket.TicketStatus.CANCELLED_BY_PASSENGER, Ticket.TicketStatus.CANCELLED_BY_COMPANY, Ticket.TicketStatus.CANCELLED]
                ).exists()
                
                if seat_taken:
                    messages.error(request, "Seat was just bought by someone else.")
                    del request.session['temp_booking']  
                    return redirect('book_ticket_step_1', trip_id=temp['trip_id'])

                origin = get_object_or_404(Location, id=temp['origin_id'])
                destination = get_object_or_404(Location, id=temp['destination_id'])
                
                try:
                    ticket = Ticket.objects.create(
                        trip=trip_lock, origin=origin, destination=destination, 
                        seat_number=temp['seat_number'], fare_paid=temp['fare_paid'], 
                        passenger=passenger, verification_code=temp['otp'], is_verified=True, 
                        ticket_status=Ticket.TicketStatus.RESERVED, 
                        payment_status=Ticket.PaymentStatus.PENDING_ONLINE if temp.get('payment_method') == 'ONLINE' else Ticket.PaymentStatus.PENDING_TERMINAL
                    )
                    del request.session['temp_booking']
                    return redirect('booking_success', pnr=ticket.pnr_number)
                except IntegrityError:
                    messages.error(request, "Seat was just taken. Please try again.")
                    return redirect('book_ticket_step_1', trip_id=temp['trip_id'])
        else:
            messages.error(request, "Invalid code.")

    return render(request, 'verify_otp.html', {'email': passenger.email})

def booking_success(request, pnr):
    ticket = get_object_or_404(Ticket, pnr_number=pnr)
    return render(request, 'booking_success.html', {'ticket': ticket, 'payment_config': PaymentConfig.objects.first()})

def track_booking(request):
    ticket = None
    if request.method == 'POST':
        pnr = request.POST.get('pnr_number', '').strip().upper()
        cnic = request.POST.get('passenger_cnic', '').strip()
        ticket = Ticket.objects.filter(pnr_number=pnr, passenger__cnic=cnic).first()
        
        if not ticket: 
            messages.error(request, "No reservation found.")
            
    return render(request, 'track_booking.html', {'ticket': ticket})

def cancel_booking_passenger(request, pnr):
    if request.method == 'POST':
        ticket = get_object_or_404(Ticket, pnr_number=pnr)
        
        if ticket.passenger.cnic != request.POST.get('cnic'):
            messages.error(request, "Identity mismatch. You can only cancel your own tickets.")
            return redirect('home')

        if ticket.ticket_status in [Ticket.TicketStatus.RESERVED, Ticket.TicketStatus.CONFIRMED]:
            ticket.ticket_status = Ticket.TicketStatus.CANCELLED_BY_PASSENGER
            
            if ticket.payment_status == Ticket.PaymentStatus.PAID_ONLINE: 
                ticket.payment_status = Ticket.PaymentStatus.REFUND_REQUESTED
                refund_mode = 'ONLINE_REFUND_QUEUE'
            elif ticket.payment_status == Ticket.PaymentStatus.PAID_TERMINAL: 
                ticket.payment_status = Ticket.PaymentStatus.REFUND_REQUESTED
                refund_mode = 'CASH_REFUND_QUEUE'
            else: 
                ticket.payment_status = Ticket.PaymentStatus.CANCELLED
                refund_mode = None
                
            ticket.save()
            send_smart_cancellation_email(ticket, "Cancelled from tracking portal.", refund_type=refund_mode)
            messages.success(request, "Reservation cancelled.")
            
    return redirect('home')

def user_login(request):
    if request.method == 'POST':
        form = AuthenticationForm(request, data=request.POST)
        if form.is_valid():
            login(request, form.get_user())
            return redirect('dashboard') 
    return render(request, 'userlogin.html', {'form': AuthenticationForm()})

def passenger_login(request):
    if request.method == 'POST':
        cnic = request.POST.get('cnic')
        passenger = Passenger.objects.filter(cnic=cnic).first()
        if passenger and passenger.email:
            otp = generate_otp()
            request.session['login_otp'] = otp
            request.session['login_cnic'] = cnic
            
            subject = "AIB Movers - Account Access"
            email_body = (
                f"Dear {passenger.full_name},\n\n"
                f"We received a request to access your AIB Movers passenger dashboard.\n\n"
                f"Your secure login code (OTP) is: {otp}\n\n"
                f"Please enter this code to access your travel history, upcoming trips, and support details. "
                f"If you did not request this, please ignore this email.\n\n"
                f"Regards,\n"
                f"The AIB Movers Team"
            )
            
            send_mail(subject, email_body, settings.DEFAULT_FROM_EMAIL, [passenger.email], fail_silently=True)
            messages.success(request, f"OTP sent to {passenger.email}")
            return redirect('passenger_verify_login')
            
        messages.error(request, "No passenger found with this CNIC.")
        
    return render(request, 'passenger_login.html')

def passenger_verify_login(request):
    if request.method == 'POST':
        entered_otp = request.POST.get('otp_code')
        if entered_otp == request.session.get('login_otp'):
            passenger = Passenger.objects.get(cnic=request.session.get('login_cnic'))
            if not passenger.user:
                passenger.user = CustomUser.objects.create_user(email=passenger.email, password=generate_otp(), full_name=passenger.full_name)
                passenger.save()
                
            login(request, passenger.user)
            del request.session['login_otp']
            messages.success(request, f"Welcome back, {passenger.full_name}!")
            return redirect('passenger_profile')
            
        messages.error(request, "Invalid Code.")
        
    return render(request, 'passenger_verify_login.html')

def user_logout(request):
    logout(request)
    return redirect('home')

def passenger_support(request):
    if request.method == 'POST':
        identifier = request.POST.get('identifier', '').strip().upper()
        
        ticket = None
        if len(identifier) == 8 and identifier.isalnum():
            ticket = Ticket.objects.filter(
                pnr_number=identifier, 
                trip__status__in=[Trip.TripStatus.SCHEDULED, Trip.TripStatus.DELAYED]
            ).first()
            
        if not ticket:
            ticket = Ticket.objects.filter(
                passenger__cnic=identifier, 
                trip__status__in=[Trip.TripStatus.SCHEDULED, Trip.TripStatus.DELAYED]
            ).order_by('trip__date', 'trip__departure_time').first()

        if not ticket:
            messages.error(request, "Request Failed: No active journey found for this PNR or CNIC.")
            return redirect('passenger_support')

        if 'submit_complaint' in request.POST:
            description = request.POST.get('description', '').strip()
            category = request.POST.get('category', '').strip()
            
            PassengerComplaint.objects.create(
                ticket=ticket,
                category=category,
                description=description
            )
            messages.success(request, "Complaint registered successfully. Our support team is reviewing it.")
            return redirect('passenger_support')

        elif 'trigger_sos' in request.POST:
            sos_description = request.POST.get('sos_description', '').strip()
            sos_message = f" PASSENGER SOS: {sos_description} | PNR: {ticket.pnr_number} | CNIC: {ticket.passenger.cnic}"
            
            TripLog.objects.create(trip=ticket.trip, message=sos_message)

            def send_passenger_sos():
                admin_emails = list(EmployeeProfile.objects.filter(role=EmployeeProfile.RoleChoices.ADMIN).values_list('user__email', flat=True))
                if admin_emails:
                    send_mail(
                        f"CRITICAL SOS - Trip #{ticket.trip.id}", 
                        sos_message, 
                        settings.DEFAULT_FROM_EMAIL, 
                        admin_emails, 
                        fail_silently=True
                    )
            threading.Thread(target=send_passenger_sos).start()
            messages.warning(request, "SOS TRANSMITTED. TERMINAL HQ HAS BEEN NOTIFIED.")
            return redirect('passenger_support')

    return render(request, 'passenger_support.html')