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

## TC automatico con Banxico

Configura:

```env
BANXICO_TOKEN=tu_token_banxico
BANXICO_SERIE_ID=SF60653
BANXICO_TC_OBJETIVO=publicacion_dof
```

Series utiles:
- `SF60653`: Fecha de liquidacion (publicacion DOF)
- `SF43718`: Fecha de determinacion (FIX)

Ejecucion manual:

```powershell
python manage.py actualizar_tc_banxico --days 7
```

Programacion diaria (ejemplo Windows Task Scheduler):

```powershell
cd c:\Users\bruce\vscode_projects\pagos-compras
.\.venv\Scripts\python manage.py actualizar_tc_banxico --days 2
```

