import uuid
from django.db import models
from django.core.exceptions import ValidationError
from django.db.models.signals import post_save, post_delete
from django.dispatch import receiver
from datetime import timedelta

from users.models import EmployeeProfile, Passenger 

class Location(models.Model):
    name = models.CharField(max_length=250, unique=True)
    def __str__(self): return self.name

class Route(models.Model):
    route_number = models.CharField(max_length=50, unique=True, help_text="e.g., R-55")
    origin = models.ForeignKey(Location, on_delete=models.CASCADE, related_name='route_starts', null=True, blank=True)
    destination = models.ForeignKey(Location, on_delete=models.CASCADE, related_name='route_ends', null=True, blank=True)
    
    def __str__(self): 
        if self.origin and self.destination:
            return f"[{self.route_number}] {self.origin} → {self.destination}"
        return f"[{self.route_number}] (Nodes Pending)"

from datetime import timedelta 

class RouteNode(models.Model):
    route = models.ForeignKey('Route', on_delete=models.CASCADE, related_name='nodes')
    location = models.ForeignKey('Location', on_delete=models.PROTECT)
    stop_order = models.IntegerField(help_text="1 for Origin, 2 for Mid-point, etc.")
    
    distance_from_origin = models.DecimalField(
        max_digits=7, 
        decimal_places=2, 
        default=0.00, 
        help_text="Distance in KM from the absolute start of the route."
    )
    
    travel_time_from_origin = models.DurationField(
        default=timedelta(hours=0), 
        help_text="Format: HH:MM:SS (e.g., 02:30:00 for 2.5 hours from origin). Includes traffic & rest stops."
    )

    class Meta:
        ordering = ['stop_order']
        constraints = [
            models.UniqueConstraint(fields=['route', 'location'], name='unique_location_per_route'),
            models.UniqueConstraint(fields=['route', 'stop_order'], name='unique_stop_order_per_route'),
        ]

    def __str__(self):
        return f"{self.route.route_number} - {self.location.name} ({self.distance_from_origin} km)"

    def clean(self):
        if self.stop_order == 1:
            if self.distance_from_origin != 0:
                raise ValidationError({'distance_from_origin': "Origin stop (Stop #1) must have a distance of 0.00 km."})
            if self.travel_time_from_origin != timedelta(0):
                raise ValidationError({'travel_time_from_origin': "Origin stop (Stop #1) must have a travel time of 00:00:00."})

        if hasattr(self, 'route') and self.route and self.route.pk:
            previous = RouteNode.objects.filter(
                route=self.route,
                stop_order__lt=self.stop_order
            ).exclude(pk=self.pk).order_by('-stop_order').first()

            if previous:
                if self.distance_from_origin <= previous.distance_from_origin:
                    raise ValidationError({'distance_from_origin': f"Distance must be greater than previous stop ({previous.distance_from_origin} km)."})
                if self.travel_time_from_origin <= previous.travel_time_from_origin:
                    raise ValidationError({'travel_time_from_origin': f"Travel time must be greater than previous stop ({previous.travel_time_from_origin})."})

@receiver([post_save, post_delete], sender=RouteNode)
def update_route_endpoints(sender, instance, **kwargs):
    route = instance.route
    nodes = route.nodes.all().order_by('stop_order')
    
    if nodes.exists():
        route.origin = nodes.first().location
        route.destination = nodes.last().location
    else:
        route.origin = None
        route.destination = None
        
    route.save(update_fields=['origin', 'destination'])


class RestStop(models.Model):
    route = models.ForeignKey(Route, on_delete=models.CASCADE, related_name='rest_stops')
    stop_name = models.CharField(max_length=255)
    stop_order = models.IntegerField()
    class Meta: ordering = ['stop_order']
    def __str__(self): return f"{self.stop_name} (Stop {self.stop_order} on {self.route.route_number})"

class Bus(models.Model):
    bus_name = models.CharField(max_length=250, unique=True)
    total_seats = models.IntegerField(default=40)
    is_active = models.BooleanField(default=True)
    class Meta:
        verbose_name_plural = "Buses"

    def __str__(self):
        return f"{self.bus_name} ({self.total_seats} Seats)"

