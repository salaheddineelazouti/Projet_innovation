import sqlite3

conn = sqlite3.connect('c:/Users/ELAZZOUTISalaheddine/Desktop/Projet_innovation/orders.db')
conn.row_factory = sqlite3.Row
cursor = conn.cursor()

# Update clients with WhatsApp phone numbers from their names
cursor.execute("""
    UPDATE clients 
    SET telephone = SUBSTR(nom, INSTR(nom, '+'))
    WHERE nom LIKE 'Client WhatsApp +%' AND (telephone IS NULL OR telephone = '')
""")
conn.commit()

# Show updated clients
cursor.execute('SELECT id, nom, telephone FROM clients WHERE nom LIKE "Client WhatsApp%"')
for row in cursor.fetchall():
    print(f'Client {row["id"]}: {row["nom"]} -> Tel: {row["telephone"]}')

conn.close()
print('Done!')
