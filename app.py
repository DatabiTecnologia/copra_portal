# app.py
from flask import Flask, render_template, request, redirect, url_for, session, jsonify, flash, Blueprint,send_file
from werkzeug.utils import secure_filename
import mysql.connector
import bcrypt
import config 
import pyodbc
import pandas as pd
from flask import send_file
import io
import psycopg2
from config import POSTGRES_CONFIG
import unicodedata
from io import BytesIO
from datetime import datetime, date
import os
from flask import request, jsonify
from flask_login import current_user, UserMixin
from datetime import datetime
from flask_login import LoginManager, login_required, login_user, logout_user, current_user
from openpyxl import load_workbook
from openpyxl.worksheet.datavalidation import DataValidation
import json
import calendar


app = Flask(__name__)
app.secret_key = config.SECRET_KEY



def get_pg_connection():
    return psycopg2.connect(
        host=config.PG_CONFIG['host'],
        port=config.PG_CONFIG['port'],
        database=config.PG_CONFIG['database'],
        user=config.PG_CONFIG['user'],
        password=config.PG_CONFIG['password']
    )
def get_postgres_connection():
    """
    Retorna uma conexão com o PostgreSQL (an_bi),
    usando o config centralizado.
    """
    return psycopg2.connect(**POSTGRES_CONFIG)

def grant_permission(page, username):
    conn = config.get_pg_connection()
    cur = conn.cursor()
    try:
        cur.execute("""
            INSERT INTO page_permissions (page, username)
            VALUES (%s, %s)
            ON CONFLICT (page, username) DO NOTHING
            RETURNING 1
        """, (page, username))
        inserted = cur.fetchone() is not None
        conn.commit()
        return inserted
    except Exception:
        conn.rollback()
        raise
    finally:
        cur.close()
        conn.close()

def revoke_permission(page, username):
    conn = config.get_pg_connection()
    cur = conn.cursor()
    try:
        cur.execute("""
            DELETE FROM page_permissions
            WHERE page = %s AND username = %s
        """, (page, username))
        removed = (cur.rowcount > 0)
        conn.commit()
        return removed
    except Exception:
        conn.rollback()
        raise
    finally:
        cur.close()
        conn.close()

def log_permission_audit(page, username, action, changed_by):
    conn = get_postgres_connection()
    cursor = conn.cursor()

    cursor.execute(
        """
        INSERT INTO permissions_audit
        (page, username, action, changed_by)
        VALUES (%s, %s, %s, %s)
        """,
        (page, username, action, changed_by)
    )

    conn.commit()
    cursor.close()
    conn.close()

# -------------------
# Permissões
# -------------------
def load_page_permissions():
    conn = get_postgres_connection()
    cursor = conn.cursor()

    cursor.execute("""
        SELECT page, username
        FROM page_permissions
    """)
    rows = cursor.fetchall()

    cursor.close()
    conn.close()

    permissions = {}
    for page, username in rows:
        permissions.setdefault(page, []).append(username)

    return permissions


def save_page_permissions(page, usernames, changed_by=None):
    conn = get_postgres_connection()
    cursor = conn.cursor()

    cursor.execute(
        "SELECT username FROM page_permissions WHERE page = %s",
        (page,)
    )
    existing = {row[0] for row in cursor.fetchall()}
    new_users = set(usernames)

    to_add = new_users - existing

    for username in to_add:
        cursor.execute(
            """
            INSERT INTO page_permissions (page, username)
            VALUES (%s, %s)
            ON CONFLICT DO NOTHING
            """,
            (page, username)
        )

        cursor.execute(
            """
            INSERT INTO permissions_audit
            (page, username, action, changed_by)
            VALUES (%s, %s, 'CONCEDIDO', %s)
            """,
            (page, username, changed_by)
        )

    conn.commit()
    cursor.close()
    conn.close()


def check_access(page):
    if 'user' not in session:
        return False

    if session['user']['is_admin'] == 1:
        return True

    permissions = load_page_permissions()
    allowed = permissions.get(page, [])

    return session['user']['username'].lower() in [u.lower() for u in allowed]



def require_permission(page_code):
    if 'user' not in session:
        return redirect(url_for('login'))

    if session['user']['is_admin'] == 1:
        return None

    if not check_access(page_code):
        return redirect(url_for('sem_permissao'))

    return None



def get_all_usernames():
    conn = get_postgres_connection()
    cursor = conn.cursor()

    cursor.execute("""
        SELECT username
        FROM users
        WHERE username IS NOT NULL AND username <> ''
        ORDER BY username
    """)
    rows = cursor.fetchall()

    cursor.close()
    conn.close()

    return [row[0] for row in rows]



def user_has_any_permission(username):
    conn = get_postgres_connection()
    cursor = conn.cursor()

    cursor.execute(
        "SELECT is_admin FROM users WHERE username = %s",
        (username,)
    )
    row = cursor.fetchone()

    cursor.close()
    conn.close()

    if row and row[0] == 1:
        return True

    permissions = load_page_permissions()
    username_lower = username.lower()

    for users in permissions.values():
        if username_lower in [u.lower() for u in users]:
            return True

    return False


def normalizar_coluna(col):
    """
    Normaliza APENAS o nome da coluna (para bater com o banco)
    """
    nfkd = unicodedata.normalize('NFKD', str(col))
    col_sem_acento = "".join([c for c in nfkd if not unicodedata.combining(c)])
    col_normalizada = col_sem_acento.lower()
    col_normalizada = col_normalizada.replace(" ", "_").replace("-", "_").replace("/", "_")
    col_normalizada = "".join([c if c.isalnum() or c == "_" else "" for c in col_normalizada])
    return col_normalizada

def save_page_permissions(page, new_users, changed_by):
    conn = get_postgres_connection()
    cursor = conn.cursor()

    cursor.execute(
        "SELECT username FROM public.page_permissions WHERE page = %s",
        (page,)
    )
    current_users = {row[0] for row in cursor.fetchall()}
    new_users = set(new_users)

    added_users = new_users - current_users
    removed_users = current_users - new_users

    for username in added_users:
        cursor.execute(
            "INSERT INTO public.page_permissions (page, username) VALUES (%s, %s)",
            (page, username)
        )
        cursor.execute(
            """
            INSERT INTO public.permissions_audit (page, username, action, changed_by)
            VALUES (%s, %s, 'CONCEDIDO', %s)
            """,
            (page, username, changed_by)
        )

    for username in removed_users:
        cursor.execute(
            "DELETE FROM public.page_permissions WHERE page = %s AND username = %s",
            (page, username)
        )
        cursor.execute(
            """
            INSERT INTO public.permissions_audit (page, username, action, changed_by)
            VALUES (%s, %s, 'REMOVIDO', %s)
            """,
            (page, username, changed_by)
        )

    conn.commit()
    cursor.close()
    conn.close()

def get_all_divisoes():
    conn = get_postgres_connection()
    cursor = conn.cursor()
    cursor.execute("""
        SELECT DISTINCT divisa
        FROM tabela_padrao
        WHERE divisa IS NOT NULL
        ORDER BY divisa
    """)
    rows = cursor.fetchall()
    cursor.close()
    conn.close()
    return [r[0] for r in rows]

def user_can_access_divisao(username, divisao):
    conn = get_postgres_connection()
    cursor = conn.cursor()
    cursor.execute("""
        SELECT 1
        FROM divisao_permissions
        WHERE username = %s AND divisao = %s
    """, (username, divisao))
    allowed = cursor.fetchone() is not None
    cursor.close()
    conn.close()
    return allowed


def get_divisoes_permitidas(username):
    conn = get_postgres_connection()
    cur = conn.cursor()

    cur.execute("""
        SELECT DISTINCT dp.divisao
        FROM divisao_permissions dp
        WHERE dp.username = %s
        ORDER BY dp.divisao;
    """, (username,))

    divisoes = [r[0] for r in cur.fetchall()]

    cur.close()
    conn.close()
    return divisoes






def save_divisao_permissions(username, novas_divisoes, admin_user):
    conn = config.get_pg_connection()
    cur = conn.cursor()

    # Divisões atuais
    cur.execute(
        "SELECT divisao FROM tabela_divisao WHERE username = %s;",
        (username,)
    )
    atuais = {row[0] for row in cur.fetchall()}
    novas = set(novas_divisoes)

    # ➖ Removidas
    for div in atuais - novas:
        cur.execute(
            "DELETE FROM tabela_divisao WHERE username=%s AND divisao=%s;",
            (username, div)
        )
        cur.execute("""
            INSERT INTO tabela_divisao_audit
            (username, divisao, action, changed_by)
            VALUES (%s, %s, 'REMOVIDO', %s);
        """, (username, div, admin_user))

    # ➕ Concedidas
    for div in novas - atuais:
        cur.execute("""
            INSERT INTO tabela_divisao
            (username, divisao, granted_by)
            VALUES (%s, %s, %s);
        """, (username, div, admin_user))

        cur.execute("""
            INSERT INTO tabela_divisao_audit
            (username, divisao, action, changed_by)
            VALUES (%s, %s, 'CONCEDIDO', %s);
        """, (username, div, admin_user))

    conn.commit()
    cur.close()
    conn.close()
from flask_login import UserMixin

class User(UserMixin):
    def __init__(self, id, username, is_admin):
        self.id = id
        self.username = username
        self.is_admin = is_admin

    def get_id(self):
        return str(self.id)

login_manager = LoginManager()
login_manager.init_app(app)
login_manager.login_view = 'login'

@login_manager.user_loader
def load_user(user_id):
    conn = get_postgres_connection()
    cursor = conn.cursor()
    cursor.execute(
        "SELECT id, username, is_admin FROM users WHERE id = %s",
        (user_id,)
    )
    row = cursor.fetchone()
    cursor.close()
    conn.close()

    if row:
        return User(id=row[0], username=row[1], is_admin=row[2])
    return None

def audit_log(entity_type, entity_id, action, details, created_by):
    import json
    conn = get_postgres_connection()
    cur = conn.cursor()
    try:
        cur.execute("""
            INSERT INTO system_audit (entity_type, entity_id, action, details, created_by)
            VALUES (%s, %s, %s, %s::jsonb, %s)
        """, (
            entity_type,
            entity_id,
            action,
            json.dumps(details or {}),
            created_by
        ))
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        cur.close()
        conn.close()

# -------------------
# Rotas
# -------------------
@app.route('/', methods=['GET', 'POST'])
def login():
    if request.method == 'POST':
        username = request.form['username']
        password = request.form['password']

        conn = get_postgres_connection()
        cursor = conn.cursor()

        cursor.execute(
            """
            SELECT username, password, is_admin
            FROM users
            WHERE username = %s
            """,
            (username,)
        )
        row = cursor.fetchone()

        cursor.close()
        conn.close()

        if not row:
            return render_template("login.html", error="Usuário não encontrado")

        db_username, password_hash, is_admin = row

        if not password_hash:
            return render_template("login.html", error="Senha não configurada")

        try:
            if isinstance(password_hash, memoryview):
                password_hash = bytes(password_hash)

            if isinstance(password_hash, memoryview):
                password_hash = password_hash.tobytes()
            elif isinstance(password_hash, str):
                password_hash = password_hash.encode("utf-8")

            if bcrypt.checkpw(password.encode("utf-8"), password_hash):
                user = {
                    "username": db_username,
                    "is_admin": is_admin
                }
                session["user"] = user
                return redirect(url_for("home"))
            else:
                return render_template("login.html", error="Senha incorreta")

        except Exception as e:
            return render_template("login.html", error=f"Erro ao validar senha: {str(e)}")

    return render_template("login.html")


