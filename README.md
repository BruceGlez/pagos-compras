# Pagos a Productores de Algodon

Sistema web para reemplazar el control en Excel de:
- `COMPRAS`
- `TC`
- `ANTICIPOS`

Stack:
- Django 6
- PostgreSQL (configurable por variables de entorno)
- Bootstrap 5

## Modulos incluidos

- Productores
- Tipos de cambio (TC por fecha)
- Anticipos a productor
- Compras de algodon
- Aplicacion de anticipos a compras, con validaciones de saldo

## Arranque rapido

```powershell
python -m venv .venv
.\.venv\Scripts\pip install -r requirements.txt
.\.venv\Scripts\python manage.py migrate
.\.venv\Scripts\python manage.py createsuperuser
.\.venv\Scripts\python manage.py runserver
```

App:
- `http://127.0.0.1:8000/`
- `http://127.0.0.1:8000/registro/`
- `http://127.0.0.1:8000/compras/`
- `http://127.0.0.1:8000/compras/<id>/flujo/`
- `http://127.0.0.1:8000/productores/`
- `http://127.0.0.1:8000/admin/`

## PostgreSQL

Por default usa SQLite. Para usar PostgreSQL exporta variables:

```env
DB_ENGINE=django.db.backends.postgresql
DB_NAME=pagos_compras
DB_USER=postgres
DB_PASSWORD=postgres
DB_HOST=localhost
DB_PORT=5432
```

Tambien puedes configurar:

```env
DJANGO_SECRET_KEY=change-me
DJANGO_DEBUG=1
DJANGO_ALLOWED_HOSTS=*
```
