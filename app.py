# app.py
from flask import Flask, render_template, request, redirect, url_for, session, jsonify, flash, Blueprint,send_file
from werkzeug.utils import secure_filename
import mysql.connector
from werkzeug.security import check_password_hash
import config
import pyodbc
import pandas as pd
from flask import send_file
import io
import psycopg2
import unicodedata
from io import BytesIO
from datetime import datetime, date
import os

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
    # exige login
    if 'user' not in session:
        return redirect(url_for('login'))

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
@app.route('/dashboard_divisao', methods=['GET', 'POST'])
def dashboard_divisao():
    if 'user' not in session:
        return redirect(url_for('login'))

    conn = None
    divisoes = []
    divisao_selecionada = None
    dados_divisao = []
    mensagem = None

    try:
        conn = config.get_pg_connection()
        cur = conn.cursor()

        # lista de divisões disponíveis
        cur.execute("""
            SELECT DISTINCT TRIM(divisao)
            FROM tabela_padrao
            WHERE divisao IS NOT NULL AND TRIM(divisao) <> ''
            ORDER BY TRIM(divisao);
        """)
        divisoes = [row[0] for row in cur.fetchall()]

        # se o usuário escolheu uma divisão
        if request.method == 'POST':
            divisao_selecionada = request.form.get('divisao')
            if divisao_selecionada:
                cur.execute("""
                    SELECT id, fundo_colecao, titulo_conteudo, codigo_referencia, notacao,
                           localizacao_fisica, data_registro, data_localizacao, observacoes
                    FROM tabela_padrao
                    WHERE TRIM(divisao) = %s;
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

    return render_template(
        'dashboard_divisao.html',
        divisoes=divisoes,
        divisao_selecionada=divisao_selecionada,
        dados_divisao=dados_divisao,
        mensagem=mensagem
    )

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


@app.route('/inserir_dados', methods=['GET', 'POST'])
def inserir_dados():
    if request.method == 'POST':
        # 🔹 Se veio upload de planilha
        if 'upload_planilha' in request.form:
            file = request.files.get('file')
            if file:
                try:
                    df = pd.read_excel(file)

                    # 🔹 Normaliza nomes das colunas para minúsculas e sem acentos
                    df.columns = (
                        df.columns
                        .str.strip()
                        .str.lower()
                        .str.replace('ç', 'c')
                        .str.replace('ã', 'a')
                        .str.replace('â', 'a')
                        .str.replace('á', 'a')
                        .str.replace('í', 'i')
                        .str.replace('é', 'e')
                        .str.replace('õ', 'o')
                        .str.replace('ô', 'o')
                        .str.replace('ú', 'u')
                    )

                    # 🔹 Converte colunas de data
                    for col in ['data', 'data da localizacao']:
                        if col in df.columns:
                            df[col] = pd.to_datetime(df[col], errors='coerce').dt.date

                    # 🔹 Substitui NaN, NaT e strings vazias por None
                    df = df.replace({pd.NaT: None}).where(pd.notnull(df), None)

                    conn = config.get_pg_connection()
                    cur = conn.cursor()

                    # 🔹 Limpa todos os registros antes de inserir (segurança)
                    cur.execute("DELETE FROM tabela_padrao;")

                    # 🔹 Garante que a coluna data_registro exista
                    cur.execute("""
                        DO $$
                        BEGIN
                            IF NOT EXISTS (
                                SELECT 1 FROM information_schema.columns
                                WHERE table_name='tabela_padrao' AND column_name='data_registro'
                            ) THEN
                                ALTER TABLE tabela_padrao ADD COLUMN data_registro DATE DEFAULT CURRENT_DATE;
                            END IF;
                        END;
                        $$;
                    """)

                    # 🔹 Insere os dados
                    for _, row in df.iterrows():
                        cur.execute("""
                            INSERT INTO tabela_padrao (
                                fundo_colecao, titulo_conteudo, codigo_referencia, notacao,
                                localizacao_fisica, data_registro, data_localizacao, observacoes, divisao
                            ) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s)
                        """, (
                            row.get('fundo/colecao'),
                            row.get('titulo / conteudo'),
                            row.get('codigo de referencia'),
                            row.get('notacao'),
                            row.get('localizacao fisica'),
                            date.today(),  # data_registro automática
                            row.get('data da localizacao'),
                            row.get('observacoes'),
                            row.get('divisao')
                        ))

                    conn.commit()
                    cur.close()
                    conn.close()

                    flash("✅ Planilha inserida com sucesso!", "success")
                    return redirect(url_for('inserir_dados'))

                except Exception as e:
                    flash(f"❌ Erro ao inserir planilha: {e}", "danger")
                    return redirect(url_for('inserir_dados'))

        # 🔹 Se veio do formulário manual
        else:
            dados = {
                'fundo_colecao': request.form.get('fundo_colecao'),
                'titulo_conteudo': request.form.get('titulo_conteudo'),
                'codigo_referencia': request.form.get('codigo_referencia'),
                'notacao': request.form.get('notacao'),
                'localizacao_fisica': request.form.get('localizacao_fisica'),
                'data_registro': request.form.get('data_registro') or None,
                'data_localizacao': request.form.get('data_localizacao') or None,
                'observacoes': request.form.get('observacoes'),
                'divisao': request.form.get('divisao')
            }

            try:
                # ✅ Converte campos de data
                for campo in ['data_registro', 'data_localizacao']:
                    if dados[campo]:
                        dados[campo] = datetime.strptime(dados[campo], "%Y-%m-%d").date()
                    else:
                        if campo == 'data_registro':
                            dados[campo] = date.today()

                conn = config.get_pg_connection()
                cur = conn.cursor()
                cur.execute("""
                    INSERT INTO tabela_padrao (
                        fundo_colecao, titulo_conteudo, codigo_referencia, notacao,
                        localizacao_fisica, data_registro, data_localizacao, observacoes, divisao
                    ) VALUES (%(fundo_colecao)s, %(titulo_conteudo)s, %(codigo_referencia)s,
                              %(notacao)s, %(localizacao_fisica)s, %(data_registro)s,
                              %(data_localizacao)s, %(observacoes)s, %(divisao)s)
                """, dados)

                conn.commit()
                cur.close()
                conn.close()
                flash("✅ Registro inserido com sucesso!", "success")
                return redirect(url_for('inserir_dados'))

            except Exception as e:
                flash(f"❌ Erro ao inserir registro: {e}", "danger")
                return redirect(url_for('inserir_dados'))

    return render_template('inserir_dados.html')



# Rota para download da planilha modelo
@app.route('/download_modelo')
def download_modelo():
    df_modelo = pd.DataFrame(columns=[
        'Fundo/Coleção',
        'Título / Conteúdo',
        'Código de Referência',
        'Notação',
        'Localização física',
        'Data',
        'Data da localização',
        'Observações',
        'Divisão'
    ])
    output = BytesIO()
    df_modelo.to_excel(output, index=False)
    output.seek(0)

    return send_file(
        output,
        as_attachment=True,
        download_name="modelo_insercao_dados.xlsx",
        mimetype='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet'
    )


@app.route('/pesquisar_divisao', methods=['GET', 'POST'])
def pesquisar_divisao():
    resultados = []
    colunas = []
    total_nao_localizado = 0
    mensagem = None
    divisao_selecionada = None

    try:
        conn = config.get_pg_connection()
        cur = conn.cursor()

        # 🔹 Buscar todas as divisões disponíveis
        cur.execute("SELECT DISTINCT divisao FROM tabela_padrao ORDER BY divisao;")
        divisoes = [row[0] for row in cur.fetchall()]

        if request.method == 'POST':
            divisao = request.form.get('divisao')
            divisao_selecionada = divisao

            # 🔹 Buscar colunas pesquisáveis (exceto id e data_insercao)
            cur.execute("""
                SELECT column_name
                FROM information_schema.columns
                WHERE table_name = 'tabela_padrao'
                AND column_name NOT IN ('id', 'data_insercao');
            """)
            colunas = [row[0] for row in cur.fetchall()]

            coluna = request.form.get('coluna')
            termo = request.form.get('termo')

            print(f"\n🟦 [DEBUG] Divisão selecionada: {divisao}")
            print(f"🟦 [DEBUG] Coluna: {coluna}")
            print(f"🟦 [DEBUG] Termo: {termo}")

            # 🔹 Pesquisa por termo em coluna específica
            if coluna and termo:
                if coluna not in colunas:
                    mensagem = "Coluna inválida selecionada!"
                    print("❌ [DEBUG] Coluna inválida!")
                else:
                    query = f"""
                        SELECT * FROM tabela_padrao
                        WHERE divisao = %s AND CAST({coluna} AS TEXT) ILIKE %s
                        ORDER BY id DESC;
                    """
                    print(f"🟩 [DEBUG] Executando query:\n{query}")
                    cur.execute(query, (divisao, f"%{termo}%"))
                    resultados = cur.fetchall()
                    print(f"🟩 [DEBUG] Registros encontrados: {len(resultados)}")

                    if not resultados:
                        mensagem = f"Nenhum resultado encontrado para '{termo}'."

            # 🔹 Pesquisa apenas pela divisão (sem termo)
            elif divisao:
                print(f"🟨 [DEBUG] Pesquisa apenas pela divisão: {divisao}")
                cur.execute("""
                    SELECT * FROM tabela_padrao
                    WHERE divisao = %s
                    ORDER BY id DESC;
                """, (divisao,))
                resultados = cur.fetchall()
                print(f"🟨 [DEBUG] Total registros da divisão: {len(resultados)}")

            # 🔹 Contar total de “não localizado”
            cur.execute("""
                SELECT COUNT(*) FROM tabela_padrao
                WHERE divisao = %s AND observacoes ILIKE '%%não localizado%%';
            """, (divisao,))
            total_nao_localizado = cur.fetchone()[0]
            print(f"🟧 [DEBUG] Total 'não localizado': {total_nao_localizado}")

        cur.close()
        conn.close()

    except Exception as e:
        if conn:
            conn.rollback()
            conn.close()
        mensagem = f"❌ Erro ao pesquisar registros: {e}"
        divisoes = []
        print(f"❌ [ERRO DEBUG] {e}")

    return render_template(
        'pesquisar_divisao.html',
        divisoes=divisoes,
        divisao_selecionada=divisao_selecionada,
        resultados=resultados,
        total_nao_localizado=total_nao_localizado,
        mensagem=mensagem,
        colunas=colunas
    )



@app.route('/exportar_divisao')
def exportar_divisao():
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
            divisao = request.form.get('divisao')

            # Converte as datas corretamente
            if data_registro:
                data_registro = datetime.strptime(data_registro, "%Y-%m-%d").date()
            if data_localizacao:
                data_localizacao = datetime.strptime(data_localizacao, "%Y-%m-%d").date()

            # Atualiza o registro
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
                    divisao = %s
                WHERE id = %s
            """, (
                fundo_colecao, titulo_conteudo, codigo_referencia, notacao,
                localizacao_fisica, data_registro, data_localizacao,
                observacoes, divisao, id
            ))

            conn.commit()
            flash("✅ Registro atualizado com sucesso!", "success")

            # Após editar, volta para a página da divisão
            return redirect(url_for('pesquisar_divisao', divisao=divisao))

        except Exception as e:
            conn.rollback()
            flash(f"❌ Erro ao atualizar registro: {e}", "danger")
            return redirect(url_for('editar_registro', id=id))

    # 🔹 Método GET — busca os dados do registro
    cur.execute("""
        SELECT id, fundo_colecao, titulo_conteudo, codigo_referencia, notacao,
               localizacao_fisica, data_registro, data_localizacao, observacoes, divisao
        FROM tabela_padrao
        WHERE id = %s
    """, (id,))
    registro = cur.fetchone()

    cur.close()
    conn.close()

    if not registro:
        flash("❌ Registro não encontrado.", "danger")
        return redirect(url_for('pesquisar_divisao'))

    # Mapear os campos para o template
    colunas = [
        'id', 'fundo_colecao', 'titulo_conteudo', 'codigo_referencia', 'notacao',
        'localizacao_fisica', 'data_registro', 'data_localizacao', 'observacoes', 'divisao'
    ]
    registro_dict = dict(zip(colunas, registro))

    return render_template('editar_registro.html', registro=registro_dict)




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
        pages = ['home', 'search', 'inserir_dados', 'dashboard_divisao', 'editar_registro', 'permissions']
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
            #'upload': 'Upload',
            'search': 'Pesquisa',        
            #'insights': 'Dashboard',     
            'permissions': 'Permissões',
            'inserir_dados': 'Inserir Dados'
        }
        pages = ['home', 'search', 'permissions', 'inserir_dados']
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
