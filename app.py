# app.py
from flask import Flask, render_template, request, redirect, url_for, session, jsonify, flash, Blueprint
from werkzeug.utils import secure_filename
import mysql.connector
from werkzeug.security import check_password_hash
import config
import pyodbc
import pandas as pd
from flask import send_file
import io
import psycopg2
import os
import re
import unicodedata

app = Flask(__name__)
app.secret_key = config.SECRET_KEY

# -------------------
# Conexões
# -------------------
def get_mysql_connection():
    return mysql.connector.connect(
        host=config.MYSQL_CONFIG['host'],
        user=config.MYSQL_CONFIG['user'],
        password=config.MYSQL_CONFIG['password'],
        database=config.MYSQL_CONFIG['database']
    )
def get_pg_connection():
    return psycopg2.connect(
        host=config.PG_CONFIG['host'],
        port=config.PG_CONFIG['port'],
        database=config.PG_CONFIG['database'],
        user=config.PG_CONFIG['user'],
        password=config.PG_CONFIG['password']
    )

# -------------------
# Permissões
# -------------------
def load_page_permissions():
    conn = get_mysql_connection()
    cursor = conn.cursor(dictionary=True)
    cursor.execute("SELECT * FROM page_permissions")  # tabela de permissões
    rows = cursor.fetchall()
    cursor.close()
    conn.close()

    permissions = {}
    for row in rows:
        permissions[row['page']] = row['allowed_titles'].split(',') if row['allowed_titles'] else []
    return permissions

def save_page_permissions(page, titles):
    conn = get_mysql_connection()
    cursor = conn.cursor()
    titles_str = ','.join(titles)
    cursor.execute("""
        INSERT INTO page_permissions (page, allowed_titles)
        VALUES (%s, %s)
        ON DUPLICATE KEY UPDATE allowed_titles = VALUES(allowed_titles)
    """, (page, titles_str))
    conn.commit()
    cursor.close()
    conn.close()

def check_access(page):
    if 'user' not in session:
        return False
    if session['user']['is_admin'] == 1:
        return True
    permissions = load_page_permissions()
    allowed_titles = permissions.get(page, [])
    return session['user']['title'].lower() in [t.lower() for t in allowed_titles]

def get_all_titles():
    conn = get_mysql_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT DISTINCT title FROM user")
    rows = cursor.fetchall()
    cursor.close()
    conn.close()
    # Retorna lista de títulos (apenas o valor, não tupla)
    return [row[0] for row in rows if row[0]]

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
# -------------------
# Rotas
# -------------------
@app.route('/', methods=['GET', 'POST'])
def login():
    if request.method == 'POST':
        username = request.form['username']
        password = request.form['password']

        conn = get_mysql_connection()
        cursor = conn.cursor(dictionary=True)
        cursor.execute("SELECT * FROM `user` WHERE username=%s", (username,))
        user = cursor.fetchone()
        cursor.close()
        conn.close()

        if not user:
            return "Usuário não encontrado", 404

        # Admins não precisam validar departamento, usuários comuns precisam de 'COPRA'
        if user['is_admin'] == 1 or ('COPRA' in user['department'] and check_password_hash(user['password'], password)):
            session['user'] = user
            return redirect(url_for('home'))
        else:
            return "Acesso negado", 403

    return render_template('login.html')

@app.route('/home')
def home():
    if 'user' not in session:
        return redirect(url_for('login'))
    return render_template('home.html')

@app.route('/logout')
def logout():
    session.pop('user', None)
    return redirect(url_for('login'))

# Mapear abas -> tabelas
TABELAS_VALIDAS = {
    "CODES_DIJUD": "codes_dijud",
    "CODES_DIDOP": "codes_didop",
    "CODES_DIPEX": "codes_dipex",
    "CODAC_DIDAS": "codac_didas",
    "CODAC_DIDOC": "codac_didoc"
}

# Mapeamento por tabela


#upload_bp = Blueprint("upload", __name__)