class Trip(models.Model):
    class TripStatus(models.TextChoices):
        SCHEDULED = 'SCHEDULED', 'Scheduled'
        DELAYED = 'DELAYED', 'Delayed'
        CANCELLED = 'CANCELLED', 'Cancelled'
        COMPLETED = 'COMPLETED', 'Completed'

    bus = models.ForeignKey(Bus, on_delete=models.PROTECT, related_name='trips')
    route = models.ForeignKey(Route, on_delete=models.PROTECT, null=True)
   
    driver = models.ForeignKey(
        'users.EmployeeProfile', 
        on_delete=models.SET_NULL, 
        null=True, 
        blank=True, 
        related_name='trips_driven', 
        limit_choices_to={'role': 'DRIVER'}
    )
    conductor = models.ForeignKey(
        'users.EmployeeProfile', 
        on_delete=models.SET_NULL, 
        null=True, 
        blank=True, 
        related_name='trips_conducted', 
        limit_choices_to={'role': 'CONDUCTOR'}
    )
    date = models.DateField()
    departure_time = models.TimeField()
    
    arrival_time = models.TimeField(null=True, blank=True, editable=False) 
    
    status = models.CharField(max_length=20, choices=TripStatus.choices, default=TripStatus.SCHEDULED)

    def __str__(self):
        return f"{self.route.route_number if self.route else 'No Route'} | {self.date}"

    def clean(self):
        from datetime import datetime
        
        if self.route and self.date and self.departure_time:
            last_node = self.route.nodes.order_by('-stop_order').first()
            if last_node and getattr(last_node, 'travel_time_from_origin', None):
                start_dt = datetime.combine(self.date, self.departure_time)
                end_dt = start_dt + last_node.travel_time_from_origin
                self.arrival_time = end_dt.time()

        if self.date and self.departure_time and self.arrival_time:
            overlapping_trips = Trip.objects.filter(
                date=self.date,
                departure_time__lt=self.arrival_time,
                arrival_time__gt=self.departure_time
            )
            
            if self.pk:
                overlapping_trips = overlapping_trips.exclude(pk=self.pk)
            
            if self.driver and overlapping_trips.filter(driver=self.driver).exists():
                raise ValidationError({
                    'driver': f"Driver {self.driver.user.full_name} is already driving another route during this time."
                })
            
            if self.conductor and overlapping_trips.filter(conductor=self.conductor).exists():
                raise ValidationError({
                    'conductor': f"Conductor {self.conductor.user.full_name} is already assigned to a trip during these hours."
                })

            if self.bus and overlapping_trips.filter(bus=self.bus).exists():
                raise ValidationError({
                    'bus': f"Bus '{self.bus.bus_name}' is already deployed on the road during this time window."
                })
            
class TripLog(models.Model):
    trip = models.ForeignKey(Trip, on_delete=models.CASCADE, related_name='logs')
    logged_by = models.ForeignKey(EmployeeProfile, on_delete=models.SET_NULL, null=True)
    timestamp = models.DateTimeField(auto_now_add=True)
    message = models.CharField(max_length=255)
    class Meta: ordering = ['-timestamp']

class Ticket(models.Model):
    class PaymentStatus(models.TextChoices):
        PENDING_TERMINAL = 'PENDING_TERMINAL', 'Pending Terminal Cash'
        PENDING_ONLINE = 'PENDING_ONLINE', 'Pending Online'
        PAID_TERMINAL = 'PAID_TERMINAL', 'Paid at Terminal'
        PAID_ONLINE = 'PAID_ONLINE', 'Paid Online'
        REFUND_REQUESTED = 'REFUND_REQUESTED', 'Refund Requested'
        REFUNDED = 'REFUNDED', 'Refunded'
        CANCELLED = 'CANCELLED', 'Cancelled'

    class TicketStatus(models.TextChoices):
        PENDING_OTP = 'PENDING_OTP', 'Pending OTP Verification'
        RESERVED = 'RESERVED', 'Reserved (Awaiting Payment)'
        CONFIRMED = 'CONFIRMED', 'Confirmed & Paid'
        CANCELLED_BY_PASSENGER = 'CANCELLED_USER', 'Cancelled by Passenger'
        CANCELLED_BY_COMPANY = 'CANCELLED_COMPANY', 'Cancelled by Company'
        CANCELLED = 'CANCELLED', 'Cancelled'

    trip = models.ForeignKey(Trip, on_delete=models.PROTECT, related_name='tickets')
    origin = models.ForeignKey(Location, on_delete=models.PROTECT, related_name='ticket_origins')
    destination = models.ForeignKey(Location, on_delete=models.PROTECT, related_name='ticket_destinations')
    seat_number = models.IntegerField()
    fare_paid = models.DecimalField(max_digits=10, decimal_places=2)

    passenger = models.ForeignKey(Passenger, on_delete=models.PROTECT, related_name='tickets_booked', null=True)
    booked_by = models.ForeignKey(EmployeeProfile, on_delete=models.SET_NULL, null=True, blank=True, related_name='tickets_sold')

    pnr_number = models.CharField(max_length=8, unique=True, editable=False)
    verification_code = models.CharField(max_length=6, blank=True, null=True)
    is_verified = models.BooleanField(default=False)
    payment_status = models.CharField(max_length=20, choices=PaymentStatus.choices, default=PaymentStatus.PENDING_TERMINAL)
    ticket_status = models.CharField(max_length=20, choices=TicketStatus.choices, default=TicketStatus.PENDING_OTP)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-created_at']
        constraints = [
            models.UniqueConstraint(
                fields=['trip', 'seat_number'],
                condition=~models.Q(ticket_status__in=['CANCELLED_USER', 'CANCELLED_COMPANY', 'CANCELLED']),
                name='unique_active_seat_per_trip'
            )
        ]

    def save(self, *args, **kwargs):
        if not self.pnr_number:
            self.pnr_number = uuid.uuid4().hex[:8].upper()
        super().save(*args, **kwargs)

