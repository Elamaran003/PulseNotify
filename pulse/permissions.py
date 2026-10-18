from rest_framework.permissions import BasePermission

from .models import UserProfile


class IsAdminUser(BasePermission):
    """Allow only authenticated users whose profile role is ADMIN."""

    def has_permission(self, request, view):
        user = request.user
        if not (user and user.is_authenticated):
            return False
        profile = getattr(user, 'profile', None)
        return profile is not None and profile.role == UserProfile.Role.ADMIN