@app.route('/upload', methods=['GET', 'POST'])
def upload():
    if not check_access('upload'):
        return "Acesso negado", 403
    if request.method == 'POST':
        file = request.files['file']
        if not file:
            flash("Nenhum arquivo enviado", "danger")
            return redirect(url_for('upload'))
        try:
            # Lê SEM usar cabeçalho, mas depois pula a primeira linha
            df_dict = pd.read_excel(file, sheet_name=None, engine="openpyxl", header=None)
            
            conn = get_pg_connection()
            cur = conn.cursor()
            
            for sheet_name, df in df_dict.items():
                tabela = sheet_name.strip().lower()
                tabelas_validas = ["codes_dijud", "codes_didop", "codes_dipex", "codac_didas", "codac_didoc"]
                
                if tabela not in tabelas_validas:
                    flash(f"Aba {sheet_name} não corresponde a nenhuma tabela válida!", "danger")
                    continue
                
                # PULA A PRIMEIRA LINHA (cabeçalho)
                df = df.iloc[1:]
                
                if len(df) == 0:
                    flash(f"Aba {sheet_name} não possui dados após o cabeçalho!", "warning")
                    continue
                
                # Busca as colunas reais do banco de dados
                cur.execute(f"""
                    SELECT column_name 
                    FROM information_schema.columns 
                    WHERE table_name = '{tabela}'
                    AND column_name NOT IN ('id', 'updated_at', 'created_at')
                    ORDER BY ordinal_position
                """)
                colunas_banco = [row[0] for row in cur.fetchall()]
                
                if len(colunas_banco) == 0:
                    flash(f"Não foi possível obter colunas da tabela {tabela}!", "danger")
                    continue
                
                # Verifica se o número de colunas bate
                if len(df.columns) != len(colunas_banco):
                    flash(f"Aba {sheet_name}: número de colunas ({len(df.columns)}) não bate com o banco ({len(colunas_banco)})!", "danger")
                    continue
                
                # Debug
                print(f"Tabela: {tabela}")
                print(f"Colunas do banco: {colunas_banco}")
                print(f"Primeiras linhas (após pular cabeçalho):\n{df.head()}")
                
                # Monta SQL
                placeholders = ", ".join(["%s"] * len(colunas_banco))
                insert_sql = f'INSERT INTO {tabela} ({", ".join(colunas_banco)}) VALUES ({placeholders})'
                
                # Inserir linha a linha mantendo os dados originais
                for _, row in df.iterrows():
                    valores = list(row)
                    cur.execute(insert_sql, valores)
            
            conn.commit()
            cur.close()
            conn.close()
            flash("Upload realizado com sucesso!", "success")
            return redirect(url_for('upload'))
            
        except Exception as e:
            flash(f"Erro no upload: {str(e)}", "danger")
            return redirect(url_for('upload'))
            
    return render_template('upload.html')

@app.route('/editar_redirect', methods=['GET'])
def editar_redirect():
    if not check_access('upload'):
        return "Acesso negado", 403

    tabela = request.args.get('tabela')
    if not tabela:
        return redirect(url_for('upload'))
    # redireciona para a rota /editar/<tabela>
    return redirect(url_for('editar', tabela=tabela))

@app.route('/editar/<tabela>', methods=['GET', 'POST'])
def editar(tabela):
    if not check_access('upload'):
        return "Acesso negado", 403

    conn = get_pg_connection()
    cur = conn.cursor()

    # Campos de busca
    search_field = "codigo_ficha_docjud" if tabela == "codes_dijud" else "fundo_colecao"
    search_value = request.args.get('search', '').strip()

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
        cur.execute(f"SELECT * FROM {tabela} WHERE {search_field} ILIKE %s ORDER BY id LIMIT 50", (f"%{search_value}%",))
    else:
        cur.execute(f"SELECT * FROM {tabela} ORDER BY id LIMIT 50")

    rows = cur.fetchall()
    colunas = [desc[0] for desc in cur.description]

    cur.close()
    conn.close()

    # Remove id e updated_at da exibição
    if "id" in colunas: 
        id_index = colunas.index("id")
    else:
        id_index = None

    display_cols = [c for c in colunas if c not in ("id", "updated_at")]

    return render_template(
        'editar.html',
        tabela=tabela,
        colunas=display_cols,
        rows=rows,
        search_field=search_field,
        search_value=search_value,
        id_index=id_index
    )



