import sqlite3

conn = sqlite3.connect('orders.db')
cursor = conn.cursor()

# Update commande #2 source to whatsapp
cursor.execute("UPDATE commandes SET source = 'whatsapp' WHERE id = 2")
conn.commit()

# Verify
cursor.execute("SELECT id, source, email_subject FROM commandes WHERE id = 2")
row = cursor.fetchone()
print(f"Commande #{row[0]}: source={row[1]}, subject={row[2]}")

conn.close()
print("Done!")
