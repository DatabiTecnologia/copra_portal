import bcrypt
import mysql.connector

# Gera hash para a nova senha
plain = "NovaSenhaSegura123!"
salt = bcrypt.gensalt(rounds=12)
hashed = bcrypt.hashpw(plain.encode('utf-8'), salt).decode('utf-8')

# Atualiza no banco (exemplo)
conn = mysql.connector.connect(host='192.168.0.233', user='seu_user', password='sua_senha', database='checkin')
cursor = conn.cursor()
cursor.execute("UPDATE `user` SET password = %s WHERE username = %s", (hashed, 'usuario_exemplo'))
conn.commit()
cursor.close()
conn.close()
print("Senha atualizada com hash:", hashed)