@app.route('/home')
def home():
    # exige login
    if 'user' not in session:
        return redirect(url_for('login'))
    
    # Verifica se o usuário tem permissões (admin ou pelo menos uma permissão)
    if not user_has_any_permission(session['user']['username']):
        return redirect(url_for('sem_permissao'))

    # valores padrão (caso algo falhe, não quebra o template)
    total_registros = 0
    total_nao_localizado = 0
    percentual_nao_localizado = 0
    total_divisoes = 0
    divisoes = []
    percentual_divisao = []
    datas = []
    qtd_nao_localizado_data = []
    notacoes = []
    qtd_notacoes = []
    ranking_divisoes = []
    qtd_nao_localizado_ranking = []

    conn = None
    try:
        conn = config.get_pg_connection()
        cur = conn.cursor()

        # Total de registros
        cur.execute("SELECT COUNT(*) FROM tabela_padrao;")
        total_registros = cur.fetchone()[0] or 0

        # Total de 'não localizado'
        cur.execute("SELECT COUNT(*) FROM tabela_padrao WHERE observacoes ILIKE '%%não localizado%%';")
        total_nao_localizado = cur.fetchone()[0] or 0

        # Percentual geral
        percentual_nao_localizado = round((total_nao_localizado / total_registros * 100), 2) if total_registros > 0 else 0

        # Quantidade de divisões distintas
        cur.execute("SELECT COUNT(DISTINCT TRIM(divisao)) FROM tabela_padrao WHERE divisao IS NOT NULL AND TRIM(divisao) <> '';")
        total_divisoes = cur.fetchone()[0] or 0

        # Não localizado por divisão (para gráfico)
        cur.execute("""
            SELECT TRIM(divisao) AS divisao,
                   COUNT(*) FILTER (WHERE observacoes ILIKE '%%não localizado%%') AS nao_localizado,
                   COUNT(*) AS total
            FROM tabela_padrao
            WHERE divisao IS NOT NULL AND TRIM(divisao) <> ''
            GROUP BY divisao
            ORDER BY divisao;
        """)
        dados_divisao = cur.fetchall()
        divisoes = [row[0] for row in dados_divisao]
        nao_localizado = [row[1] for row in dados_divisao]
        total_por_divisao = [row[2] for row in dados_divisao]
        percentual_divisao = [
            round((row[1] / row[2] * 100), 2) if row[2] > 0 else 0 for row in dados_divisao
        ]

        # Evolução temporal (data_registro x não localizado)
        cur.execute("""
            SELECT data_registro::date AS data_registro,
                   COUNT(*) FILTER (WHERE observacoes ILIKE '%%não localizado%%') AS nao_localizado
            FROM tabela_padrao
            WHERE data_registro IS NOT NULL
            GROUP BY data_registro::date
            ORDER BY data_registro::date;
        """)
        evolucao_data = cur.fetchall()
        datas = [row[0].strftime("%d/%m/%Y") if row[0] is not None else '' for row in evolucao_data]
        qtd_nao_localizado_data = [row[1] for row in evolucao_data]

        # Distribuição das notações (top 10)
        cur.execute("""
            SELECT TRIM(notacao) AS notacao, COUNT(*) 
            FROM tabela_padrao
            WHERE notacao IS NOT NULL AND TRIM(notacao) <> ''
            GROUP BY notacao
            ORDER BY COUNT(*) DESC
            LIMIT 10;
        """)
        notacoes_data = cur.fetchall()
        notacoes = [row[0] for row in notacoes_data]
        qtd_notacoes = [row[1] for row in notacoes_data]

        # Ranking de divisões com mais 'não localizado' (top 10)
        cur.execute("""
            SELECT TRIM(divisao) AS divisao, COUNT(*) AS qtd
            FROM tabela_padrao
            WHERE observacoes ILIKE '%%não localizado%%' AND divisao IS NOT NULL AND TRIM(divisao) <> ''
            GROUP BY divisao
            ORDER BY qtd DESC
            LIMIT 10;
        """)
        rank = cur.fetchall()
        ranking_divisoes = [r[0] for r in rank]
        qtd_nao_localizado_ranking = [r[1] for r in rank]

        # garantir fechamento
        cur.close()
        conn.close()
        conn = None

    except Exception as e:
        # tenta fechar/rollback com segurança
        try:
            if conn:
                conn.rollback()
        except Exception:
            pass
        try:
            if conn:
                conn.close()
        except Exception:
            pass
        # opcional: log do erro no console
        print(f"[ERRO] rota /home: {e}")

    # renderiza com todas as variáveis necessárias para o home.html
    return render_template(
        'home.html',
        total_registros=total_registros,
        total_nao_localizado=total_nao_localizado,
        percentual_nao_localizado=percentual_nao_localizado,
        total_divisoes=total_divisoes,
        divisoes=divisoes,
        percentual_divisao=percentual_divisao,
        datas=datas,
        qtd_nao_localizado_data=qtd_nao_localizado_data,
        notacoes=notacoes,
        qtd_notacoes=qtd_notacoes,
        ranking_divisoes=ranking_divisoes,
        qtd_nao_localizado_ranking=qtd_nao_localizado_ranking
    )
@app.template_filter("br_date")
def br_date(value):
    if not value:
        return ""
    # se vier string, tenta converter
    if isinstance(value, str):
        for fmt in ("%Y-%m-%d", "%d/%m/%Y"):
            try:
                value = datetime.strptime(value, fmt).date()
                break
            except ValueError:
                continue
        else:
            return value  # não conseguiu converter, devolve como veio
    if isinstance(value, datetime):
        value = value.date()
    if isinstance(value, date):
        return value.strftime("%d/%m/%Y")
    return str(value)

@app.template_filter("br_datetime")
def br_datetime(value):
    if not value:
        return ""
    # se vier string, tenta converter
    if isinstance(value, str):
        for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%dT%H:%M:%S", "%d/%m/%Y %H:%M"):
            try:
                value = datetime.strptime(value, fmt)
                break
            except ValueError:
                continue
        else:
            return value
    if isinstance(value, date) and not isinstance(value, datetime):
        return value.strftime("%d/%m/%Y")
    if isinstance(value, datetime):
        return value.strftime("%d/%m/%Y %H:%M")
    return str(value)

@app.route('/home_v2')
def home_v2():
    # exige login
    if 'user' not in session:
        return redirect(url_for('login'))

    # Verifica se o usuário tem permissões (admin ou pelo menos uma permissão)
    if not user_has_any_permission(session['user']['username']):
        return redirect(url_for('sem_permissao'))

    # =========================
    # ✅ Variáveis do dashboard v2 (base localizado + tentativas)
    # =========================
    total_registros = 0
    total_encontrados = 0
    total_nao_encontrados = 0
    percentual_nao_encontrados = 0.0

    # Série mensal últimos 12 meses por inserido_em
    meses_insercao = []
    encontrados_mes = []
    nao_encontrados_mes = []

    # Tentativas por dia (últimos 60 dias)
    dias_tentativas = []
    tentativas_total_dia = []
    tentativas_encontrado_dia = []

    # Ranking por divisão (localizado)
    divisoes_rank = []
    encontrados_div = []
    nao_encontrados_div = []

    # Achados no mês atual (data_localizacao)
    achados_mes = []  # lista de tuplas (id, codigo, titulo, divisao, data_localizacao, alterado_por)

    conn = None
    try:
        conn = config.get_pg_connection()
        cur = conn.cursor()

        # Total geral
        cur.execute("SELECT COUNT(*) FROM tabela_padrao;")
        total_registros = cur.fetchone()[0] or 0

        # Cards: encontrados vs não encontrados (NULL conta como não encontrado)
        cur.execute("""
            SELECT
              COUNT(*) FILTER (WHERE COALESCE(localizado, FALSE) = TRUE)  AS encontrados,
              COUNT(*) FILTER (WHERE COALESCE(localizado, FALSE) = FALSE) AS nao_encontrados
            FROM tabela_padrao;
        """)
        row = cur.fetchone()
        total_encontrados = row[0] or 0
        total_nao_encontrados = row[1] or 0
        percentual_nao_encontrados = round((total_nao_encontrados / total_registros * 100), 2) if total_registros else 0.0

        # Série mensal últimos 12 meses por inserido_em
        cur.execute("""
            WITH m AS (
                SELECT date_trunc('month', CURRENT_DATE) - (interval '1 month' * gs) AS mes
                FROM generate_series(0, 11) gs
            )
            SELECT
              m.mes::date AS mes,
              COALESCE(COUNT(tp.*) FILTER (WHERE COALESCE(tp.localizado, FALSE) = TRUE), 0)  AS encontrados,
              COALESCE(COUNT(tp.*) FILTER (WHERE COALESCE(tp.localizado, FALSE) = FALSE), 0) AS nao_encontrados
            FROM m
            LEFT JOIN tabela_padrao tp
              ON date_trunc('month', tp.inserido_em) = m.mes
            GROUP BY 1
            ORDER BY 1;
        """)
        serie = cur.fetchall()
        meses_insercao = [s[0].strftime("%m/%Y") for s in serie]
        encontrados_mes = [int(s[1]) for s in serie]
        nao_encontrados_mes = [int(s[2]) for s in serie]

        # Tentativas por dia (últimos 60 dias)
        cur.execute("""
            WITH d AS (
                SELECT (CURRENT_DATE - gs)::date AS dia
                FROM generate_series(0, 59) gs
            )
            SELECT
              d.dia,
              COALESCE(COUNT(dt.*), 0) AS tentativas_total,
              COALESCE(COUNT(dt.*) FILTER (WHERE dt.status = 'ENCONTRADO'), 0) AS tentativas_encontrado
            FROM d
            LEFT JOIN documento_tentativas dt
              ON DATE(dt.criado_em) = d.dia
            GROUP BY 1
            ORDER BY 1;
        """)
        serie_t = cur.fetchall()
        dias_tentativas = [s[0].strftime("%d/%m") for s in serie_t]
        tentativas_total_dia = [int(s[1]) for s in serie_t]
        tentativas_encontrado_dia = [int(s[2]) for s in serie_t]

        # Ranking por divisão (localizado) - Top 10 por volume total
        cur.execute("""
            SELECT
              UPPER(TRIM(divisao)) AS divisao,
              COUNT(*) AS total,
              COUNT(*) FILTER (WHERE COALESCE(localizado, FALSE) = TRUE) AS encontrados,
              COUNT(*) FILTER (WHERE COALESCE(localizado, FALSE) = FALSE) AS nao_encontrados
            FROM tabela_padrao
            WHERE divisao IS NOT NULL AND TRIM(divisao) <> ''
            GROUP BY 1
            ORDER BY total DESC
            LIMIT 10;
        """)
        rdiv = cur.fetchall()
        divisoes_rank = [r[0] for r in rdiv]
        encontrados_div = [int(r[2]) for r in rdiv]
        nao_encontrados_div = [int(r[3]) for r in rdiv]

        # Achados no mês atual (data_localizacao)
        mes_inicio = date.today().replace(day=1)
        last_day = calendar.monthrange(mes_inicio.year, mes_inicio.month)[1]
        mes_fim = date(mes_inicio.year, mes_inicio.month, last_day)

        cur.execute("""
            SELECT
              id,
              codigo_referencia,
              titulo_conteudo,
              UPPER(TRIM(divisao)) AS divisao,
              data_localizacao,
              alterado_por
            FROM tabela_padrao
            WHERE COALESCE(localizado, FALSE) = TRUE
              AND data_localizacao BETWEEN %s AND %s
            ORDER BY data_localizacao DESC, id DESC
            LIMIT 200;
        """, (mes_inicio, mes_fim))
        achados_mes = cur.fetchall()

        cur.close()
        conn.close()
        conn = None

    except Exception as e:
        try:
            if conn:
                conn.rollback()
        except Exception:
            pass
        try:
            if conn:
                conn.close()
        except Exception:
            pass
        print(f"[ERRO] rota /home_v2: {e}")

    return render_template(
        'home_v2.html',
        total_registros=total_registros,
        total_encontrados=total_encontrados,
        total_nao_encontrados=total_nao_encontrados,
        percentual_nao_encontrados=percentual_nao_encontrados,
        meses_insercao=meses_insercao,
        encontrados_mes=encontrados_mes,
        nao_encontrados_mes=nao_encontrados_mes,
        dias_tentativas=dias_tentativas,
        tentativas_total_dia=tentativas_total_dia,
        tentativas_encontrado_dia=tentativas_encontrado_dia,
        divisoes_rank=divisoes_rank,
        encontrados_div=encontrados_div,
        nao_encontrados_div=nao_encontrados_div,
        achados_mes=achados_mes
    )

