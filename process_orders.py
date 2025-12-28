"""
Purchase Order Processing Module
Main script to fetch emails, extract data, and process purchase orders.
"""

import os
import sys
import json
from datetime import datetime

# Fix Windows console encoding
sys.stdout.reconfigure(encoding='utf-8')

from gmail_receiver import GmailReceiver
from data_extractor import DataExtractor
from database import DatabaseManager


class OrderProcessor:
    def __init__(self):
        self.gmail = GmailReceiver()
        self.db = DatabaseManager()
        self.extractor = DataExtractor(db_manager=self.db)  # Pass DB for history lookups
        self.attachments_dir = "attachments"
        self.processed_orders = []
    
    def process_new_emails(self, max_emails=10, save_to_db=True):
        """Process new unread emails for purchase orders."""
        print("=" * 60)
        print("🚀 Traitement des bons de commande")
        print("=" * 60)
        
        # Connect to Gmail
        if not self.gmail.connect():
            print("❌ Impossible de se connecter à Gmail")
            return []
        
        # Connect to database FIRST (needed for history lookups)
        self.db.connect()
        self.db.init_database()
        
        # Update extractor with connected database
        self.extractor.set_database(self.db)
        
        try:
            # Get recent emails (using recent emails for testing)
            print("\n📥 Récupération des emails récents...")
            emails = self.gmail.get_recent_emails(count=max_emails)
            
            if not emails:
                print("📭 Aucun email à traiter")
                return []
            
            print(f"📬 {len(emails)} email(s) à analyser")
            
            orders = []
            for i, email_data in enumerate(emails, 1):
                print(f"\n{'─' * 60}")
                print(f"📧 Email {i}/{len(emails)}: {email_data['subject'][:50]}...")
                
                # Check if email already processed BEFORE calling GPT-4
                email_id = email_data.get('id')
                if email_id and self.db.is_email_processed(email_id):
                    print(f"   ⏭️ Email déjà traité, ignoré")
                    continue
                
                order = self.process_single_email(email_data)
                if order and order.get('est_bon_commande'):
                    orders.append(order)
                    print(f"   ✅ Bon de commande détecté: {order.get('numero_commande', 'N/A')}")
                    
                    # Save to database
                    if save_to_db:
                        self.db.create_order(order)
                else:
                    print("   ℹ️ Pas un bon de commande")
            
            self.processed_orders = orders
            return orders
            
        finally:
            self.gmail.disconnect()
            if save_to_db:
                self.db.disconnect()
    
    def process_single_email(self, email_data):
        """Process a single email and extract order data."""
        attachment_texts = {}
        
        # Download and process attachments if any
        if email_data.get('attachments'):
            print(f"   📎 {len(email_data['attachments'])} pièce(s) jointe(s)")
            
            downloaded = self.gmail.download_attachments(
                email_data['id'], 
                save_dir=self.attachments_dir
            )
            
            if downloaded:
                attachment_texts = self.extractor.process_attachments(downloaded)
        
        # Extract data using OpenAI
        print("   🔍 Analyse du contenu...")
        order_data = self.extractor.extract_from_email(email_data, attachment_texts)
        
        if order_data:
            # Add metadata
            order_data['email_id'] = email_data.get('id')
            order_data['email_subject'] = email_data.get('subject')
            order_data['email_from'] = email_data.get('from')
            order_data['processed_at'] = datetime.now().isoformat()
        
        return order_data
    
    def display_results(self):
        """Display processed orders summary."""
        if not self.processed_orders:
            print("\n📭 Aucun bon de commande détecté")
            return
        
        print("\n" + "=" * 60)
        print(f"📋 RÉSUMÉ: {len(self.processed_orders)} BON(S) DE COMMANDE")
        print("=" * 60)
        
        for i, order in enumerate(self.processed_orders, 1):
            print(f"\n🛒 Commande {i}:")
            print(f"   📌 N° Commande: {order.get('numero_commande', 'N/A')}")
            print(f"   🏢 Client: {order.get('entreprise_cliente', 'N/A')}")
            print(f"   📦 Produit: {order.get('type_produit', 'N/A')}")
            print(f"   📝 Nature: {order.get('nature_produit', 'N/A')}")
            print(f"   🔢 Quantité: {order.get('quantite', 'N/A')} {order.get('unite', '')}")
            if order.get('prix_total'):
                print(f"   💰 Total: {order.get('prix_total')} {order.get('devise', '')}")
            if order.get('date_livraison'):
                print(f"   📅 Livraison: {order.get('date_livraison')}")
            print(f"   🎯 Confiance: {order.get('confiance', 'N/A')}%")
    
    def save_results(self, filename="orders_output.json"):
        """Save processed orders to JSON file."""
        if not self.processed_orders:
            return
        
        with open(filename, "w", encoding="utf-8") as f:
            json.dump(self.processed_orders, f, indent=2, ensure_ascii=False)
        
        print(f"\n💾 Résultats sauvegardés: {filename}")


def main():
    processor = OrderProcessor()
    
    # Process emails
    processor.process_new_emails(max_emails=5)
    
    # Display results
    processor.display_results()
    
    # Save to file
    processor.save_results()
    
    print("\n" + "=" * 60)
    print("✅ Traitement terminé")
    print("=" * 60)


if __name__ == "__main__":
    main()
