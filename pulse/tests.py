from decimal import Decimal
from unittest.mock import MagicMock, patch

from django.contrib.auth.models import User
from django.test import TestCase
from rest_framework.test import APIClient

from . import tasks
from .models import NotificationLog, PriceAlert, UserProfile


# ---------------------------------------------------------------- Required 1
class PriceThresholdTest(TestCase):
    def test_price_below_threshold_triggers_alert(self):
        self.assertTrue(4200 <= 4500)

    def test_price_above_threshold_does_not_trigger(self):
        self.assertFalse(5000 <= 4500)

    def test_price_equal_to_threshold_triggers_alert(self):
        self.assertTrue(4500 <= 4500)


# ---------------------------------------------------------------- Required 2
class NotificationLogTest(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username='testuser', password='testpass')
        self.alert = PriceAlert.objects.create(
            user=self.user, origin='DEL', destination='BOM',
            threshold_price=4500, status=PriceAlert.Status.ACTIVE,
        )

    def test_notification_log_created_with_correct_message(self):
        log = NotificationLog.objects.create(
            alert=self.alert, triggered_price=4200,
            message='Price dropped to 4200 for DEL-BOM',
        )
        self.assertEqual(log.triggered_price, 4200)
        self.assertEqual(log.alert, self.alert)
        self.assertIn('DEL-BOM', log.message)


# ---------------------------------------------------------------- Required 3
class AlertScopingTest(TestCase):
    def setUp(self):
        self.user1 = User.objects.create_user(username='user1', password='pass')
        self.user2 = User.objects.create_user(username='user2', password='pass')
        PriceAlert.objects.create(user=self.user1, origin='DEL', destination='BOM', threshold_price=4500)
        PriceAlert.objects.create(user=self.user2, origin='BLR', destination='HYD', threshold_price=2000)

    def test_user_only_sees_own_alerts(self):
        user1_alerts = PriceAlert.objects.filter(user=self.user1)
        self.assertEqual(user1_alerts.count(), 1)
        self.assertEqual(user1_alerts.first().origin, 'DEL')

    def test_user_cannot_see_other_users_alerts(self):
        user1_alerts = PriceAlert.objects.filter(user=self.user1)
        self.assertNotIn('BLR', [a.origin for a in user1_alerts])


# ------------------------------------------------- Extra: real business logic
class CeleryTaskTest(TestCase):
    """check_prices / send_notification with the HTTP call mocked."""

    def setUp(self):
        self.user = User.objects.create_user(username='celeryuser', password='pass')
        self.alert = PriceAlert.objects.create(
            user=self.user, origin='DEL', destination='BOM', threshold_price=4500,
        )

    def _run_check(self, price):
        fake = MagicMock(status_code=200)
        fake.json.return_value = {'route': 'DEL-BOM', 'price': price}
        with patch('pulse.tasks.requests.get', return_value=fake), \
             patch.object(tasks.send_notification, 'delay',
                          side_effect=lambda *a, **k: tasks.send_notification(*a, **k)):
            tasks.check_prices()

    def test_price_below_threshold_creates_log_and_marks_triggered(self):
        self._run_check(4200)
        self.assertEqual(NotificationLog.objects.count(), 1)
        self.alert.refresh_from_db()
        self.assertEqual(self.alert.status, PriceAlert.Status.TRIGGERED)

    def test_price_equal_to_threshold_triggers(self):
        self._run_check(4500)
        self.assertEqual(NotificationLog.objects.count(), 1)

    def test_price_above_threshold_does_nothing(self):
        self._run_check(5000)
        self.assertEqual(NotificationLog.objects.count(), 0)
        self.alert.refresh_from_db()
        self.assertEqual(self.alert.status, PriceAlert.Status.ACTIVE)

    def test_inactive_alert_is_not_checked(self):
        self.alert.status = PriceAlert.Status.INACTIVE
        self.alert.save()
        self._run_check(1000)
        self.assertEqual(NotificationLog.objects.count(), 0)