@app.route('/dashboard_divisao', methods=['GET', 'POST'])
def dashboard_divisao():
    if 'user' not in session:
        return redirect(url_for('login'))

    # 🔐 Verifica permissões da página
    permission_check = require_permission('dashboard_divisao')
    if permission_check:
        return permission_check

    conn = None
    divisao_selecionada = None
    dados_divisao = []
    mensagem = None

    username = session['user']['username']
    is_admin = session['user']['is_admin']

    try:
        conn = config.get_pg_connection()
        cur = conn.cursor()

        # 🔹 Divisões permitidas por usuário
        divisoes = get_divisoes_permitidas(username, is_admin)

        # 🔹 POST — usuário escolheu uma divisão
        if request.method == 'POST':
            divisao_selecionada = request.form.get('divisao')

            # 🔒 Bloqueio de divisão não autorizada
            if divisao_selecionada and divisao_selecionada not in divisoes:
                mensagem = "❌ Você não tem permissão para acessar esta divisão."
                return render_template(
                    'dashboard_divisao.html',
                    divisoes=divisoes,
                    divisao_selecionada=None,
                    dados_divisao=[],
                    mensagem=mensagem
                )

            if divisao_selecionada:
                cur.execute("""
                    SELECT id, fundo_colecao, titulo_conteudo, codigo_referencia, notacao,
                           localizacao_fisica, data_registro, data_localizacao, observacoes
                    FROM tabela_padrao
                    WHERE TRIM(divisao) = %s
                    ORDER BY id DESC;
                """, (divisao_selecionada,))
                dados_divisao = cur.fetchall()

                if not dados_divisao:
                    mensagem = f"Nenhum registro encontrado para a divisão '{divisao_selecionada}'."

        cur.close()
        conn.close()
        conn = None

    except Exception as e:
        print(f"[ERRO] rota /dashboard_divisao: {e}")
        if conn:
            conn.close()
        mensagem = "Erro ao carregar dados da divisão."
        divisoes = []

    return render_template(
        'dashboard_divisao.html',
        divisoes=divisoes,
        divisao_selecionada=divisao_selecionada,
        dados_divisao=dados_divisao,
        mensagem=mensagem
    )

@app.route('/dashboard_divisao_v2', methods=['GET', 'POST'])
def dashboard_divisao_v2():
    if 'user' not in session:
        return redirect(url_for('login'))

    # 🔐 Permissão
    permission_check = require_permission('dashboard_divisao')
    if permission_check:
        return permission_check

    conn = None
    mensagem = None
    divisao_selecionada = None

    username = session['user']['username']
    is_admin = session['user']['is_admin']

    # =========================
    # Defaults (não quebrar template)
    # =========================
    divisoes = []
    cards = {
        "total_registros": 0,
        "total_localizados": 0,
        "total_nao_localizados": 0,
        "percent_nao_localizados": 0
    }

    # Gráficos
    labels_inserido = []
    serie_localizados_inserido = []
    serie_nao_localizados_inserido = []

    labels_tentativas = []
    serie_tentativas_total = []
    serie_tentativas_encontrado = []

    labels_achados = []
    serie_achados = []

    # Tabela: achados do período
    achados_mes = []

    try:
        conn = config.get_pg_connection()
        cur = conn.cursor()

        # 🔹 Divisões permitidas
        divisoes = get_divisoes_permitidas(username, is_admin)

        # Seleção (POST ou GET por querystring opcional)
        if request.method == 'POST':
            divisao_selecionada = request.form.get('divisao')
        else:
            divisao_selecionada = request.args.get('divisao')

        if divisao_selecionada:
            # 🔒 Bloqueia divisão não permitida
            if divisao_selecionada not in divisoes:
                mensagem = "❌ Você não tem permissão para acessar esta divisão."
                return render_template(
                    'dashboard_divisao_v2.html',
                    divisoes=divisoes,
                    divisao_selecionada=None,
                    mensagem=mensagem,
                    cards=cards,
                    labels_inserido=[],
                    serie_localizados_inserido=[],
                    serie_nao_localizados_inserido=[],
                    labels_tentativas=[],
                    serie_tentativas_total=[],
                    serie_tentativas_encontrado=[],
                    labels_achados=[],
                    serie_achados=[],
                    achados_mes=[]
                )

            # =========================
            # 1) Cards (baseado no localizado)
            # =========================
            cur.execute("""
                SELECT
                    COUNT(*) AS total,
                    COUNT(*) FILTER (WHERE COALESCE(localizado, FALSE) = TRUE) AS localizados,
                    COUNT(*) FILTER (WHERE COALESCE(localizado, FALSE) = FALSE) AS nao_localizados
                FROM tabela_padrao
                WHERE TRIM(divisao) = %s
            """, (divisao_selecionada,))
            total, localizados, nao_localizados = cur.fetchone() or (0, 0, 0)

            cards["total_registros"] = int(total or 0)
            cards["total_localizados"] = int(localizados or 0)
            cards["total_nao_localizados"] = int(nao_localizados or 0)
            cards["percent_nao_localizados"] = round((cards["total_nao_localizados"] / cards["total_registros"] * 100), 2) if cards["total_registros"] > 0 else 0

            # =========================
            # 2) Evolução por inserido_em (últimos 12 meses)
            # Localizados vs Não localizados
            # =========================
            cur.execute("""
                SELECT
                    DATE_TRUNC('day', inserido_em)::date AS dia,
                    COUNT(*) FILTER (WHERE COALESCE(localizado, FALSE) = TRUE) AS localizados,
                    COUNT(*) FILTER (WHERE COALESCE(localizado, FALSE) = FALSE) AS nao_localizados
                FROM tabela_padrao
                WHERE TRIM(divisao) = %s
                  AND inserido_em >= (CURRENT_DATE - INTERVAL '12 months')
                GROUP BY 1
                ORDER BY 1;
            """, (divisao_selecionada,))
            rows = cur.fetchall()

            labels_inserido = [(r[0].strftime("%d/%m/%Y") if r[0] else "") for r in rows]
            serie_localizados_inserido = [int(r[1] or 0) for r in rows]
            serie_nao_localizados_inserido = [int(r[2] or 0) for r in rows]

            # =========================
            # 3) Tentativas por dia (últimos 12 meses)
            # total tentativas e tentativas ENCONTRADO
            # =========================
            cur.execute("""
                SELECT
                    DATE_TRUNC('day', dt.criado_em)::date AS dia,
                    COUNT(*) AS total,
                    COUNT(*) FILTER (WHERE dt.status = 'ENCONTRADO') AS encontrados
                FROM documento_tentativas dt
                JOIN tabela_padrao tp ON tp.id = dt.registro_id
                WHERE TRIM(tp.divisao) = %s
                  AND dt.criado_em >= (CURRENT_DATE - INTERVAL '12 months')
                GROUP BY 1
                ORDER BY 1;
            """, (divisao_selecionada,))
            rows = cur.fetchall()

            labels_tentativas = [(r[0].strftime("%d/%m/%Y") if r[0] else "") for r in rows]
            serie_tentativas_total = [int(r[1] or 0) for r in rows]
            serie_tentativas_encontrado = [int(r[2] or 0) for r in rows]

            # =========================
            # 4) Achados por data_localizacao (últimos 12 meses)
            # =========================
            cur.execute("""
                SELECT
                    data_localizacao::date AS dia,
                    COUNT(*) AS achados
                FROM tabela_padrao
                WHERE TRIM(divisao) = %s
                  AND COALESCE(localizado, FALSE) = TRUE
                  AND data_localizacao IS NOT NULL
                  AND data_localizacao >= (CURRENT_DATE - INTERVAL '12 months')
                GROUP BY 1
                ORDER BY 1;
            """, (divisao_selecionada,))
            rows = cur.fetchall()

            labels_achados = [(r[0].strftime("%d/%m/%Y") if r[0] else "") for r in rows]
            serie_achados = [int(r[1] or 0) for r in rows]

            # =========================
            # 5) Tabela: itens achados nos últimos 12 meses
            # (todas as colunas + inserido_por/alterado_por)
            # =========================
            cur.execute("""
                SELECT
                    id, fundo_colecao, titulo_conteudo, codigo_referencia, notacao,
                    localizacao_fisica, data_registro, data_localizacao, observacoes, divisao,
                    localizado, inserido_por, inserido_em, alterado_por, alterado_em
                FROM tabela_padrao
                WHERE TRIM(divisao) = %s
                  AND COALESCE(localizado, FALSE) = TRUE
                  AND data_localizacao IS NOT NULL
                  AND data_localizacao >= (CURRENT_DATE - INTERVAL '12 months')
                ORDER BY data_localizacao DESC, id DESC;
            """, (divisao_selecionada,))
            achados_mes = cur.fetchall()

        cur.close()
        conn.close()
        conn = None

    except Exception as e:
        print(f"[ERRO] rota /dashboard_divisao_v2: {e}")
        try:
            if conn:
                conn.close()
        except Exception:
            pass
        mensagem = "Erro ao carregar dados da divisão (v2)."

    return render_template(
        'dashboard_divisao_v2.html',
        divisoes=divisoes,
        divisao_selecionada=divisao_selecionada,
        mensagem=mensagem,
        cards=cards,
        labels_inserido=labels_inserido,
        serie_localizados_inserido=serie_localizados_inserido,
        serie_nao_localizados_inserido=serie_nao_localizados_inserido,
        labels_tentativas=labels_tentativas,
        serie_tentativas_total=serie_tentativas_total,
        serie_tentativas_encontrado=serie_tentativas_encontrado,
        labels_achados=labels_achados,
        serie_achados=serie_achados,
        achados_mes=achados_mes
    )

@app.route('/logout')
def logout():
    session.pop('user', None)
    return redirect(url_for('login'))

@app.route('/sem_permissao')
def sem_permissao():
    """
    Página exibida quando o usuário não tem permissões para acessar o sistema.
    """
    if 'user' not in session:
        return redirect(url_for('login'))
    return render_template('sem_permissao.html')

# Mapear abas -> tabelas
TABELAS_VALIDAS = {
    "CODES_DIJUD": "codes_dijud",
    "CODES_DIDOP": "codes_didop",
    "CODES_DIPEX": "codes_dipex",
    "CODAC_DIDAS": "codac_didas",
    "CODAC_DIDOC": "codac_didoc"
}


