import random

from django.contrib.auth import authenticate
from django.db.models import Count, Q
from django.http import Http404, JsonResponse
from django.shortcuts import get_object_or_404
from django.views.decorators.http import require_GET
from rest_framework import generics, status
from rest_framework.response import Response
from rest_framework.views import APIView
from rest_framework_simplejwt.tokens import RefreshToken

from .models import NotificationLog, PriceAlert
from .permissions import IsAdminUser
from .serializers import LoginSerializer, PriceAlertSerializer, RegisterSerializer


def _access_token_for(user):
    return str(RefreshToken.for_user(user).access_token)


# ---------------------------------------------------------------- 5.1 Register
class RegisterView(APIView):
    authentication_classes = []
    permission_classes = []

    def post(self, request):
        serializer = RegisterSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)          # 400 on duplicate username
        user = serializer.save()
        return Response(
            {
                'username': user.username,
                'access': _access_token_for(user),
                'role': user.profile.role,
            },
            status=status.HTTP_201_CREATED,
        )


# ------------------------------------------------------------------ 5.2 Login
class LoginView(APIView):
    authentication_classes = []
    permission_classes = []

    def post(self, request):
        serializer = LoginSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        user = authenticate(
            username=serializer.validated_data['username'],
            password=serializer.validated_data['password'],
        )
        if user is None:
            return Response({'detail': 'Invalid credentials.'}, status=status.HTTP_401_UNAUTHORIZED)
        return Response(
            {
                'username': user.username,
                'access': _access_token_for(user),
                'role': user.profile.role,
            },
            status=status.HTTP_200_OK,
        )


# ------------------------------------------------ 5.3 / 5.4  POST + GET alerts
class AlertListCreateView(generics.ListCreateAPIView):
    serializer_class = PriceAlertSerializer

    def get_queryset(self):
        # Scoped to the logged-in user: nobody ever sees another user's alerts.
        return PriceAlert.objects.filter(user=self.request.user).order_by('-created_at')

    def perform_create(self, serializer):
        serializer.save(user=self.request.user)   # user from token, never from body


# ------------------------------------------------------- 5.5 DELETE alert
class AlertDeactivateView(APIView):
    def delete(self, request, id):
        alert = get_object_or_404(PriceAlert, id=id)
        if alert.user != request.user:
            raise Http404   # never reveal that someone else's alert exists
        alert.status = PriceAlert.Status.INACTIVE
        alert.save(update_fields=['status'])
        return Response({'status': 'inactive'}, status=status.HTTP_200_OK)


# ------------------------------------------------------ 5.6 Mock price feed
MOCK_PRICES = {
    'DEL-BOM': (3000, 7000),
    'BLR-HYD': (1500, 4000),
    'DEL-BLR': (4000, 9000),
    'BOM-GOA': (2000, 5000),
}


@require_GET
def get_flight_price(request):
    """Plain Django view: no authentication, called internally by Celery."""
    route = request.GET.get('route', '').strip().upper()
    price_range = MOCK_PRICES.get(route)
    if not price_range:
        return JsonResponse({'error': 'Route not found'}, status=404)
    price = random.randint(*price_range)
    return JsonResponse({'route': route, 'price': price})


# ----------------------------------------------------- 5.7 Admin summary
class AdminSummaryView(APIView):
    permission_classes = [IsAdminUser]

    def get(self, request):
        alert_stats = PriceAlert.objects.aggregate(
            total_alerts=Count('id'),
            active_alerts=Count('id', filter=Q(status=PriceAlert.Status.ACTIVE)),
            triggered_alerts=Count('id', filter=Q(status=PriceAlert.Status.TRIGGERED)),
        )
        total_notifications = NotificationLog.objects.aggregate(Count('id'))['id__count']

        top_routes = (
            PriceAlert.objects.values('origin', 'destination')
            .annotate(alert_count=Count('id'))
            .order_by('-alert_count', 'origin', 'destination')[:5]
        )

        return Response({
            **alert_stats,
            'total_notifications': total_notifications,
            'top_routes': [
                {'route': f"{r['origin']}-{r['destination']}", 'alert_count': r['alert_count']}
                for r in top_routes   # formatting only; counting done in the DB
            ],
        })
