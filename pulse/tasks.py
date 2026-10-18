import logging

import requests
from celery import shared_task
from django.conf import settings

from .models import NotificationLog, PriceAlert

logger = logging.getLogger(__name__)


@shared_task
def check_prices():
    """Runs every 60s via Celery Beat. Fetch price per active route and fire
    send_notification.delay() for every alert whose threshold is met."""
    active_alerts = PriceAlert.objects.filter(status=PriceAlert.Status.ACTIVE)
    routes = active_alerts.values_list('origin', 'destination').distinct()

    for origin, destination in routes:
        route = f'{origin}-{destination}'

        try:
            response = requests.get(settings.PRICE_FEED_URL, params={'route': route}, timeout=5)
        except requests.RequestException as exc:
            logger.warning('Price feed unreachable for %s: %s', route, exc)
            continue

        if response.status_code != 200:
            continue
        current_price = response.json().get('price')
        if current_price is None:
            continue

        route_alerts = active_alerts.filter(origin=origin, destination=destination)
        for alert in route_alerts:
            if current_price <= float(alert.threshold_price):
                send_notification.delay(alert.id, current_price)   # async, never blocks


@shared_task
def send_notification(alert_id, triggered_price):
    try:
        alert = PriceAlert.objects.get(id=alert_id)
    except PriceAlert.DoesNotExist:
        return
    if alert.status != PriceAlert.Status.ACTIVE:
        return  # deactivated / already triggered between scheduling and execution

    message = (
        f'Price alert triggered! {alert.origin}-{alert.destination} '
        f'is now ₹{triggered_price} - below your threshold of ₹{alert.threshold_price}'
    )
    NotificationLog.objects.create(alert=alert, triggered_price=triggered_price, message=message)

    # Mark as triggered so it doesn't fire again
    alert.status = PriceAlert.Status.TRIGGERED
    alert.save(update_fields=['status'])
