# config.py
import os
#from dotenv import load_dotenv
MYSQL_CONFIG = {
    'host': '192.168.0.233',
    'user': 'pbi_checkin',
    'password': 'pbiarquivo@1234',
    'database': 'checkin'
}

PG_CONFIG = {
    'host': os.getenv("PG_HOST", "localhost"),
    'port': os.getenv("PG_PORT", 5432),
    'database': os.getenv("PG_DATABASE", "AN"),
    'user': os.getenv("PG_USER", "pbi_an"),
    'password': os.getenv("PG_PASSWORD", "AN77")
}

SECRET_KEY = 'b1c9f89a3e5a45dbb623a2c81ef2acff4fa4bfb89c3f1f83d7f8b9a6c8d92e3a'

