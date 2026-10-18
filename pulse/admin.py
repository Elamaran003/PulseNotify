from django.contrib import admin

from .models import NotificationLog, PriceAlert, UserProfile

admin.site.register(UserProfile)
admin.site.register(PriceAlert)
admin.site.register(NotificationLog)
