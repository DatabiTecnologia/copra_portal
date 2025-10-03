# 📂 Sistema Flask - Gestão de Dados e Permissões

![Python](https://img.shields.io/badge/Python-3.9%2B-blue?logo=python&logoColor=white)
![Flask](https://img.shields.io/badge/Flask-Framework-black?logo=flask)
![MySQL](https://img.shields.io/badge/MySQL-Database-4479A1?logo=mysql&logoColor=white)
![PostgreSQL](https://img.shields.io/badge/PostgreSQL-Database-336791?logo=postgresql&logoColor=white)
![SQL Server](https://img.shields.io/badge/SQL%20Server-Database-CC2927?logo=microsoftsqlserver&logoColor=white)
![Pandas](https://img.shields.io/badge/Pandas-Data%20Analysis-150458?logo=pandas)
![OpenPyXL](https://img.shields.io/badge/OpenPyXL-Excel%20IO-00C300)

---

## 🔹 Visão Geral
Este sistema é uma aplicação **Flask** para gerenciamento de autenticação, permissões de acesso, upload de dados via Excel para PostgreSQL, pesquisa em SQL Server, edição de registros e exportação de relatórios.  

Ele integra **3 bancos de dados** distintos:
- **MySQL** → Usuários e permissões.  
- **PostgreSQL** → Tabelas carregadas por upload.  
- **SQL Server** → Consultas avançadas (documentos / fichas).  

---

## 🔹 Estrutura do Projeto
```txt
project/
│── app.py                # Código principal Flask
│── requirements.txt      # Todos pip install
│── config.py            # Configurações (chaves e conexões DB)
│── templates/            # Páginas HTML (Jinja2)
│   ├── login.html
|   ├── base.html         # Base estrutural do leyate
│   ├── home.html
│   ├── upload.html
│   ├── editar.html
│   ├── search.html
│   ├── insights.html
│   └── permissions.html
│── static/               # CSS, JS, imagens
    ├── style.css
    ├── logoan-circulo_small.png

🔹 Funcionalidades
🔑 Autenticação

GET / → Tela de login.

POST / → Login com validação de senha e permissões.
( tela irá validar se o usuario é administrador, se for ele tem acesso direto, se não for, ele vai validar se ele faz parte do grupo de copra que vem do banco dados do checkin, se tiver lá ele vai ver qual é grupo de atividade ele pertence (arquivista, administativivo e etc.) e vai ser liberado a pagina conforme o administrado deu a permissão na pagina permissão ou permissions.html)

GET /logout → Finaliza sessão.

GET /home → Tela inicial.

📤 Upload de Arquivos

GET /upload → Formulário de upload.

POST /upload → Lê planilhas Excel e insere dados em tabelas PostgreSQL.

Suporta tabelas: codes_dijud, codes_didop, codes_dipex, codac_didas, codac_didoc.

✏️ Edição de Registros

GET /editar/<tabela> → Lista registros de uma tabela.

POST /editar/<tabela> → Atualiza valores de registros.

📊 Insights

GET /insights → Dashboard inicial (exemplo para BI).

🔐 Permissões

GET /permissions → Tela de administração de permissões.

POST /permissions → Salva permissões no MySQL.

🔍 Pesquisa (SQL Server)

GET /search → Pesquisa registros em tblFicha2.

GET /search/export → Exporta resultados para Excel.

GET /nao_localizado → Lista registros com status “não localizado”.

🔹 Controle de Acesso

Acesso controlado por check_access(page).

Admins têm acesso total.

Permissões específicas são gerenciadas via tabela page_permissions (MySQL).

O menu é dinâmico, exibindo apenas páginas permitidas para o usuário.

🔹 Como Rodar
instalar as dependencias
requirements.txt
depois 
python app.py    


![Pagina de entrada](prints_imagens/login.png)
![Pagina home](prints_imagens/home.png)
![Pagina de upload e edição de dados](prints_imagens/upload.png)
![Pagina de pesquisa e extração de dados para excel](prints_imagens/pesquisa.png)