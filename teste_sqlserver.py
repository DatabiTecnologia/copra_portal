import pyodbc

try:
    conn = pyodbc.connect(
        'DRIVER={ODBC Driver 17 for SQL Server};'
        'SERVER=orion\\scriptcase;'
        'UID=pbi_docjud;'
        'PWD=pbiarquivo@1234;'
        'DATABASE=Docjud'
    )
    print("Conexão bem-sucedida!")
    cursor = conn.cursor()
    cursor.execute("SELECT TOP 1 * FROM tblFicha2")
    row = cursor.fetchone()
    print("Primeira linha da tabela tblFicha2:", row)
    conn.close()
except Exception as e:
    print("Erro na conexão:", e)