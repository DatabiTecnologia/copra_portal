# config.py
import os
import psycopg2
from psycopg2 import extras


PG_CONFIG = {
    'host': "192.168.0.43",
    'port': 5432,
    'database': "copra",
    'user': "",
    'password': ""
}
def get_pg_connection():
    """Retorna uma conexão PostgreSQL usando psycopg2."""
    conn = psycopg2.connect(**PG_CONFIG)
    return conn

POSTGRES_CONFIG = {
    "host": "192.168.0.43",
    "password": "",
    "database": "an_bi",
    "port": 5432
}
SECRET_KEY = ''