@app.route('/inserir_dados', methods=['GET', 'POST'])
def inserir_dados():
    if 'user' not in session:
        return redirect(url_for('login'))

    # 🔐 Permissão
    permission_check = require_permission('inserir_dados')
    if permission_check:
        return permission_check

    registro = None
    modo = 'inserir'

    
    # ✅ PREFILL vindo do /verificar_codigo
    codigo_prefill = (request.args.get('codigo') or '').strip()
    notacao_prefill = (request.args.get('notacao') or '').strip()

    if request.method == 'POST':
        changed_by = session['user']['username']

        # ==========================================================
        # 🔹 1) UPLOAD PLANILHA (UPSERT)
        # ==========================================================
        if 'upload_planilha' in request.form:
            file = request.files.get('file')
            if not file:
                flash("❌ Nenhum arquivo foi enviado.", "danger")
                return redirect(url_for('inserir_dados'))

            try:
                df = pd.read_excel(file)

                # ✅ Normalização robusta de headers (acentos/unicode/NBSP)
                import unicodedata, re

                def normalize_header(s: str) -> str:
                    s = str(s or "")
                    s = s.replace("\u00a0", " ")  # NBSP do Excel
                    s = s.strip()
                    s = unicodedata.normalize("NFKD", s)
                    s = "".join(ch for ch in s if not unicodedata.combining(ch))  # remove acentos
                    s = s.lower()
                    s = re.sub(r"\s+", " ", s)  # normaliza espaços múltiplos
                    return s

                df.columns = [normalize_header(c) for c in df.columns]

                # -----------------------------
                # ✅ find_col
                # -----------------------------
                def find_col(possiveis):
                    for c in df.columns:
                        if c in possiveis:
                            return c
                    return None

                col_codigo = find_col({
                    'codigo de referencia', 'codigo referencia',
                    'codigoreferencia', 'cod referencia', 'codreferencia',
                    'codigo_referencia', 'cod_referencia'
                })
                col_divisao = find_col({'divisao'})
                col_data_registro = find_col({'data', 'data registro', 'data do registro'})
                col_data_localizacao = find_col({'data da localizacao', 'data localizacao'})
                col_localizado = find_col({'localizado'})
                col_fundo = find_col({'fundo/colecao', 'fundo / colecao', 'fundo colecao', 'fundo'})
                col_titulo = find_col({'titulo / conteudo', 'titulo/conteudo', 'titulo conteudo', 'titulo'})
                col_notacao = find_col({'notacao'})
                col_local_fisico = find_col({'localizacao fisica', 'localizacao física', 'localizacao'})
                col_obs = find_col({'observacoes', 'observação', 'observacoes ', 'observações', 'observacao'})

                if not col_codigo:
                    raise Exception("A planilha precisa ter a coluna 'Código de Referência' (ex.: Código de Referência).")
                if not col_divisao:
                    raise Exception("A planilha precisa ter a coluna 'Divisão' (ex.: Divisão).")

                # Converte datas (se existirem)
                if col_data_registro:
                    df[col_data_registro] = pd.to_datetime(df[col_data_registro], errors='coerce').dt.date
                if col_data_localizacao:
                    df[col_data_localizacao] = pd.to_datetime(df[col_data_localizacao], errors='coerce').dt.date

                # Localizado: Sim/Não etc.
                if col_localizado:
                    df[col_localizado] = (
                        df[col_localizado].astype(str).str.strip().str.lower()
                        .map({
                            'sim': True, 's': True, 'true': True, '1': True, 'yes': True,
                            'nao': False, 'não': False, 'n': False, 'false': False, '0': False, 'no': False
                        })
                        .fillna(False)
                        .astype(bool)
                    )
                else:
                    df['localizado'] = False
                    col_localizado = 'localizado'

                # Trata NaN/NaT
                df = df.replace({pd.NaT: None}).where(pd.notnull(df), None)

                # -----------------------------
                # ✅ obrigatórios: código + divisão
                # -----------------------------
                df[col_codigo] = df[col_codigo].astype(str).str.strip()
                df[col_divisao] = df[col_divisao].astype(str).str.strip().str.upper()

                df_valid = df[
                    (df[col_codigo].notna()) & (df[col_codigo] != '') &
                    (df[col_divisao].notna()) & (df[col_divisao] != '')
                ]

                if df_valid.empty:
                    raise Exception("Nenhuma linha válida: 'Código de Referência' e 'Divisão' são obrigatórios.")

                linhas_ignoradas = int(len(df) - len(df_valid))
                df = df_valid

                if linhas_ignoradas > 0:
                    flash(f"⚠️ {linhas_ignoradas} linha(s) foram ignoradas por falta de Código de Referência ou Divisão.", "warning")

                # (opcional) valida divisões
                DIVISOES_VALIDAS = {'DIJUD', 'DIPEX', 'DIPOP', 'DIDOC', 'DIDAS'}
                invalidas = df[~df[col_divisao].isin(DIVISOES_VALIDAS)]
                if not invalidas.empty:
                    raise Exception("Planilha com divisão inválida. Use: DIJUD, DIPEX, DIPOP, DIDOC, DIDAS.")

                # -----------------------------
                # ✅ UPSERT (índice unique parcial => ON CONFLICT precisa do WHERE)
                # -----------------------------
                conn = config.get_pg_connection()
                cur = conn.cursor()

                inseridos = 0
                atualizados = 0

                for _, row in df.iterrows():
                    cur.execute("""
                        INSERT INTO tabela_padrao (
                            fundo_colecao, titulo_conteudo, codigo_referencia, notacao,
                            localizacao_fisica, data_registro, data_localizacao,
                            observacoes, divisao, localizado, inserido_por
                        )
                        VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
                        ON CONFLICT (codigo_referencia)
                        WHERE (codigo_referencia IS NOT NULL AND BTRIM(codigo_referencia) <> '')
                        DO UPDATE
                        SET fundo_colecao = EXCLUDED.fundo_colecao,
                            titulo_conteudo = EXCLUDED.titulo_conteudo,
                            notacao = EXCLUDED.notacao,
                            localizacao_fisica = EXCLUDED.localizacao_fisica,
                            data_registro = EXCLUDED.data_registro,
                            data_localizacao = EXCLUDED.data_localizacao,
                            observacoes = EXCLUDED.observacoes,
                            divisao = EXCLUDED.divisao,
                            localizado = EXCLUDED.localizado,
                            alterado_em = NOW(),
                            alterado_por = %s
                        RETURNING (xmax = 0) AS inserted
                    """, (
                        row.get(col_fundo) if col_fundo else None,              # 1
                        row.get(col_titulo) if col_titulo else None,            # 2
                        row.get(col_codigo),                                    # 3
                        row.get(col_notacao) if col_notacao else None,          # 4
                        row.get(col_local_fisico) if col_local_fisico else None,# 5
                        row.get(col_data_registro) if col_data_registro else None,      # 6
                        row.get(col_data_localizacao) if col_data_localizacao else None,# 7
                        row.get(col_obs) if col_obs else None,                  # 8
                        row.get(col_divisao),                                   # 9
                        bool(row.get(col_localizado)) if row.get(col_localizado) is not None else False,  # 10
                        changed_by,                                             # 11 -> inserido_por
                        changed_by                                              # 12 -> alterado_por
                    ))
                    was_inserted = cur.fetchone()[0]
                    if was_inserted:
                        inseridos += 1
                    else:
                        atualizados += 1

                conn.commit()
                cur.close()
                conn.close()

                # Auditoria
                try:
                    audit_log(
                        entity_type="REGISTRO",
                        entity_id=None,
                        action="UPLOAD_PLANILHA",
                        details={
                            "origem": "PLANILHA",
                            "total_linhas_validas": int(len(df)),
                            "linhas_ignoradas": int(linhas_ignoradas),
                            "inseridos": int(inseridos),
                            "atualizados": int(atualizados)
                        },
                        created_by=changed_by
                    )
                except Exception:
                    pass

                flash("✅ Planilha inserida/atualizada com sucesso!", "success")
                return redirect(url_for('inserir_dados'))

            except Exception as e:
                flash(f"❌ Erro ao inserir planilha: {e}", "danger")
                return redirect(url_for('inserir_dados'))

        # ==========================================================
        # 🔍 2) CONSULTA POR CÓDIGO (se você usar esse botão em outro form)
        # ==========================================================
        elif 'verificar_codigo' in request.form:
            codigo = (request.form.get('codigo_referencia') or '').strip()
            if not codigo:
                flash("❌ Informe um Código de Referência.", "danger")
                return redirect(url_for('inserir_dados'))

            conn = config.get_pg_connection()
            cur = conn.cursor()
            cur.execute("""
                SELECT fundo_colecao, titulo_conteudo, codigo_referencia, notacao,
                       localizacao_fisica, data_registro, data_localizacao, observacoes, divisao, localizado
                FROM tabela_padrao
                WHERE codigo_referencia = %s
            """, (codigo,))
            registro = cur.fetchone()
            cur.close()
            conn.close()

            if registro:
                modo = 'editar'
                flash("⚠️ Código encontrado. Editando registro.", "warning")
            else:
                flash("ℹ️ Código não encontrado. Novo registro.", "info")

            return render_template('inserir_dados.html', modo=modo, registro=registro)

        # ==========================================================
        # ✏️ 3) EDITAR EXISTENTE (por código)
        # ==========================================================
        elif 'editar_registro' in request.form:
            try:
                codigo = (request.form.get('codigo_referencia') or '').strip()
                divisao = (request.form.get('divisao') or '').strip().upper()
                if not codigo:
                    flash("❌ Código de Referência é obrigatório.", "danger")
                    return redirect(url_for('inserir_dados'))
                if not divisao:
                    flash("❌ Divisão é obrigatória.", "danger")
                    return redirect(url_for('inserir_dados'))

                data_localizacao = request.form.get('data_localizacao') or None
                if data_localizacao:
                    try:
                        data_localizacao = datetime.strptime(data_localizacao, "%Y-%m-%d").date()
                    except ValueError:
                        data_localizacao = None

                localizado = (request.form.get('localizado') == 'true')

                conn = config.get_pg_connection()
                cur = conn.cursor()
                cur.execute("""
                    UPDATE tabela_padrao SET
                        fundo_colecao = %s,
                        titulo_conteudo = %s,
                        notacao = %s,
                        localizacao_fisica = %s,
                        data_localizacao = %s,
                        observacoes = %s,
                        divisao = %s,
                        localizado = %s,
                        alterado_em = NOW(),
                        alterado_por = %s
                    WHERE codigo_referencia = %s
                """, (
                    request.form.get('fundo_colecao'),
                    request.form.get('titulo_conteudo'),
                    request.form.get('notacao'),
                    request.form.get('localizacao_fisica'),
                    data_localizacao,
                    request.form.get('observacoes'),
                    divisao,
                    localizado,
                    changed_by,
                    codigo
                ))
                conn.commit()
                cur.close()
                conn.close()

                flash("✅ Registro atualizado com sucesso!", "success")
                return redirect(url_for('inserir_dados'))

            except Exception as e:
                flash(f"❌ Erro ao editar registro: {e}", "danger")
                return redirect(url_for('inserir_dados'))

        # ==========================================================
        # ➕ 4) INSERÇÃO MANUAL (UPSERT) - botão inserir_manual
        # ==========================================================
        elif 'inserir_manual' in request.form:
            try:
                codigo = (request.form.get('codigo_referencia') or '').strip()
                notacao = (request.form.get('notacao') or '').strip()
                divisao = (request.form.get('divisao') or '').strip().upper()

                # 🔴 Divisão obrigatória
                if not divisao:
                    flash("❌ Divisão é obrigatória.", "danger")
                    return redirect(url_for('inserir_dados'))

                # 🔁 Regra XOR: ou código ou notação (exatamente um)
                if (not codigo and not notacao):
                    flash("❌ Informe Código de Referência OU Notação.", "danger")
                    return redirect(url_for('inserir_dados'))

                if (codigo and notacao):
                    flash("❌ Preencha apenas um dos campos: Código de Referência OU Notação.", "danger")
                    return redirect(url_for('inserir_dados'))

                data_registro = request.form.get('data_registro') or None
                if data_registro:
                    try:
                        data_registro = datetime.strptime(data_registro, "%Y-%m-%d").date()
                    except ValueError:
                        data_registro = None

                data_localizacao = request.form.get('data_localizacao') or None
                if data_localizacao:
                    try:
                        data_localizacao = datetime.strptime(data_localizacao, "%Y-%m-%d").date()
                    except ValueError:
                        data_localizacao = None

                localizado = (request.form.get('localizado') == 'true')

                dados = {
                    'fundo_colecao': request.form.get('fundo_colecao'),
                    'titulo_conteudo': request.form.get('titulo_conteudo'),
                    'codigo_referencia': codigo if codigo else None,
                    'notacao': notacao if notacao else None,
                    'localizacao_fisica': request.form.get('localizacao_fisica'),
                    'data_registro': data_registro,
                    'data_localizacao': data_localizacao,
                    'observacoes': request.form.get('observacoes'),
                    'divisao': divisao,
                    'localizado': localizado,
                    'inserido_por': changed_by
                }

                conn = config.get_pg_connection()
                cur = conn.cursor()

                cur.execute("""
                    INSERT INTO tabela_padrao (
                        fundo_colecao, titulo_conteudo, codigo_referencia, notacao,
                        localizacao_fisica, data_registro, data_localizacao,
                        observacoes, divisao, localizado, inserido_por
                    )
                    VALUES (
                        %(fundo_colecao)s, %(titulo_conteudo)s, %(codigo_referencia)s, %(notacao)s,
                        %(localizacao_fisica)s, %(data_registro)s, %(data_localizacao)s,
                        %(observacoes)s, %(divisao)s, %(localizado)s, %(inserido_por)s
                    )
                    ON CONFLICT (codigo_referencia)
                    WHERE (codigo_referencia IS NOT NULL AND BTRIM(codigo_referencia) <> '')
                    DO UPDATE
                    SET fundo_colecao = EXCLUDED.fundo_colecao,
                        titulo_conteudo = EXCLUDED.titulo_conteudo,
                        notacao = EXCLUDED.notacao,
                        localizacao_fisica = EXCLUDED.localizacao_fisica,
                        data_registro = EXCLUDED.data_registro,
                        data_localizacao = EXCLUDED.data_localizacao,
                        observacoes = EXCLUDED.observacoes,
                        divisao = EXCLUDED.divisao,
                        localizado = EXCLUDED.localizado,
                        alterado_em = NOW(),
                        alterado_por = %(inserido_por)s
                """, dados)

                conn.commit()
                cur.close()
                conn.close()

                try:
                    audit_log(
                        entity_type="REGISTRO",
                        entity_id=None,
                        action="INSERIR_OU_ATUALIZAR_MANUAL",
                        details={
                            "codigo_referencia": codigo if codigo else None,
                            "notacao": notacao if notacao else None,
                            "divisao": divisao,
                            "localizado": bool(localizado),
                            "origem": "FORM_MANUAL"
                        },
                        created_by=changed_by
                    )
                except Exception:
                    pass

                flash("✅ Registro inserido/atualizado com sucesso!", "success")
                return redirect(url_for('inserir_dados'))

            except Exception as e:
                flash(f"❌ Erro ao inserir registro: {e}", "danger")
                return redirect(url_for('inserir_dados'))


        # ==========================================================
        # ✅ fallback (garante que nunca retorna None)
        # ==========================================================
        flash("⚠️ Nenhuma ação reconhecida no formulário.", "warning")
        return redirect(url_for('inserir_dados'))

        # GET
    # Prefill vindos das telas de verificação
    codigo_prefill = (request.args.get('codigo') or '').strip()
    notacao_prefill = (request.args.get('notacao') or '').strip()

    # Padroniza visualmente em maiúsculas (opcional, mas ajuda)
    if codigo_prefill:
        codigo_prefill = codigo_prefill.upper()
    if notacao_prefill:
        notacao_prefill = notacao_prefill.upper()

    return render_template(
        'inserir_dados.html',
        modo=modo,
        codigo_prefill=codigo_prefill,
        notacao_prefill=notacao_prefill
    )









