"""
Script pour mettre à jour les noms des clients WhatsApp
"""
import sqlite3

conn = sqlite3.connect('orders.db')
cursor = conn.cursor()

# Mise à jour des noms - basé sur les transcriptions reçues
# +212701970502 = Restaurant Salah Eddine (vu dans la transcription audio)
updates = [
    ('+212701970502', 'Restaurant Salah Eddine'),
    # Ajoutez d'autres mappings si besoin
]

for phone, name in updates:
    cursor.execute("UPDATE clients SET nom = ? WHERE telephone = ?", (name, phone))
    print(f"✅ {phone} -> {name}")

conn.commit()

# Vérification
print("\n📋 Liste des clients mise à jour:")
cursor.execute("SELECT id, nom, telephone FROM clients ORDER BY id")
for row in cursor.fetchall():
    print(f"  ID {row[0]}: {row[1]} (Tel: {row[2]})")

conn.close()
