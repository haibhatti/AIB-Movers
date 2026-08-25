from django.contrib.auth.forms import UserCreationForm, UserChangeForm
from django import forms
from .models import CustomUser, Passenger

class CustomUserCreationForm(UserCreationForm):
    class Meta:
        model = CustomUser
        fields = ('email', 'phone', 'full_name')

class CustomUserChangeForm(UserChangeForm):
    class Meta:
        model = CustomUser
        fields = ('email', 'phone', 'full_name')

class PassengerForm(forms.ModelForm):
    class Meta:
        model = Passenger
        fields = [
            'full_name', 'cnic', 'gender', 'phone', 'email',
            'address', 'emergency_contact_name', 'emergency_contact_phone', 'blood_group'
        ]
        widgets = {
            'full_name': forms.TextInput(attrs={'class': 'form-control'}),
            'cnic': forms.TextInput(attrs={'class':'form-control', 'placeholder': '35202-1234567-1'}),
            'gender': forms.Select(attrs={'class': 'form-select'}), 
            'phone': forms.TextInput(attrs={'class': 'form-control'}), 
            'email': forms.EmailInput(attrs={'class':'form-control'}), 
            'address': forms.Textarea(attrs={'class':'form-control', 'rows':2}),
            'emergency_contact_name': forms.TextInput(attrs={'class':'form-control'}),
            'emergency_contact_phone': forms.TextInput(attrs={'class':'form-control'}),
            'blood_group': forms.TextInput(attrs={'class':'form-control', 'placeholder': 'e.g. A+'}),
        }
    def __init__(self, *args, **kwargs):
         super().__init__(*args, **kwargs)
         self.fields['address'].required = True
         self.fields['emergency_contact_name'].required = True
         self.fields['emergency_contact_phone'].required = True
         self.fields['blood_group'].required = True
        