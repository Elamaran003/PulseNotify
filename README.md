# PulseNotify - Flight Price Alert Backend

Django + DRF + SimpleJWT + Celery (Beat) + Redis. Database: **SQLite** (no Postgres / Docker needed for the DB).

## 1. Setup

```bash
python -m venv .venv
source .venv/bin/activate          # Windows: .venv\Scripts\activate
pip install -r requirements.txt
cp .env.example .env               # Windows: copy .env.example .env  (then edit SECRET_KEY)
python manage.py migrate
python manage.py createsuperuser   # superusers automatically get role=ADMIN
```

## 2. Redis (Celery broker)

Redis is the only external service. Pick one:

```bash
docker compose up -d redis         # easiest
# or: brew install redis && redis-server | sudo apt install redis-server | WSL / Memurai on Windows
```

## 3. Run the three processes (three terminals, venv active in each)

```bash
# Terminal 1 - API server + mock price feed
python manage.py runserver

# Terminal 2 - Celery worker (watch send_notification tasks arrive)
celery -A pulsenotify worker --loglevel=info
#   Windows: celery -A pulsenotify worker --loglevel=info --pool=solo

# Terminal 3 - Celery Beat (check_prices fires every 60 seconds)
celery -A pulsenotify beat --loglevel=info
```

## 4. Try it

1. Register (`POST /api/auth/register/`) and copy the `access` token.
2. Create an alert with a high threshold so it triggers fast, e.g. DEL-BOM @ 7000:
   `POST /api/alerts/  {"origin":"DEL","destination":"BOM","threshold_price":7000}`
3. Within ~60s the worker log shows `send_notification`, the alert becomes `triggered`, and a `NotificationLog` row appears
   (inspect via `/admin/` or any SQLite viewer, e.g. DB Browser for SQLite).
4. Log in as the superuser and call `GET /api/admin/summary/`.

## Endpoints

| Method | URL | Auth |
|---|---|---|
| POST | `/api/auth/register/` | none |
| POST | `/api/auth/login/` | none |
| POST | `/api/alerts/` | JWT |
| GET | `/api/alerts/` | JWT (own alerts only) |
| DELETE | `/api/alerts/<id>/` | JWT (sets status=inactive) |
| GET | `/api/flights/price/?route=DEL-BOM` | none (internal mock feed) |
| GET | `/api/admin/summary/` | JWT + role=ADMIN |

Header: `Authorization: Bearer <access>`. Supported mock routes: DEL-BOM, BLR-HYD, DEL-BLR, BOM-GOA.

## Tests

```bash
python manage.py test
```

## Postman

Import `postman/PulseNotify.postman_collection.json`, run requests 01-13 in order.
Request 13 needs the superuser from step 1 (collection variables `admin_username` / `admin_password`).

## Settings

`pulsenotify/settings/{base,local,production}.py`; selected via `DJANGO_SETTINGS_MODULE` in `.env`
(`pulsenotify.settings.local` or `pulsenotify.settings.production`). `.env` is git-ignored.
