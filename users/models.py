from django.db import models
from django.contrib.auth.models import AbstractBaseUser, BaseUserManager, PermissionsMixin

# Create your models here.

#Custom User Manager
class CustomUserManager(BaseUserManager):
    def create_user(self,email,password=None,**extra_fields):
        if not email:
            raise ValueError('Email field must be set')

        email = self.normalize_email(email)
        user = self.model(email=email,**extra_fields)

        user.set_password(password)

        user.save(using=self.db)
        return user

    def create_superuser(self,email,password=None,**extra_fields):
        extra_fields.setdefault('is_staff',True)
        extra_fields.setdefault('is_active',True)
        extra_fields.setdefault('is_superuser',True)

        return self.create_user(email, password, **extra_fields)


#Custom User Model
class CustomUser(AbstractBaseUser,PermissionsMixin):
        email = models.EmailField(unique=True)
        phone = models.CharField(max_length=20,unique=True,null=True,blank=True)
        full_name = models.CharField(max_length=255)

        is_staff = models.BooleanField(default=False)
        is_active = models.BooleanField(default=True)
        date_joined = models.DateTimeField(auto_now_add=True)

        objects = CustomUserManager()

        USERNAME_FIELD = 'email'
        REQUIRED_FIELDS = ['full_name']

        def __str__(self):
             return f"{self.full_name} ({self.email})"


#Profiles
class EmployeeProfile(models.Model):
     user = models.OneToOneField(CustomUser, on_delete=models.CASCADE, related_name='employee_profile')

     class RoleChoices(models.TextChoices):
          BOOKING_AGENT = 'AGENT', 'Booking Agent'
          DRIVER = 'DRIVER', 'Bus Driver'
          CONDUCTOR = 'CONDUCTOR', 'Bus Conductor'
          ADMIN = 'ADMIN', 'Terminal Manager'
          SUPPORT = 'SUPPORT', "Support Agent"
          


     role = models.CharField(max_length=20, choices=RoleChoices.choices, default = RoleChoices.BOOKING_AGENT)
     cnic = models.CharField(max_length=15, unique=True)
     
     

     is_active = models.BooleanField(default=True)

     def __str__(self):
          return f"{self.user.full_name} - {self.get_role_display()}"


class Passenger(models.Model):
     class GenderChoices(models.TextChoices):
          MALE = 'M', 'Male'
          FEMALE = 'F', 'Female'

     user = models.OneToOneField(CustomUser, on_delete=models.SET_NULL, null = True, blank=True, related_name='passenger_profile')
     assigned_agent = models.ForeignKey(EmployeeProfile, on_delete=models.SET_NULL,null=True, blank=True)

     cnic = models.CharField(max_length=15,unique=True)
     full_name = models.CharField(max_length=255)
     gender = models.CharField(max_length=1,choices=GenderChoices.choices) 
     phone = models.CharField(max_length=20)
     email = models.EmailField(blank=True, null=True)
     address = models.TextField(blank=True)
     emergency_contact_name = models.CharField(max_length=255)
     emergency_contact_phone = models.CharField(max_length=20)
     blood_group = models.CharField(max_length=5, blank=True, null=True)

     def __str__(self):
        return f"{self.full_name} ({self.gender})"