# Rota para download da planilha modelo (com validação)
@app.route('/download_modelo')
def download_modelo():
    if 'user' not in session:
        return redirect(url_for('login'))

    # Verifica permissões
    permission_check = require_permission('inserir_dados')
    if permission_check:
        return permission_check

    # Modelo (ordem deve bater com o seu import)
    df_modelo = pd.DataFrame(columns=[
        'Fundo/Coleção',
        'Título / Conteúdo',
        'Código de Referência',
        'Notação',
        'Localização física',
        'Data',
        'Data da localização',
        'Localizado',   # ✅ validação Sim/Não
        'Observações',
        'Divisão'       # ✅ validação DIJUD/DIPEX/DIPOP/DIDOC/DIDAS
    ])

    # Cria o Excel em memória
    output = BytesIO()
    df_modelo.to_excel(output, index=False)
    output.seek(0)

    # Abre com openpyxl para colocar validações
    wb = load_workbook(output)
    ws = wb.active

    # Letras das colunas conforme a ordem acima:
    # A Fundo/Coleção
    # B Título / Conteúdo
    # C Código de Referência
    # D Notação
    # E Localização física
    # F Data
    # G Data da localização
    # H Localizado
    # I Observações
    # J Divisão
    COL_LOCALIZADO = "H"
    COL_DIVISAO = "J"

    # Validação: Localizado -> Sim/Não
    dv_localizado = DataValidation(type="list", formula1='"Sim,Não"', allow_blank=True)
    dv_localizado.error = "Valor inválido. Use apenas: Sim ou Não."
    dv_localizado.errorTitle = "Localizado"
    dv_localizado.prompt = "Escolha Sim ou Não."
    dv_localizado.promptTitle = "Localizado"

    # Validação: Divisão -> lista fixa
    dv_divisao = DataValidation(type="list", formula1='"DIJUD,DIPEX,DIPOP,DIDOC,DIDAS"', allow_blank=False)
    dv_divisao.error = "Valor inválido. Selecione uma divisão da lista."
    dv_divisao.errorTitle = "Divisão"
    dv_divisao.prompt = "Selecione uma divisão."
    dv_divisao.promptTitle = "Divisão"

    ws.add_data_validation(dv_localizado)
    ws.add_data_validation(dv_divisao)

    # Aplica validações (da linha 2 até 5000)
    dv_localizado.add(f"{COL_LOCALIZADO}2:{COL_LOCALIZADO}5000")
    dv_divisao.add(f"{COL_DIVISAO}2:{COL_DIVISAO}5000")

    # Salva de volta em um novo buffer
    out2 = BytesIO()
    wb.save(out2)
    out2.seek(0)

    return send_file(
        out2,
        as_attachment=True,
        download_name="modelo_insercao_dados.xlsx",
        mimetype="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
    )

def get_divisoes_permitidas(username, is_admin):
    """
    - Admin: lista todas as divisões existentes no banco de DADOS (tabela_padrao)
    - Usuário comum: lista somente divisões liberadas no banco de SEGURANÇA (divisao_permissions)
    """

    # ADMIN -> buscar no banco de DADOS
    if int(is_admin) == 1:
        conn = get_pg_connection()
        cur = conn.cursor()
        cur.execute("""
            SELECT DISTINCT TRIM(divisao)
            FROM tabela_padrao
            WHERE divisao IS NOT NULL
              AND TRIM(divisao) <> ''
            ORDER BY TRIM(divisao);
        """)
        divisoes = [row[0] for row in cur.fetchall()]
        cur.close()
        conn.close()
        return divisoes

    # USUÁRIO COMUM -> buscar no banco de SEGURANÇA
    conn = get_postgres_connection()
    cur = conn.cursor()
    cur.execute("""
        SELECT DISTINCT TRIM(dp.divisao)
        FROM divisao_permissions dp
        WHERE dp.username = %s
        ORDER BY TRIM(dp.divisao);
    """, (username,))
    divisoes = [row[0] for row in cur.fetchall()]
    cur.close()
    conn.close()
    return divisoes




@app.route('/pesquisar_divisao', methods=['GET', 'POST'])
def pesquisar_divisao():
    if 'user' not in session:
        return redirect(url_for('login'))

    # 🔐 permissão
    permission_check = require_permission('pesquisar_divisao')
    if permission_check:
        return permission_check

    conn = config.get_pg_connection()
    cur = conn.cursor()

    # ✅ Carrega lista de divisões pro select
    cur.execute("""
        SELECT DISTINCT UPPER(TRIM(divisao)) AS divisao
        FROM tabela_padrao
        WHERE divisao IS NOT NULL AND TRIM(divisao) <> ''
        ORDER BY UPPER(TRIM(divisao));
    """)
    divisoes = [row[0] for row in cur.fetchall()]

    divisao_selecionada = None
    resultados = []
    mensagem = None
    total_nao_localizado = 0

    # ---------------------------------------
    # ✅ 1) Captura divisão selecionada
    # ---------------------------------------
    # Se vier por GET (?divisao=DIPEX), usa também
    if request.method == 'GET':
        divisao_selecionada = request.args.get('divisao')
        if divisao_selecionada:
            divisao_selecionada = divisao_selecionada.strip().upper()

    # Se vier por POST (form select), prioriza POST
    if request.method == 'POST':
        divisao_selecionada = request.form.get('divisao') or request.form.get('divisao_selecionada')
        if divisao_selecionada:
            divisao_selecionada = divisao_selecionada.strip().upper()

    # Se ainda não tem divisão selecionada, só renderiza a tela
    if not divisao_selecionada:
        cur.close()
        conn.close()
        return render_template(
            'pesquisar_divisao.html',
            divisoes=divisoes,
            divisao_selecionada=None,
            resultados=[],
            mensagem=None,
            total_nao_localizado=0
        )

    # ---------------------------------------
    # ✅ 2) Contagem de não localizados
    # ---------------------------------------
    cur.execute("""
        SELECT COUNT(*)
        FROM tabela_padrao
        WHERE UPPER(TRIM(divisao)) = %s
          AND COALESCE(localizado, FALSE) = FALSE
    """, (divisao_selecionada,))
    total_nao_localizado = cur.fetchone()[0] or 0

    # ---------------------------------------
    # ✅ 3) Filtro opcional (coluna + termo)
    # ---------------------------------------
    coluna = None
    termo = None

    if request.method == 'POST' and request.form.get('coluna') and request.form.get('termo'):
        coluna = request.form.get('coluna')
        termo = (request.form.get('termo') or '').strip()

    # colunas permitidas (proteção contra SQL injection)
    colunas_permitidas = {
        'todas',
        'fundo_colecao',
        'titulo_conteudo',
        'codigo_referencia',
        'notacao',
        'localizacao_fisica',
        'data_registro',
        'data_localizacao',
        'observacoes',
        'divisao'
    }

    if coluna and coluna not in colunas_permitidas:
        coluna = None

    # ---------------------------------------
    # ✅ 4) Consulta de resultados
    # ---------------------------------------
    base_select = """
        SELECT
            id,
            fundo_colecao,
            titulo_conteudo,
            codigo_referencia,
            notacao,
            localizacao_fisica,
            data_registro,
            data_localizacao,
            observacoes,
            UPPER(TRIM(divisao)) AS divisao,
            COALESCE(localizado, FALSE) AS localizado
        FROM tabela_padrao
        WHERE UPPER(TRIM(divisao)) = %s
    """

    params = [divisao_selecionada]

    if coluna and termo:
        if coluna == 'todas':
            base_select += """
              AND (
                COALESCE(fundo_colecao,'') ILIKE %s OR
                COALESCE(titulo_conteudo,'') ILIKE %s OR
                COALESCE(codigo_referencia,'') ILIKE %s OR
                COALESCE(notacao,'') ILIKE %s OR
                COALESCE(localizacao_fisica,'') ILIKE %s OR
                COALESCE(observacoes,'') ILIKE %s OR
                COALESCE(divisao,'') ILIKE %s
              )
            """
            like = f"%{termo}%"
            params.extend([like, like, like, like, like, like, like])
        else:
            base_select += f" AND COALESCE({coluna}, '') ILIKE %s "
            params.append(f"%{termo}%")

    base_select += " ORDER BY id DESC LIMIT 2000;"  # limite de segurança

    cur.execute(base_select, tuple(params))
    resultados = cur.fetchall()

    if not resultados:
        mensagem = "Nenhum resultado encontrado para essa divisão/filtro."

    cur.close()
    conn.close()

    return render_template(
        'pesquisar_divisao.html',
        divisoes=divisoes,
        divisao_selecionada=divisao_selecionada,
        resultados=resultados,
        mensagem=mensagem,
        total_nao_localizado=total_nao_localizado
    )
    





@app.route('/exportar_divisao')
def exportar_divisao():
    if 'user' not in session:
        return redirect(url_for('login'))
    
    # Verifica permissões
    permission_check = require_permission('pesquisar_divisao')  # Usa mesma permissão de pesquisar_divisao
    if permission_check:
        return permission_check
    
    divisao = request.args.get('divisao')
    coluna = request.args.get('coluna')
    termo = request.args.get('termo')

    conn = config.get_pg_connection()
    cur = conn.cursor()

    if divisao:
        if coluna and termo:
            # 🔹 Exportar somente registros filtrados
            query = f"""
                SELECT id, fundo_colecao, titulo_conteudo, codigo_referencia, notacao,
                       localizacao_fisica, data_registro, data_localizacao, observacoes, divisao
                FROM tabela_padrao
                WHERE TRIM(UPPER(divisao)) = TRIM(UPPER(%s))
                  AND {coluna} ILIKE %s
                ORDER BY id;
            """
            cur.execute(query, (divisao, f'%{termo}%'))
        else:
            # 🔹 Exportar todos os registros da divisão
            cur.execute("""
                SELECT id, fundo_colecao, titulo_conteudo, codigo_referencia, notacao,
                       localizacao_fisica, data_registro, data_localizacao, observacoes, divisao
                FROM tabela_padrao
                WHERE TRIM(UPPER(divisao)) = TRIM(UPPER(%s))
                ORDER BY id;
            """, (divisao,))
    else:
        flash("⚠️ Nenhuma divisão selecionada.", "warning")
        return redirect(url_for('pesquisar_divisao'))

    resultados = cur.fetchall()
    colunas = [desc[0] for desc in cur.description]
    cur.close()
    conn.close()

    # Gera o DataFrame e exporta
    df = pd.DataFrame(resultados, columns=colunas)
    filename = f"export_{divisao}_{datetime.now().strftime('%Y%m%d_%H%M%S')}.xlsx"
    file_path = os.path.join("static", "exports", filename)
    os.makedirs(os.path.dirname(file_path), exist_ok=True)
    df.to_excel(file_path, index=False)

    return send_file(file_path, as_attachment=True)








