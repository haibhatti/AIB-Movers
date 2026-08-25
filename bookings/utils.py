from datetime import datetime, timedelta
from decimal import Decimal
from django.core.exceptions import ObjectDoesNotExist
from .models import PricingConfig, RouteNode, AuditLog

def calculate_dynamic_fare(route, start_location, end_location):
    try:
        config = PricingConfig.objects.first()
        if not config:
            return Decimal('0.00') 

        start_node = RouteNode.objects.get(route=route, location=start_location)
        end_node = RouteNode.objects.get(route=route, location=end_location)

        if start_node.stop_order >= end_node.stop_order:
            return Decimal('0.00')

        distance_travelled = abs(end_node.distance_from_origin - start_node.distance_from_origin)
        total_fare = config.base_fare + (distance_travelled * config.per_km_rate)
        
        return total_fare.quantize(Decimal('0.00'))

    except ObjectDoesNotExist:
        return Decimal('0.00')


def log_system_action(actor, action, description, ticket=None):
    from .models import AuditLog
    if actor:
        AuditLog.objects.create(
            actor=actor,
            action=action,
            description=description,
            ticket=ticket
        )

def calculate_segment_timing(trip, start_location, end_location):
    from datetime import datetime
    from django.core.exceptions import ObjectDoesNotExist
    
    try:
        start_node = RouteNode.objects.get(route=trip.route, location=start_location)
        end_node = RouteNode.objects.get(route=trip.route, location=end_location)

        start_dt = datetime.combine(trip.date, trip.departure_time)

        segment_dep_dt = start_dt + start_node.travel_time_from_origin
        segment_arr_dt = start_dt + end_node.travel_time_from_origin

        return {
            'dep_time': segment_dep_dt.strftime('%I:%M %p'),
            'arr_time': segment_arr_dt.strftime('%I:%M %p'),
            'dep_date': segment_dep_dt.strftime('%b %d, %Y'),
            'arr_date': segment_arr_dt.strftime('%b %d, %Y'),
        }
    except ObjectDoesNotExist:
        return {
            'dep_time': trip.departure_time.strftime('%I:%M %p'),
            'arr_time': trip.arrival_time.strftime('%I:%M %p') if trip.arrival_time else '--',
            'dep_date': trip.date.strftime('%b %d, %Y'),
            'arr_date': trip.date.strftime('%b %d, %Y'),
        }