class ApiTest(TestCase):
    def setUp(self):
        self.client = APIClient()

    def _register(self, username='alice', **extra):
        return self.client.post('/api/auth/register/', {
            'username': username, 'password': 'securepass', 'email': 'a@example.com', **extra,
        }, format='json')

    def _auth(self, token):
        self.client.credentials(HTTP_AUTHORIZATION=f'Bearer {token}')

    def test_register_returns_201_token_and_user_role(self):
        res = self._register()
        self.assertEqual(res.status_code, 201)
        self.assertEqual(res.data['role'], 'user')
        self.assertIn('access', res.data)

    def test_register_ignores_role_in_body(self):
        res = self._register(role='admin')
        self.assertEqual(res.data['role'], 'user')

    def test_register_duplicate_username_400(self):
        self._register()
        self.assertEqual(self._register().status_code, 400)

    def test_login_success_and_failure(self):
        self._register()
        ok = self.client.post('/api/auth/login/', {'username': 'alice', 'password': 'securepass'}, format='json')
        bad = self.client.post('/api/auth/login/', {'username': 'alice', 'password': 'nope'}, format='json')
        self.assertEqual(ok.status_code, 200)
        self.assertIn('access', ok.data)
        self.assertEqual(bad.status_code, 401)

    def test_alert_endpoints_require_token(self):
        self.assertEqual(self.client.get('/api/alerts/').status_code, 401)

    def test_create_list_and_deactivate_alert(self):
        self._auth(self._register().data['access'])
        self.assertEqual(self.client.get('/api/alerts/').data, [])   # empty list, not 404
        created = self.client.post('/api/alerts/', {
            'origin': 'del', 'destination': 'bom', 'threshold_price': '4500',
        }, format='json')
        self.assertEqual(created.status_code, 201)
        self.assertEqual(created.data['origin'], 'DEL')
        self.assertEqual(len(self.client.get('/api/alerts/').data), 1)
        res = self.client.delete(f"/api/alerts/{created.data['id']}/")
        self.assertEqual(res.status_code, 200)
        self.assertEqual(res.data, {'status': 'inactive'})
        self.assertTrue(PriceAlert.objects.filter(id=created.data['id'], status='inactive').exists())

    def test_cannot_delete_or_see_other_users_alert(self):
        self._auth(self._register('alice').data['access'])
        alert_id = self.client.post('/api/alerts/', {
            'origin': 'DEL', 'destination': 'BOM', 'threshold_price': 4500}, format='json').data['id']
        self._auth(self._register('bob').data['access'])
        self.assertEqual(self.client.get('/api/alerts/').data, [])
        self.assertEqual(self.client.delete(f'/api/alerts/{alert_id}/').status_code, 404)
        self.assertEqual(self.client.delete('/api/alerts/99999/').status_code, 404)
        self.assertEqual(PriceAlert.objects.get(id=alert_id).status, 'active')

    def test_price_feed_public_ok_and_unknown_route(self):
        ok = self.client.get('/api/flights/price/', {'route': 'DEL-BOM'})
        self.assertEqual(ok.status_code, 200)
        self.assertTrue(3000 <= ok.json()['price'] <= 7000)
        self.assertEqual(self.client.get('/api/flights/price/', {'route': 'XXX-YYY'}).status_code, 404)

    def test_admin_summary_403_for_user_and_200_for_admin(self):
        self._auth(self._register('alice').data['access'])
        self.client.post('/api/alerts/', {'origin': 'DEL', 'destination': 'BOM', 'threshold_price': 4500}, format='json')
        self.assertEqual(self.client.get('/api/admin/summary/').status_code, 403)

        admin = User.objects.create_user(username='boss', password='adminpass')
        admin.profile.role = UserProfile.Role.ADMIN
        admin.profile.save()
        token = self.client.post('/api/auth/login/', {'username': 'boss', 'password': 'adminpass'}, format='json').data['access']
        self._auth(token)
        res = self.client.get('/api/admin/summary/')
        self.assertEqual(res.status_code, 200)
        self.assertEqual(res.data['total_alerts'], 1)
        self.assertEqual(res.data['active_alerts'], 1)
        self.assertEqual(res.data['triggered_alerts'], 0)
        self.assertEqual(res.data['total_notifications'], 0)
        self.assertEqual(res.data['top_routes'], [{'route': 'DEL-BOM', 'alert_count': 1}])

    def test_superuser_gets_admin_profile(self):
        su = User.objects.create_superuser(username='root', password='x', email='r@example.com')
        self.assertEqual(su.profile.role, UserProfile.Role.ADMIN)