@app.route('/editar_registro/<int:id>', methods=['GET', 'POST'])
def editar_registro(id):
    if 'user' not in session:
        return redirect(url_for('login'))

    permission_check = require_permission('editar_registro')
    if permission_check:
        return permission_check

    changed_by = session['user']['username']
    DIVISOES_VALIDAS = {'DIJUD', 'DIPEX', 'DIPOP', 'DIDOC', 'DIDAS'}

    conn = config.get_pg_connection()
    cur = conn.cursor()

    if request.method == 'POST':
        try:
            fundo_colecao = request.form.get('fundo_colecao')
            titulo_conteudo = request.form.get('titulo_conteudo')
            codigo_referencia = request.form.get('codigo_referencia')
            notacao = request.form.get('notacao')
            localizacao_fisica = request.form.get('localizacao_fisica')

            data_registro = request.form.get('data_registro') or None
            data_localizacao = request.form.get('data_localizacao') or None

            observacoes = request.form.get('observacoes')

            divisao = (request.form.get('divisao') or '').strip().upper()
            if not divisao or divisao not in DIVISOES_VALIDAS:
                flash("❌ Divisão inválida. Selecione uma divisão válida.", "danger")
                return redirect(url_for('editar_registro', id=id))

            # ✅ Localizado (true/false do select)
            localizado = (request.form.get('localizado') == 'true')

            # Converte datas
            if data_registro:
                data_registro = datetime.strptime(data_registro, "%Y-%m-%d").date()

            if data_localizacao:
                data_localizacao = datetime.strptime(data_localizacao, "%Y-%m-%d").date()

            # ✅ auto-preenche data_localizacao quando localizado = true e data não foi informada
            if localizado and not data_localizacao:
                data_localizacao = date.today()

            cur.execute("""
                UPDATE tabela_padrao
                SET fundo_colecao = %s,
                    titulo_conteudo = %s,
                    codigo_referencia = %s,
                    notacao = %s,
                    localizacao_fisica = %s,
                    data_registro = %s,
                    data_localizacao = %s,
                    observacoes = %s,
                    divisao = %s,
                    localizado = %s,
                    alterado_em = NOW(),
                    alterado_por = %s
                WHERE id = %s
            """, (
                fundo_colecao, titulo_conteudo, codigo_referencia, notacao,
                localizacao_fisica, data_registro, data_localizacao,
                observacoes, divisao, localizado, changed_by, id
            ))

            conn.commit()
            flash("✅ Registro atualizado com sucesso!", "success")

            try:
                audit_log(
                    entity_type="REGISTRO",
                    entity_id=id,
                    action="EDITAR_REGISTRO",
                    details={
                        "divisao": divisao,
                        "codigo_referencia": codigo_referencia,
                        "localizado": bool(localizado),
                        "data_localizacao": str(data_localizacao) if data_localizacao else None
                    },
                    created_by=changed_by
                )
            except Exception:
                pass

            cur.close()
            conn.close()
            return redirect(url_for('pesquisar_divisao', divisao=divisao))

        except Exception as e:
            conn.rollback()
            cur.close()
            conn.close()
            flash(f"❌ Erro ao atualizar registro: {e}", "danger")
            return redirect(url_for('editar_registro', id=id))

    # GET registro
    cur.execute("""
        SELECT id, fundo_colecao, titulo_conteudo, codigo_referencia, notacao,
               localizacao_fisica, data_registro, data_localizacao, observacoes, divisao,
               localizado
        FROM tabela_padrao
        WHERE id = %s
    """, (id,))
    registro = cur.fetchone()

    if not registro:
        cur.close()
        conn.close()
        flash("❌ Registro não encontrado.", "danger")
        return redirect(url_for('pesquisar_divisao'))

    colunas = [
        'id', 'fundo_colecao', 'titulo_conteudo', 'codigo_referencia', 'notacao',
        'localizacao_fisica', 'data_registro', 'data_localizacao', 'observacoes', 'divisao',
        'localizado'
    ]
    registro_dict = dict(zip(colunas, registro))

    # GET tentativas
    cur.execute("""
        SELECT status, observacao, criado_por, criado_em
        FROM documento_tentativas
        WHERE registro_id = %s
        ORDER BY criado_em DESC
    """, (id,))
    tentativas = cur.fetchall()

    cur.close()
    conn.close()

    return render_template('editar_registro.html', registro=registro_dict, tentativas=tentativas)









@app.route('/editar_redirect', methods=['GET'])
def editar_redirect():
    if 'user' not in session:
        return redirect(url_for('login'))
    
    # Verifica permissões
    permission_check = require_permission('upload')
    if permission_check:
        return permission_check

    tabela = request.args.get('tabela')
    if not tabela:
        return redirect(url_for('upload'))
    # redireciona para a rota /editar/<tabela>
    return redirect(url_for('editar', tabela=tabela))

@app.route('/editar/<tabela>', methods=['GET', 'POST'])
def editar(tabela):
    if 'user' not in session:
        return redirect(url_for('login'))
    
    # Verifica permissões
    permission_check = require_permission('upload')
    if permission_check:
        return permission_check

    conn = get_pg_connection()
    cur = conn.cursor()

    # Campo de busca depende da tabela
    search_field = "codigo_ficha_docjud" if tabela == "codes_dijud" else "fundo_colecao"
    search_value = request.args.get('search', '').strip()

    # POST → atualização de registro
    if request.method == 'POST':
        record_id = request.form['id']
        coluna = request.form['coluna']
        valor = request.form['valor']

        query = f"UPDATE {tabela} SET {coluna} = %s, updated_at = NOW() WHERE id = %s"
        cur.execute(query, (valor, record_id))
        conn.commit()

        cur.close()
        conn.close()
        return redirect(url_for('editar', tabela=tabela, search=search_value))

    # GET → busca registros
    if search_value:
        cur.execute(
            f"SELECT * FROM {tabela} WHERE {search_field} ILIKE %s ORDER BY id LIMIT 50",
            (f"%{search_value}%",)
        )
    else:
        cur.execute(f"SELECT * FROM {tabela} ORDER BY id LIMIT 50")

    rows = cur.fetchall()
    colunas = [desc[0] for desc in cur.description]

    cur.close()
    conn.close()

    # índice do id
    if "id" in colunas:
        id_index = colunas.index("id")
    else:
        id_index = None

    # colunas visíveis (sem id e updated_at)
    display_cols = [c for c in colunas if c not in ("id", "updated_at")]

    # índices correspondentes às colunas visíveis
    col_index_map = [colunas.index(c) for c in display_cols]

    # linhas de dados visíveis
    rows_display = [
        [row[i] for i in col_index_map] for row in rows
    ]

    # ids separados (para hidden input)
    ids = [row[id_index] for row in rows] if id_index is not None else []

    return render_template(
        'editar.html',
        tabela=tabela,
        colunas=display_cols,
        rows=rows_display,
        ids=ids,
        search_field=search_field,
        search_value=search_value,
        id_index=id_index
    )


@app.route('/insights')
def insights():
    if 'user' not in session:
        return redirect(url_for('login'))
    
    # Verifica permissões
    permission_check = require_permission('insights')
    if permission_check:
        return permission_check
    return render_template('insights.html')
    

@app.route('/admin/permissoes', methods=['GET', 'POST'])
@login_required
def permissoes():
    divisoes = get_divisoes()

    return render_template(
        'permissoes.html',
        divisoes=divisoes,
        page_labels=page_labels
    )
def get_divisoes():
    conn = get_pg_connection()
    cur = conn.cursor()

    cur.execute("""
        SELECT DISTINCT divisao
        FROM tabela_padrao
        WHERE divisao IS NOT NULL
          AND TRIM(divisao) <> ''
        ORDER BY divisao;
    """)

    divisoes = [row[0] for row in cur.fetchall()]

    cur.close()
    conn.close()

    return divisoes
@app.route('/permissions/divisao/action', methods=['POST'])
def change_divisao_permission():

    if 'user' not in session:
        return jsonify(success=False, message="Sessão expirada"), 401

    if session['user']['is_admin'] != 1:
        return jsonify(success=False, message="Sem permissão"), 403

    data = request.get_json()

    username = data.get('username')
    divisao = data.get('divisao')
    action = data.get('action')  # GRANT | REMOVE

    if not username or not divisao or action not in ['GRANT', 'REMOVE']:
        return jsonify(success=False, message="Dados inválidos"), 400

    conn = get_postgres_connection()
    cur = conn.cursor()

    try:
        if action == 'GRANT':
            cur.execute("""
                INSERT INTO divisao_permissions (username, divisao)
                SELECT %s, %s
                WHERE NOT EXISTS (
                    SELECT 1 FROM divisao_permissions
                    WHERE username = %s AND divisao = %s
                )
            """, (username, divisao, username, divisao))

            audit_action = 'CONCEDIDO'

        else:
            cur.execute("""
                DELETE FROM divisao_permissions
                WHERE username = %s AND divisao = %s
            """, (username, divisao))

            audit_action = 'REMOVIDO'

        # 🔐 AUDITORIA (PADRÃO EXISTENTE)
        cur.execute("""
            INSERT INTO permissions_audit
            (page, username, action, changed_by)
            VALUES (%s, %s, %s, %s)
        """, (
            f'DIVISAO:{divisao}',
            username,
            audit_action,
            session['user']['username']
        ))

        conn.commit()

        return jsonify(
            success=True,
            message=f"Divisão {divisao} {audit_action.lower()}"
        )

    except Exception as e:
        conn.rollback()
        return jsonify(success=False, message=str(e)), 500

    finally:
        cur.close()
        conn.close()




# -------------------
# Página de Permissões
# -------------------

@app.route('/permissions')
def permissions():
    if 'user' not in session:
        return redirect(url_for('login'))

    if session['user']['is_admin'] != 1:
        return redirect(url_for('sem_permissao'))

    conn = get_postgres_connection()
    cur = conn.cursor()

    # usuários
    cur.execute("SELECT username FROM users ORDER BY username")
    all_usernames = [r[0] for r in cur.fetchall()]

    # páginas
    pages = [
        'home', 'search', 'inserir_dados', 'dashboard_divisao',
        'pesquisar_divisao', 'editar_registro', 'upload',
        'editar', 'insights', 'permissions'
    ]

    page_labels = {
        'home': 'Home',
        'home_v2': 'Home versão 2',
        'search': 'Pesquisa Docjud',
        'inserir_dados': 'Inserir Dados',
        'dashboard_divisao': 'Dashboard Divisão',
        'pesquisar_divisao': 'Pesquisar Divisão',
        'editar_registro': 'Editar Registro',
        'editar': 'Editar',
        'permissions': 'Permissões',
        'audit': 'Log de Auditoria'
    }

    # permissões por página
    cur.execute("SELECT page, username FROM page_permissions")
    page_permissions = {}
    for page, user in cur.fetchall():
        page_permissions.setdefault(page, []).append(user)

    # 🔹 divisões (AGORA FUNCIONA)
    divisoes = get_divisoes()

    # permissões por divisão
    cur.execute("SELECT username, divisao FROM divisao_permissions")
    divisao_permissions = [f"{u}::{d}" for u, d in cur.fetchall()]

    cur.close()
    conn.close()

    return render_template(
        'permissions.html',
        all_usernames=all_usernames,
        pages=pages,
        page_labels=page_labels,
        page_permissions=page_permissions,
        divisoes=divisoes,
        divisao_permissions=divisao_permissions
    )

@app.route('/permissions/page/bulk_update', methods=['POST'])
@login_required
def bulk_update_page_permissions():
    data = request.json

    username = data.get('username')
    new_pages = set(data.get('pages', []))  # páginas marcadas no frontend

    if not username:
        return jsonify(success=False, message="Usuário não informado")

    conn = get_pg_connection()
    cur = conn.cursor()

    # 🔹 Busca permissões atuais do usuário
    cur.execute("""
        SELECT page
        FROM page_permissions
        WHERE username = %s
    """, (username,))
    current_pages = {row[0] for row in cur.fetchall()}

    # 🔹 Diferenças
    pages_to_add = new_pages - current_pages
    pages_to_remove = current_pages - new_pages

    # 🔹 Concede novas permissões
    for page in pages_to_add:
        cur.execute("""
            INSERT INTO page_permissions (username, page)
            VALUES (%s, %s)
        """, (username, page))

        # 🔍 Auditoria (GRANT)
        cur.execute("""
            INSERT INTO permissions_audit
            (username, page, action, changed_by, changed_at)
            VALUES (%s, %s, 'GRANT', %s, %s)
        """, (
            username,
            page,
            current_user.username,
            datetime.now()
        ))

    # 🔹 Remove permissões
    for page in pages_to_remove:
        cur.execute("""
            DELETE FROM page_permissions
            WHERE username = %s AND page = %s
        """, (username, page))

        # 🔍 Auditoria (REMOVE)
        cur.execute("""
            INSERT INTO permissions_audit
            (username, page, action, changed_by, changed_at)
            VALUES (%s, %s, 'REMOVE', %s, %s)
        """, (
            username,
            page,
            current_user.username,
            datetime.now()
        ))

    conn.commit()
    cur.close()
    conn.close()

    return jsonify(
        success=True,
        message="Permissões atualizadas com sucesso"
    )

