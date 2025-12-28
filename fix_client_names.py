"""
Script pour corriger les noms de clients génériques "Client WhatsApp +xxx"
"""
import sqlite3

def fix_client_names():
    conn = sqlite3.connect('orders.db')
    conn.row_factory = sqlite3.Row
    cursor = conn.cursor()
    
    # Find clients with generic names
    cursor.execute("""
        SELECT id, nom, telephone 
        FROM clients 
        WHERE nom LIKE 'Client WhatsApp%' OR nom LIKE 'Client Inconnu%'
    """)
    generic_clients = cursor.fetchall()
    
    print(f"\n📋 {len(generic_clients)} client(s) avec noms génériques trouvé(s):\n")
    
    for client in generic_clients:
        print(f"  ID {client['id']}: {client['nom']} (Tél: {client['telephone']})")
    
    if not generic_clients:
        print("✅ Aucun client avec nom générique trouvé!")
        conn.close()
        return
    
    print("\n" + "=" * 50)
    print("Voulez-vous renommer ces clients?")
    print("=" * 50)
    
    for client in generic_clients:
        print(f"\n📱 Client ID {client['id']}: {client['nom']}")
        print(f"   Téléphone: {client['telephone']}")
        
        new_name = input("   Nouveau nom (ou Entrée pour garder): ").strip()
        
        if new_name:
            cursor.execute("UPDATE clients SET nom = ? WHERE id = ?", (new_name, client['id']))
            conn.commit()
            print(f"   ✅ Renommé en: {new_name}")
        else:
            print("   ⏭️ Nom conservé")
    
    conn.close()
    print("\n✅ Terminé!")

if __name__ == "__main__":
    fix_client_names()
