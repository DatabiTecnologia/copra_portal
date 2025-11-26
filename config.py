# config.py
import os
import psycopg2
from psycopg2 import extras
#from dotenv import load_dotenv
MYSQL_CONFIG = {
    'host': '192.168.0.233',
    'user': 'pbi_checkin',
    'password': 'pbiarquivo@1234',
    'database': 'checkin'
}

PG_CONFIG = {
    'host': "192.168.0.43",
    'port': 5432,
    'database': "copra",
    'user': "usuario_bi",
    'password': "bi@tec77"
}
def get_pg_connection():
    """Retorna uma conexão PostgreSQL usando psycopg2."""
    conn = psycopg2.connect(**PG_CONFIG)
    return conn

POSTGRES_CONFIG = {
    "host": "192.168.0.43",
    "user": "usuario_bi",
    "password": "bi@tec77",
    "database": "an_bi",
    "portc": 5432
}
SECRET_KEY = 'b1c9f89a3e5a45dbb623a2c81ef2acff4fa4bfb89c3f1f83d7f8b9a6c8d92e3a'