class PaymentConfig(models.Model):
    payment_option_1 = models.CharField(max_length=100, blank=True, null=True, default="Bank Transfer")
    payment_option_2 = models.CharField(max_length=100, blank=True, null=True)
    
    account_title = models.CharField(max_length=255)
    account_number = models.CharField(max_length=50)
    instructions = models.TextField(blank=True)
    
    def __str__(self):
        return f"Payment Config: {self.account_title}"

class TripExpense(models.Model):
    class ExpenseType(models.TextChoices):
        FUEL = 'FUEL', 'Fuel / Diesel'
        TOLL = 'TOLL', 'Toll Tax'
        MAINTENANCE = 'MAINT', 'Emergency Maintenance'
        MEAL = 'MEAL', 'Staff Meals'
        CHALLAN = 'CHALLAN', 'Route Fines / Challan'
        OTHER = 'OTHER', 'Other / Misc'
    
    trip = models.ForeignKey(Trip, on_delete=models.CASCADE, related_name='expenses')
    logged_by = models.ForeignKey('users.EmployeeProfile', on_delete=models.SET_NULL, null=True)
    expense_type = models.CharField(max_length=15, choices=ExpenseType.choices)
    amount = models.DecimalField(max_digits=10, decimal_places=2)
    description = models.TextField(blank=True, help_text="Optional details or receipt numbers.")
    date_logged = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return f"{self.trip.route.route_number} - {self.expense_type}: Rs. {self.amount}"

class TripTracking(models.Model):
    trip = models.ForeignKey(Trip, on_delete=models.CASCADE, related_name='tracking_logs')
    location_name = models.CharField(max_length=150, help_text="e.g., Bhera Rest Area, Faizabad Interchange")
    timestamp = models.DateTimeField(auto_now_add=True)
    logged_by = models.ForeignKey('users.EmployeeProfile', on_delete=models.SET_NULL, null=True)

    class Meta:
        ordering = ['-timestamp']

    def __str__(self):
        return f"{self.trip.route.route_number} - {self.location_name}"

class PricingConfig(models.Model):
    base_fare = models.DecimalField(max_digits=10, decimal_places=2, default=500.00, help_text="Fixed boarding charge (Rs.)")
    per_km_rate = models.DecimalField(max_digits=10, decimal_places=2, default=5.50, help_text="Rate per kilometer (Rs.)")
    last_updated = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = "Dynamic Pricing Configuration"
        verbose_name_plural = "Dynamic Pricing Configurations"

    def __str__(self):
        return f"Base: Rs.{self.base_fare} | Per KM: Rs.{self.per_km_rate}"


class PassengerComplaint(models.Model):
    ticket = models.ForeignKey('Ticket', on_delete=models.CASCADE, related_name='complaints')
    category = models.CharField(max_length=50, choices=[
        ('MAINTENANCE', 'Seat/Screen/AC Issue'), 
        ('HARASSMENT', 'Harassment/Safety concern'), 
        ('STAFF', 'Staff Behavior'), 
        ('OTHER', 'Other')
    ])
    description = models.TextField()
    is_resolved = models.BooleanField(default=False)
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self): 
        return f"{self.category} - PNR: {self.ticket.pnr_number}"

class AuditLog(models.Model):
    actor = models.ForeignKey(EmployeeProfile, on_delete=models.SET_NULL, null=True, help_text="The employee who performed the action")
    action = models.CharField(max_length=100, help_text="e.g., TICKET_DELETED, REFUND_PROCESSED")
    description = models.TextField()
    ticket = models.ForeignKey(Ticket, on_delete=models.SET_NULL, null=True, blank=True)
    timestamp = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-timestamp']

    def __str__(self):
        return f"{self.timestamp.strftime('%Y-%m-%d %H:%M')} | {self.actor} -> {self.action}"