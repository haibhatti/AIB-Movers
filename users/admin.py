from django.contrib import admin
from django.contrib.auth.admin import UserAdmin
from .models import CustomUser, EmployeeProfile, Passenger
from .forms import CustomUserCreationForm, CustomUserChangeForm

class CustomUserAdmin(UserAdmin):
    add_form = CustomUserCreationForm
    form = CustomUserChangeForm
    model = CustomUser
    list_display = ['email', 'full_name', 'phone', 'is_staff']
    
    readonly_fields = ('date_joined', 'last_login')
    
    fieldsets = (
        (None, {'fields': ('email', 'password')}),
        ('Personal info', {'fields': ('full_name', 'phone')}),
        ('Permissions', {'fields': ('is_active', 'is_staff', 'is_superuser', 'groups', 'user_permissions')}),
   
        ('Important dates', {'fields': ('last_login', 'date_joined')}),
    )
    add_fieldsets = (
        (None, {
            'classes': ('wide',),
           'fields': ('email', 'full_name', 'phone', 'password1', 'password2')}
        ),
    )
    search_fields = ['email', 'full_name', 'phone']
    ordering = ['email']

admin.site.register(CustomUser, CustomUserAdmin)
admin.site.register(EmployeeProfile)
admin.site.register(Passenger)