#@app.route('/search')
#def search():
#    if not check_access('search'):
#        return "Acesso negado", 403
#    return render_template('search.html')

@app.route('/insights')
def insights():
    if not check_access('insights'):
        return "Acesso negado", 403
    return render_template('insights.html')

# -------------------
# Página de Permissões
# -------------------

@app.route('/permissions', methods=['GET', 'POST'])
def permissions():
    if not check_access('permissions'):
        return jsonify(success=False, message="Acesso negado"), 403

    if request.method == 'POST':
        # Recebe os dados do formulário
        data = request.form.to_dict(flat=False)  # flat=False para pegar listas
        # Exemplo: {'home': ['Título 1', 'Título 2'], 'search': ['Título 3']}
        try:
            for page, titles in data.items():
                save_page_permissions(page, titles)  # Função que salva no banco
            return jsonify(success=True, message="Permissões salvas com sucesso!")
        except Exception as e:
            return jsonify(success=False, message=f"Erro ao salvar permissões: {e}")
    else:
        # GET: renderiza a página normalmente
        pages = ['home', 'upload', 'search', 'insights', 'permissions']
        all_titles = get_all_titles()  # Função que retorna todos os títulos possíveis
        page_permissions = load_page_permissions()  # Função que carrega permissões atuais
        return render_template('permissions.html', pages=pages, all_titles=all_titles, page_permissions=page_permissions)

# -------------------
# Menu dinâmico
# -------------------
@app.context_processor
def inject_user_menu():
    if 'user' in session:
        # Mapeamento dos nomes exibidos no menu
        page_labels = {
            'home': 'Home',
            'upload': 'Upload',
            'search': 'Pesquisa',        
            'insights': 'Dashboard',     
            'permissions': 'Permissões'  
        }
        pages = ['home', 'upload', 'search', 'insights', 'permissions']
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
    if not check_access('search'):
        return "Acesso negado", 403

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
                   COD_FICHA, DT_CADASTRO, TITULO, SOBRENOME, PRENOME, RESP_ID, PRENOME2, RESP2_ID, ASSUNTO, ANO, ANOF,
                   NL_NUMERO, NL_APELACAO, NL_CAIXA, NL_GAL, OBS, PROCEDENCIA_ID, SERIE_ID,
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

    # Count total para paginação
    count_sql = f"SELECT COUNT(*) FROM tblFicha2 {where_sql}"
    cursor.execute(count_sql, params[:-2])
    total = cursor.fetchone()[0]
    has_next = (offset + per_page) < total
    total_pages = (total + per_page - 1) // per_page  # arredonda pra cima

    cursor.close()
    conn.close()

    # Conta os "não localizados"
    count_nao_localizado = 0
    if not where_clauses:  # só na tela inicial
        conn = get_sqlserver_connection()
        cursor = conn.cursor()
        cursor.execute("SELECT COUNT(*) FROM tblFicha2 WHERE OBS LIKE '%não localizado%' OR OBS LIKE '%Produtos não localizado%'")
        count_nao_localizado = cursor.fetchone()[0]
        cursor.close()
        conn.close()

    return render_template('search.html', results=results, page=page, has_next=has_next, total_pages=total_pages, count_nao_localizado=count_nao_localizado)


@app.route('/search/export')
def export_search():
    if not check_access('search'):
        return "Acesso negado", 403

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




@app.route('/nao_localizado')
def nao_localizado():
    if not check_access('search'):
        return "Acesso negado", 403

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

# -------------------
if __name__ == '__main__':
    app.run(debug=True, host='0.0.0.0', port=5000)