@app.route('/permissions_audit')
def permissions_audit():
    if 'user' not in session:
        return redirect(url_for('login'))

    # Apenas admin
    if session['user']['is_admin'] != 1:
        return redirect(url_for('sem_permissao'))

    conn = get_postgres_connection()
    cursor = conn.cursor()

    cursor.execute("""
        SELECT 
            page,
            username,
            action,
            changed_by,
            changed_at
        FROM permissions_audit
        ORDER BY changed_at DESC
        LIMIT 500
    """)

    logs = cursor.fetchall()

    cursor.close()
    conn.close()

    return render_template(
        'permissions_audit.html',
        logs=logs
    )

@app.route('/permissions/remove', methods=['POST'])
def remove_permission():
    if 'user' not in session:
        return jsonify(success=False, message="Não autenticado")

    if session['user']['is_admin'] != 1:
        return jsonify(success=False, message="Sem permissão")

    page = request.form.get('page')
    username = request.form.get('username')
    changed_by = session['user']['username']

    conn = get_postgres_connection()
    cursor = conn.cursor()

    cursor.execute(
        """
        DELETE FROM page_permissions
        WHERE page = %s AND username = %s
        """,
        (page, username)
    )

    cursor.execute(
        """
        INSERT INTO permissions_audit
        (page, username, action, changed_by)
        VALUES (%s, %s, 'REMOVIDO', %s)
        """,
        (page, username, changed_by)
    )

    conn.commit()
    cursor.close()
    conn.close()

    return jsonify(success=True)

@app.route('/permissions/action', methods=['POST'])
def permission_action():
    if 'user' not in session:
        return jsonify(success=False, message="Não autenticado"), 401

    if session['user'].get('is_admin') != 1:
        return jsonify(success=False, message="Sem permissão"), 403

    data = request.get_json(silent=True) or {}
    page = (data.get('page') or '').strip()
    username = (data.get('username') or '').strip()
    action = (data.get('action') or '').strip().upper()
    changed_by = session['user']['username']

    if not page or not username or action not in ('GRANT', 'REMOVE'):
        return jsonify(success=False, message="Dados inválidos"), 400

    try:
        if action == 'GRANT':
            changed = grant_permission(page, username)

            if changed:
                log_permission_audit(page, username, 'CONCEDIDO', changed_by)
                try:
                    audit_log(
                        entity_type="PERMISSAO",
                        entity_id=None,
                        action="CONCEDER_PERMISSAO",
                        details={"page": page, "username": username},
                        created_by=changed_by
                    )
                except Exception:
                    pass
                return jsonify(success=True, message="Permissão concedida com sucesso")
            else:
                return jsonify(success=True, message="Esse usuário já tinha permissão nessa página")

        elif action == 'REMOVE':
            changed = revoke_permission(page, username)

            if changed:
                log_permission_audit(page, username, 'REMOVIDO', changed_by)
                try:
                    audit_log(
                        entity_type="PERMISSAO",
                        entity_id=None,
                        action="REMOVER_PERMISSAO",
                        details={"page": page, "username": username},
                        created_by=changed_by
                    )
                except Exception:
                    pass
                return jsonify(success=True, message="Permissão removida com sucesso")
            else:
                return jsonify(success=True, message="Esse usuário já não tinha permissão nessa página")

    except Exception as e:
        return jsonify(success=False, message=str(e)), 500

    
@app.route('/permissions/page/action', methods=['POST'])
def permission_page_action():
    if 'user' not in session or session['user']['is_admin'] != 1:
        return jsonify(success=False, message="Sem permissão")

    data = request.json
    page = data['page']
    username = data['username']
    action = data['action']
    admin = session['user']['username']

    conn = get_postgres_connection()
    cur = conn.cursor()

    if action == 'GRANT':
        cur.execute("""
            INSERT INTO page_permissions (page, username)
            VALUES (%s, %s)
            ON CONFLICT DO NOTHING
        """, (page, username))

        cur.execute("""
            INSERT INTO permissions_audit
            (page, username, action, changed_by)
            VALUES (%s, %s, 'CONCEDIDO', %s)
        """, (page, username, admin))

    else:
        cur.execute("""
            DELETE FROM page_permissions
            WHERE page = %s AND username = %s
        """, (page, username))

        cur.execute("""
            INSERT INTO permissions_audit
            (page, username, action, changed_by)
            VALUES (%s, %s, 'REMOVIDO', %s)
        """, (page, username, admin))

    conn.commit()
    cur.close()
    conn.close()

    return jsonify(success=True, message="Permissão de página atualizada")



# -------------------
# Menu dinâmico
# -------------------
@app.context_processor
def inject_user_menu():
    if 'user' in session:
        # Mapeamento dos nomes exibidos no menu
        page_labels = {
            'home': 'Home',
            'home_v2': 'Home versão 2',
            #'upload': 'Upload',
            #'search': 'Pesquisa Docjud',        
            'dashboard_bi': 'Dashboard',
            'pesquisar_divisao': 'Pesquisar Divisão',      
            'permissions': 'Permissões',
            'insights': 'teste insights',
            #'inserir_dados': 'Inserir registros antigo'
            'permissions_audit': 'Auditoria',
            'audit': 'Log de Auditoria',
            'verificar_codigo': 'Inserir registro'
            
        }
        pages = ['home', 'home_v2', 'pesquisar_divisao', 'verificar_codigo', 'dashboard_bi', 'insights', 'permissions', 'permissions_audit', 'audit'] #ordem das páginas no menu (peginas retirada#, 'inserir_dados', 'search',
        menu = []
        for page in pages:
            if page == 'permissions' and session['user']['is_admin'] != 1:
                continue
            elif page != 'home' and not check_access(page):
                continue
            menu.append((page, page_labels.get(page, page.capitalize())))
        return dict(get_user_menu=lambda: menu, user=session['user'])
    return dict(get_user_menu=lambda: [], user=None)
def get_sqlserver_connection():
    return pyodbc.connect(
        'DRIVER={ODBC Driver 17 for SQL Server};'
        'SERVER=orion\\scriptcase;'
        'UID=pbi_docjud;'
        'PWD=pbiarquivo@1234;'
        'DATABASE=Docjud'  
    )

@app.route('/search')
def search():
    if 'user' not in session:
        return redirect(url_for('login'))
    
    # Verifica permissões
    permission_check = require_permission('search')
    if permission_check:
        return permission_check

    page = int(request.args.get('page', 1))
    per_page = 20
    offset = (page - 1) * per_page

    cod_ficha = request.args.get('cod_ficha', '')
    nl_numero = request.args.get('nl_numero', '')
    t_codref_sian = request.args.get('t_codref_sian', '')
    t_codref_pai = request.args.get('t_codref_pai', '')

    conn = get_sqlserver_connection()
    cursor = conn.cursor()

    where_clauses = []
    params = []

    if cod_ficha:
        where_clauses.append("COD_FICHA LIKE ?")
        params.append(f"%{cod_ficha}%")
    if nl_numero:
        where_clauses.append("NL_NUMERO LIKE ?")
        params.append(f"%{nl_numero}%")
    if t_codref_sian:
        where_clauses.append("T_CodReferenciaSIAN_ID LIKE ?")
        params.append(f"%{t_codref_sian}%")
    if t_codref_pai:
        where_clauses.append("T_codRefPaiSIAN_ID LIKE ?")
        params.append(f"%{t_codref_pai}%")

    where_sql = f"WHERE {' AND '.join(where_clauses)}" if where_clauses else ""

    paginated_sql = f"""
        SELECT * FROM (
            SELECT ROW_NUMBER() OVER (ORDER BY COD_FICHA) AS row_num,
                   COD_FICHA, DT_CADASTRO, TITULO, SOBRENOME, PRENOME, RESP_ID, PRENOME2, RESP2_ID,
                   ASSUNTO, ANO, ANOF, NL_NUMERO, NL_APELACAO, NL_CAIXA, NL_GAL, OBS,
                   PROCEDENCIA_ID, SERIE_ID,
                   T_CodReferenciaSIAN_ID, T_codRefPaiSIAN_ID, CodigoReferenciaPaiSIAN
            FROM tblFicha2
            {where_sql}
        ) AS numbered
        WHERE row_num > ? AND row_num <= ?
    """
    params.extend([offset, offset + per_page])
    cursor.execute(paginated_sql, params)

    columns = [column[0] for column in cursor.description]
    results = [dict(zip(columns, row)) for row in cursor.fetchall()]

    # Total para paginação
    count_sql = f"SELECT COUNT(*) FROM tblFicha2 {where_sql}"
    cursor.execute(count_sql, params[:-2])
    total = cursor.fetchone()[0]

    has_next = (offset + per_page) < total
    total_pages = (total + per_page - 1) // per_page

    cursor.close()
    conn.close()

    # ===== CARDS (somente tela inicial) =====
    total_registros = 0
    count_nao_localizado = 0
    percentual_nao_localizado = 0

    if not where_clauses:
        conn = get_sqlserver_connection()
        cursor = conn.cursor()

        # Total geral
        cursor.execute("SELECT COUNT(*) FROM tblFicha2")
        total_registros = cursor.fetchone()[0]

        # Não localizados
        cursor.execute("""
            SELECT COUNT(*) 
            FROM tblFicha2 
            WHERE OBS LIKE '%não localizado%'
               OR OBS LIKE '%Produtos não localizado%'
        """)
        count_nao_localizado = cursor.fetchone()[0]

        # Percentual
        if total_registros > 0:
            percentual_nao_localizado = round(
                (count_nao_localizado / total_registros) * 100, 2
            )

        cursor.close()
        conn.close()

    return render_template(
        'search.html',
        results=results,
        page=page,
        has_next=has_next,
        total_pages=total_pages,
        total_registros=total_registros,
        count_nao_localizado=count_nao_localizado,
        percentual_nao_localizado=percentual_nao_localizado
    )



@app.route('/search/export')
def export_search():
    if 'user' not in session:
        return redirect(url_for('login'))
    
    # Verifica permissões
    permission_check = require_permission('search')
    if permission_check:
        return permission_check

    # Pega os filtros
    cod_ficha = request.args.get('cod_ficha', '')
    nl_numero = request.args.get('nl_numero', '')
    t_codref_sian = request.args.get('t_codref_sian', '')
    t_codref_pai = request.args.get('t_codref_pai', '')
    query_nao_localizado = request.args.get('query', '')

    conn = get_sqlserver_connection()
    cursor = conn.cursor()

    base_sql = """
        SELECT COD_FICHA, DT_CADASTRO, TITULO, SOBRENOME, PRENOME, RESP_ID, PRENOME2, RESP2_ID, ASSUNTO, ANO, ANOF,
               NL_NUMERO, NL_APELACAO, NL_CAIXA, NL_GAL, OBS, PROCEDENCIA_ID, SERIE_ID,
               T_CodReferenciaSIAN_ID, T_codRefPaiSIAN_ID, CodigoReferenciaPaiSIAN
        FROM tblFicha2
    """

    where_clauses = []
    params = []

    if query_nao_localizado == "nao_localizado":
        where_clauses.append("OBS LIKE '%não localizado%' OR OBS LIKE '%Produtos não localizado%'")
    else:
        if cod_ficha:
            where_clauses.append("COD_FICHA LIKE ?")
            params.append(f"%{cod_ficha}%")
        if nl_numero:
            where_clauses.append("NL_NUMERO LIKE ?")
            params.append(f"%{nl_numero}%")
        if t_codref_sian:
            where_clauses.append("T_CodReferenciaSIAN_ID LIKE ?")
            params.append(f"%{t_codref_sian}%")
        if t_codref_pai:
            where_clauses.append("T_codRefPaiSIAN_ID LIKE ?")
            params.append(f"%{t_codref_pai}%")

    where_sql = f"WHERE {' AND '.join(where_clauses)}" if where_clauses else ""
    full_sql = base_sql + " " + where_sql

    cursor.execute(full_sql, params)
    columns = [column[0] for column in cursor.description]
    rows = [dict(zip(columns, row)) for row in cursor.fetchall()]
    cursor.close()
    conn.close()

    # Cria DataFrame
    if rows:
        df = pd.DataFrame(rows)
    else:
        # Garantir que pelo menos o cabeçalho exista
        df = pd.DataFrame(columns=columns)

    output = io.BytesIO()
    with pd.ExcelWriter(output, engine='openpyxl') as writer:
        df.to_excel(writer, index=False, sheet_name="Resultados")
    output.seek(0)

    return send_file(
        output,
        download_name="resultados.xlsx",
        as_attachment=True,
        mimetype='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet'
    )

