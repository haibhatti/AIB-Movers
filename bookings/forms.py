from django import forms
from .models import Ticket, Location

class RouteSearchForm(forms.Form):
    origin = forms.ModelChoiceField(
        queryset=Location.objects.all(),
        widget=forms.Select(attrs={'class': 'form-select'}) 
    )
    destination = forms.ModelChoiceField(
        queryset=Location.objects.all(),
        widget=forms.Select(attrs={'class': 'form-select'})
    )

class TicketForm(forms.ModelForm):
    class Meta:
        model = Ticket
        fields = ['trip', 'seat_number']
        widgets = {
            'trip': forms.Select(attrs={'class': 'form-select'}), 
            'seat_number': forms.NumberInput(attrs={'class':'form-control', 'min':1}),
        }

    def __init__(self, *args, **kwargs):
        self.Passenger_gender = kwargs.pop('passenger_gender', None)
        super().__init__(*args, **kwargs)

    def clean(self):
        cleaned_data = super().clean()
        trip = cleaned_data.get('trip')
        seat_number = cleaned_data.get('seat_number')

        if not self.Passenger_gender:
            raise forms.ValidationError("Gender must be provided due to the seating rules")

        if trip and seat_number:
            existing_ticket = Ticket.objects.filter(
                trip=trip, seat_number=seat_number
            ).exclude(
                ticket_status__in=[
                    Ticket.TicketStatus.CANCELLED_BY_PASSENGER,
                    Ticket.TicketStatus.CANCELLED_BY_COMPANY,
                    Ticket.TicketStatus.CANCELLED
                ]
            )
            
            
            if self.instance and self.instance.pk:
                existing_ticket = existing_ticket.exclude(pk=self.instance.pk)

            if existing_ticket.exists():
                raise forms.ValidationError(f"Seat #{seat_number} is already taken")

            if seat_number % 2 == 0:
                adjacent_seat_num = seat_number - 1
            else:
                adjacent_seat_num = seat_number + 1

            adjacent_ticket = Ticket.objects.filter(
                trip=trip, seat_number=adjacent_seat_num
            ).exclude(
                ticket_status__in=[
                    Ticket.TicketStatus.CANCELLED_BY_PASSENGER,
                    Ticket.TicketStatus.CANCELLED_BY_COMPANY,
                    Ticket.TicketStatus.CANCELLED
                ]
            ).select_related('passenger').first()

            if adjacent_ticket and adjacent_ticket.passenger:
                adj_gender = adjacent_ticket.passenger.gender
                if adj_gender != self.Passenger_gender:
                    gender_label = "Female" if adj_gender == 'F' else "Male"
                    raise forms.ValidationError(f"Seating Rule Constraint! Seat #{adjacent_seat_num} is occupied by a {gender_label} passenger. Adjacent Seats must be reserved by passengers of same gender")

        return cleaned_data