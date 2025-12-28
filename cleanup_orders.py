"""Script to cleanup duplicate orders and Opoint client."""
import sqlite3

conn = sqlite3.connect('orders.db')
cursor = conn.cursor()

# Delete duplicate orders 17 and 18
cursor.execute('DELETE FROM commandes WHERE id IN (17, 18)')
print(f"Deleted {cursor.rowcount} orders")

# Delete Opoint client if exists
cursor.execute('DELETE FROM clients WHERE nom LIKE ?', ('%Opoint%',))
print(f"Deleted {cursor.rowcount} clients named Opoint")

conn.commit()
conn.close()
print("Cleanup done!")