@app.route('/dashboard-bi')
def dashboard_bi():
    return render_template('dashboard_BI.html')

from flask import redirect, url_for, render_template, request, flash

@app.route('/verificar_codigo', methods=['GET', 'POST'])
def verificar_codigo():
    if 'user' not in session:
        return redirect(url_for('login'))

    permission_check = require_permission('inserir_dados')
    if permission_check:
        return permission_check

    registro = None
    codigo = None
    notacao = None
    nao_encontrado = False
    tipo_busca = None  # "codigo" ou "notacao"

    if request.method == 'POST':
        codigo = (request.form.get('codigo_referencia') or '').strip()
        notacao = (request.form.get('notacao') or '').strip()

        # ✅ valida: precisa ter pelo menos um
        if not codigo and not notacao:
            flash("❌ Informe um Código de Referência ou uma Notação.", "danger")
            return redirect(url_for('verificar_codigo'))

        conn = config.get_pg_connection()
        cur = conn.cursor()

        try:
            if codigo:
                tipo_busca = "codigo"
                codigo_busca = codigo.upper()

                cur.execute("""
                    SELECT id, fundo_colecao, titulo_conteudo, codigo_referencia, notacao,
                           localizacao_fisica, data_registro, data_localizacao, observacoes, divisao, localizado
                    FROM tabela_padrao
                    WHERE UPPER(TRIM(codigo_referencia)) = %s
                    ORDER BY id DESC
                    LIMIT 1
                """, (codigo_busca,))

            else:
                tipo_busca = "notacao"
                notacao_busca = notacao.upper()

                cur.execute("""
                    SELECT id, fundo_colecao, titulo_conteudo, codigo_referencia, notacao,
                           localizacao_fisica, data_registro, data_localizacao, observacoes, divisao, localizado
                    FROM tabela_padrao
                    WHERE UPPER(TRIM(notacao)) = %s
                    ORDER BY id DESC
                    LIMIT 1
                """, (notacao_busca,))

            row = cur.fetchone()

        finally:
            cur.close()
            conn.close()

        if row:
            registro = {
                'id': row[0],
                'fundo_colecao': row[1],
                'titulo_conteudo': row[2],
                'codigo_referencia': row[3],
                'notacao': row[4],
                'localizacao_fisica': row[5],
                'data_registro': row[6],
                'data_localizacao': row[7],
                'observacoes': row[8],
                'divisao': row[9],
                'localizado': row[10],
            }
        else:
            nao_encontrado = True
            if tipo_busca == "codigo":
                flash("ℹ️ Código não encontrado. Você pode inserir um novo registro com esse código.", "warning")
            else:
                flash("ℹ️ Notação não encontrada. Você pode inserir um novo registro com essa notação.", "warning")

    return render_template(
        'verificar_codigo.html',
        registro=registro,
        codigo=codigo,
        notacao=notacao,
        tipo_busca=tipo_busca,
        nao_encontrado=nao_encontrado
    )




@app.route('/editar_registro_cod_ref/<codigo>', methods=['GET', 'POST'])
def editar_registro_cod_ref(codigo):

    if 'user' not in session:
        return redirect(url_for('login'))

    conn = config.get_pg_connection()
    cur = conn.cursor()

    if request.method == 'POST':
        cur.execute("""
            UPDATE tabela_padrao SET
                fundo_colecao=%s,
                titulo_conteudo=%s,
                notacao=%s,
                localizacao_fisica=%s,
                data_localizacao=%s,
                observacoes=%s,
                divisao=%s
            WHERE codigo_referencia=%s
        """, (
            request.form['fundo_colecao'],
            request.form['titulo_conteudo'],
            request.form['notacao'],
            request.form['localizacao_fisica'],
            request.form['data_localizacao'] or None,
            request.form['observacoes'],
            request.form['divisao'],
            codigo
        ))
        conn.commit()
        cur.close()
        conn.close()
        flash("✅ Registro atualizado com sucesso!", "success")
        return redirect(url_for('home'))

    cur.execute("""
        SELECT id, fundo_colecao, titulo_conteudo, codigo_referencia,
               notacao, localizacao_fisica, data_registro,
               data_localizacao, observacoes, divisao
        FROM tabela_padrao
        WHERE codigo_referencia=%s
    """, (codigo,))
    row = cur.fetchone()
    cur.close()
    conn.close()

    if not row:
        flash("Registro não encontrado.", "danger")
        return redirect(url_for('verificar_codigo'))

    registro = {
        'id': row[0],
        'fundo_colecao': row[1],
        'titulo_conteudo': row[2],
        'codigo_referencia': row[3],
        'notacao': row[4],
        'localizacao_fisica': row[5],
        'data_registro': row[6],
        'data_localizacao': row[7],
        'observacoes': row[8],
        'divisao': row[9],
    }

    return render_template(
        'editar_registro.html',
        registro=registro
    )


@app.route('/nao_localizado')
def nao_localizado():
    if 'user' not in session:
        return redirect(url_for('login'))
    
    # Verifica permissões
    permission_check = require_permission('search')
    if permission_check:
        return permission_check

    conn = get_sqlserver_connection()
    cursor = conn.cursor()
    sql = """
        SELECT COD_FICHA, DT_CADASTRO, TITULO, SOBRENOME, PRENOME, RESP_ID, PRENOME2, RESP2_ID, ASSUNTO, ANO, ANOF, 
               NL_NUMERO, NL_APELACAO, NL_CAIXA, NL_GAL, OBS, PROCEDENCIA_ID, SERIE_ID, 
               T_CodReferenciaSIAN_ID, T_codRefPaiSIAN_ID, CodigoReferenciaPaiSIAN
        FROM tblFicha2
        WHERE OBS LIKE '%não localizado%' OR OBS LIKE '%Produtos não localizado%'
    """
    cursor.execute(sql)
    columns = [column[0] for column in cursor.description]
    results = [dict(zip(columns, row)) for row in cursor.fetchall()]
    cursor.close()
    conn.close()

    return render_template(
        'search.html',
        results=results,
        page=1,
        has_next=False,
        count_nao_localizado=len(results)
    )
@app.route("/insights")
def insights_view():
    return render_template(
        "insights.html",
        title="Insights - Cadê meu DOC"
    )

@app.route('/registro/<int:registro_id>/tentativa', methods=['POST'])
def registrar_tentativa(registro_id):
    if 'user' not in session:
        return jsonify(success=False, message="Não autenticado")

    # Permissão: quem edita pode registrar tentativa
    permission_check = require_permission('editar_registro')
    if permission_check:
        return jsonify(success=False, message="Sem permissão")

    data = request.get_json(silent=True) or {}
    status = (data.get('status') or 'PROCURANDO').strip().upper()
    observacao = (data.get('observacao') or '').strip()
    criado_por = session['user']['username']

    if status not in ('PROCURANDO', 'NAO_ENCONTRADO', 'ENCONTRADO'):
        return jsonify(success=False, message="Status inválido.")

    try:
        conn = config.get_pg_connection()
        cur = conn.cursor()
        
        cur.execute("SELECT current_database(), current_schema(), inet_server_addr(), inet_server_port();")
        print("DEBUG POST tentativa ->", cur.fetchone())


        # ✅ grava tentativa no registro certo
        cur.execute("""
            INSERT INTO documento_tentativas (registro_id, status, observacao, criado_por, criado_em)
            VALUES (%s, %s, %s, %s, NOW())
        """, (registro_id, status, observacao, criado_por))

        # ✅ se encontrou, marca o registro como localizado (opcional, mas faz sentido)
        if status == 'ENCONTRADO':
            cur.execute("""
                UPDATE tabela_padrao
                SET localizado = TRUE,
                    alterado_em = NOW(),
                    alterado_por = %s
                WHERE id = %s
            """, (criado_por, registro_id))
            cur.execute("SELECT COUNT(*) FROM public.documento_tentativas WHERE registro_id=%s", (registro_id,))
            print("DEBUG POST tentativa count (mesma conn) ->", cur.fetchone()[0])

        conn.commit()
        cur.close()
        conn.close()

        # auditoria (não quebra o fluxo)
        try:
            audit_log(
                entity_type="TENTATIVA",
                entity_id=registro_id,
                action="REGISTRAR_TENTATIVA",
                details={"status": status, "observacao": observacao},
                created_by=criado_por
            )
        except Exception:
            pass

        return jsonify(success=True, message="✅ Tentativa registrada com sucesso!")

    except Exception as e:
        try:
            conn.rollback()
        except Exception:
            pass
        return jsonify(success=False, message=f"Erro ao registrar tentativa: {e}")




@app.route('/audit', methods=['GET'])
def audit():
    if 'user' not in session:
        return redirect(url_for('login'))

    # ✅ somente admin
    if session['user'].get('is_admin') != 1:
        return redirect(url_for('sem_permissao'))

    # filtros (query string)
    q = (request.args.get('q') or '').strip()  # busca livre (usuario, acao, entidade)
    entity_type = (request.args.get('entity_type') or '').strip().upper()
    action = (request.args.get('action') or '').strip().upper()
    created_by = (request.args.get('created_by') or '').strip()
    date_from = (request.args.get('date_from') or '').strip()  # YYYY-MM-DD
    date_to = (request.args.get('date_to') or '').strip()      # YYYY-MM-DD

    # paginação
    per_page = 25
    page = request.args.get('page', 1, type=int)
    if page < 1:
        page = 1
    offset = (page - 1) * per_page

    where = []
    params = []

    if entity_type:
        where.append("entity_type = %s")
        params.append(entity_type)

    if action:
        where.append("action = %s")
        params.append(action)

    if created_by:
        where.append("created_by ILIKE %s")
        params.append(f"%{created_by}%")

    # datas inclusivas (from 00:00, to 23:59:59)
    if date_from:
        where.append("created_at >= %s::date")
        params.append(date_from)

    if date_to:
        where.append("created_at < (%s::date + interval '1 day')")
        params.append(date_to)

    # busca livre: procura em created_by, action, entity_type e details::text
    if q:
        where.append("""
            (
                created_by ILIKE %s OR
                action ILIKE %s OR
                entity_type ILIKE %s OR
                details::text ILIKE %s
            )
        """)
        params.extend([f"%{q}%"] * 4)

    where_sql = ("WHERE " + " AND ".join(where)) if where else ""

    conn = get_postgres_connection()
    cur = conn.cursor()

    # total para paginação
    cur.execute(f"SELECT COUNT(*) FROM system_audit {where_sql}", params)
    total = cur.fetchone()[0]
    total_pages = max(1, (total + per_page - 1) // per_page)

    # dados da página
    cur.execute(f"""
        SELECT id, created_at, created_by, entity_type, entity_id, action, details
        FROM system_audit
        {where_sql}
        ORDER BY created_at DESC
        LIMIT %s OFFSET %s
    """, params + [per_page, offset])

    rows = cur.fetchall()

    # opções para dropdowns (distintos)
    cur.execute("SELECT DISTINCT entity_type FROM system_audit ORDER BY entity_type")
    entity_types = [r[0] for r in cur.fetchall()]

    cur.execute("SELECT DISTINCT action FROM system_audit ORDER BY action")
    actions = [r[0] for r in cur.fetchall()]

    cur.close()
    conn.close()

    # normaliza detalhes (já vem dict se psycopg2 estiver configurado; senão, vem string/json)
    audits = []
    for r in rows:
        audits.append({
            "id": r[0],
            "created_at": r[1],
            "created_by": r[2],
            "entity_type": r[3],
            "entity_id": r[4],
            "action": r[5],
            "details": r[6]
        })

    return render_template(
        "audit.html",
        audits=audits,
        total=total,
        page=page,
        total_pages=total_pages,
        per_page=per_page,
        q=q,
        entity_type=entity_type,
        action=action,
        created_by=created_by,
        date_from=date_from,
        date_to=date_to,
        entity_types=entity_types,
        actions=actions
    )

# -------------------
if __name__ == '__main__':
    app.run(debug=True, host='0.0.0.0', port=5001)
