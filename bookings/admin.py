from django.contrib import admin
from django.core.exceptions import ValidationError

from .models import (
    Location, Bus, Route, RestStop, Trip, TripLog, Ticket, 
    PaymentConfig, PricingConfig, RouteNode, PassengerComplaint, AuditLog
)

admin.site.site_header = "AIB Movers Admin"
admin.site.site_title = "AIB Movers Admin"
admin.site.index_title = "Welcome to AIB Movers Operations"

class RouteNodeInline(admin.TabularInline):
    model = RouteNode
    extra = 1
    fields = ('stop_order', 'location', 'distance_from_origin', 'travel_time_from_origin') 
    ordering = ('stop_order',)

class RestStopInline(admin.TabularInline):
    model = RestStop
    extra = 1
    fields = ('stop_order', 'stop_name')
    ordering = ('stop_order',)

@admin.register(Route)
class RouteAdmin(admin.ModelAdmin):
    list_display = ('route_number', 'origin', 'destination')
    readonly_fields = ('origin', 'destination')
    inlines = [RouteNodeInline, RestStopInline]

@admin.register(Trip)
class TripAdmin(admin.ModelAdmin):
    list_display = ('route', 'date', 'departure_time', 'arrival_time', 'status', 'bus')
    list_filter = ('date', 'status')

@admin.register(Ticket)
class TicketAdmin(admin.ModelAdmin):
    list_display = ('pnr_number', 'get_passenger_name', 'trip', 'seat_number', 'ticket_status', 'payment_status')
    search_fields = ('pnr_number', 'passenger__full_name', 'passenger__cnic')
    list_filter = ('ticket_status', 'payment_status', 'created_at')

    def get_passenger_name(self, obj):
        if obj.passenger:
            return obj.passenger.full_name
        return "No Passenger"
    
    get_passenger_name.short_description = 'Passenger Name'

@admin.register(AuditLog)
class AuditLogAdmin(admin.ModelAdmin):
    list_display = ('timestamp', 'actor', 'action', 'ticket')
    list_filter = ('action', 'timestamp')
    search_fields = ('actor__user__full_name', 'action', 'ticket__pnr_number')
    readonly_fields = ('actor', 'action', 'description', 'ticket', 'timestamp')
    
    def has_delete_permission(self, request, obj=None):
        return False 
admin.site.register(Location)
admin.site.register(Bus)
admin.site.register(TripLog)
admin.site.register(PaymentConfig)
admin.site.register(PricingConfig)
admin.site.register(PassengerComplaint)
