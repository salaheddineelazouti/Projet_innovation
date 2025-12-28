"""
Flask Web Application for Purchase Order Validation
Interface for commercial team to view, edit, and validate orders.
"""

import os
import sys
from flask import Flask, render_template, request, jsonify, redirect, url_for, send_file
from datetime import datetime

# Fix Windows console encoding
sys.stdout.reconfigure(encoding='utf-8')

from database import DatabaseManager
from process_orders import OrderProcessor
from analytics import Analytics, AlertSystem, ReportGenerator, ClientHistory, AIPredictor
from whatsapp_receiver import WhatsAppReceiver
from data_extractor import DataExtractor
from email_sender import email_sender

app = Flask(__name__)
whatsapp = WhatsAppReceiver()
app.secret_key = os.urandom(24)

db = DatabaseManager()

# Initialize database once at startup
db.connect()
db.init_database()


@app.before_request
def before_request():
    """Ensure database connection before each request."""
    if not db.connection:
        db.connect()


# Correction automatique des sources WhatsApp
def fix_whatsapp_sources():
    """Fix orders that came from WhatsApp but have wrong source."""
    try:
        cursor = db.connection.cursor()
        # Corriger les commandes avec email_subject contenant "WhatsApp" mais source incorrecte
        cursor.execute("""
            UPDATE commandes 
            SET source = 'whatsapp' 
            WHERE (email_subject LIKE '%WhatsApp%' OR email_subject LIKE '%whatsapp%')
            AND (source IS NULL OR source != 'whatsapp')
        """)
        # Spécifiquement corriger la commande #2
        cursor.execute("UPDATE commandes SET source = 'whatsapp' WHERE id = 2")
        db.connection.commit()
        print("✅ Sources WhatsApp corrigées")
    except Exception as e:
        print(f"Erreur correction sources: {e}")

# Exécuter la correction au démarrage
fix_whatsapp_sources()


# Don't disconnect after each request - keep connection alive
# @app.teardown_request
# def teardown_request(exception):
#     """Disconnect from database after each request."""
#     db.disconnect()


# ============== PAGES ==============

@app.route('/')
def index():
    """Dashboard page."""
    from datetime import datetime, timedelta
    import json
    
    stats = db.get_stats()
    recent_orders = db.get_all_orders()[:5]
    top_clients = db.get_top_clients(5)
    top_products = db.get_top_products(5)
    
    # Get trend data for the last 7 days
    trend_data = db.get_orders_trend(7)
    
    # Build labels and data for chart
    today = datetime.now().date()
    labels = []
    data = []
    trend_dict = {item['date']: item['count'] for item in trend_data}
    
    for i in range(6, -1, -1):
        date = today - timedelta(days=i)
        date_str = date.strftime('%Y-%m-%d')
        labels.append(date.strftime('%d/%m'))
        data.append(trend_dict.get(date_str, 0))
    
    return render_template('index.html', 
                         stats=stats, 
                         recent_orders=recent_orders,
                         top_clients=top_clients,
                         top_products=top_products,
                         trend_labels=json.dumps(labels),
                         trend_data=json.dumps(data))


@app.route('/orders')
def orders_list():
    """List all orders."""
    status_filter = request.args.get('status', None)
    if status_filter:
        orders = db.get_all_orders(status=status_filter)
    else:
        orders = db.get_all_orders()
    return render_template('orders.html', orders=orders, current_filter=status_filter)


@app.route('/orders/<int:order_id>')
def order_detail(order_id):
    """Order detail page for validation."""
    order = db.get_order(order_id)
    products = db.get_all_products()
    if not order:
        return redirect(url_for('orders_list'))
    return render_template('order_detail.html', order=order, products=products)


@app.route('/process')
def process_page():
    """Page to trigger email processing."""
    return render_template('process.html')


# ============== API ENDPOINTS ==============

@app.route('/api/process-emails', methods=['POST'])
def api_process_emails():
    """Process new emails for purchase orders."""
    try:
        processor = OrderProcessor()
        orders = processor.process_new_emails(max_emails=10, save_to_db=True)
        return jsonify({
            'success': True,
            'message': f'{len(orders)} bon(s) de commande détecté(s)',
            'orders_count': len(orders)
        })
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500


@app.route('/api/orders/<int:order_id>/validate', methods=['POST'])
def api_validate_order(order_id):
    """Validate an order and send WhatsApp/Email confirmation if applicable."""
    validated_by = request.json.get('validated_by', 'Commercial') if request.json else 'Commercial'
    db.update_order_status(order_id, 'validee', validated_by)
    
    # Send WhatsApp confirmation if order came from WhatsApp
    order = db.get_order(order_id)
    whatsapp_sent = False
    email_sent = False
    
    if order:
        # Send Email confirmation
        try:
            email_sent = email_sender.send_validation_email(order)
        except Exception as e:
            print(f"   ⚠️ Erreur envoi email: {e}")
        
        # Send WhatsApp confirmation if order came from WhatsApp
        if order.get('source') == 'whatsapp':
            # Try to get phone from client_telephone or email_from
            phone = order.get('client_telephone') or order.get('email_from', '')
            
            # Extract phone number if it's in the client name (Client WhatsApp +212...)
            if not phone and order.get('client_nom', '').startswith('Client WhatsApp'):
                import re
                match = re.search(r'\+\d+', order.get('client_nom', ''))
                if match:
                    phone = match.group()
            
            if phone:
                try:
                    # Format phone for WhatsApp (remove + and add whatsapp: prefix)
                    phone_clean = phone.replace('+', '').replace(' ', '').replace('whatsapp:', '')
                    whatsapp_number = f"+{phone_clean}"
                    
                    message = f"""✅ *Commande Validée!*

📋 *Détails:*
• Client: {order.get('client_nom', 'N/A')}
• Produit: {order.get('produit_type', order.get('nature_produit', 'N/A'))}
• Quantité: {order.get('quantite', 'N/A')} {order.get('unite', '')}
• N° Commande: {order.get('numero_commande') or f"CMD-{order_id}"}

✨ Merci pour votre confiance!
📞 Pour toute question, contactez-nous."""
                    
                    result = whatsapp.send_reply(whatsapp_number, message)
                    if result:
                        whatsapp_sent = True
                        print(f"   📱 Confirmation WhatsApp envoyée à {whatsapp_number}")
                except Exception as e:
                    print(f"   ⚠️ Erreur envoi WhatsApp: {e}")
    
    return jsonify({
        'success': True, 
        'message': 'Commande validée',
        'whatsapp_sent': whatsapp_sent,
        'email_sent': email_sent
    })


@app.route('/api/orders/<int:order_id>/reject', methods=['POST'])
def api_reject_order(order_id):
    """Reject an order and send WhatsApp/Email notification if applicable."""
    reason = request.json.get('reason', '') if request.json else ''
    
    # Get order before updating status
    order = db.get_order(order_id)
    
    db.update_order_status(order_id, 'rejetee')
    
    whatsapp_sent = False
    email_sent = False
    
    if order:
        # Send Email notification
        try:
            email_sent = email_sender.send_rejection_email(order, reason)
        except Exception as e:
            print(f"   ⚠️ Erreur envoi email: {e}")
        
        # Send WhatsApp notification if order came from WhatsApp
        if order.get('source') == 'whatsapp':
            # Try to get phone from client_telephone or email_from
            phone = order.get('client_telephone') or order.get('email_from', '')
            
            # Extract phone number if it's in the client name (Client WhatsApp +212...)
            if not phone and order.get('client_nom', '').startswith('Client WhatsApp'):
                import re
                match = re.search(r'\+\d+', order.get('client_nom', ''))
                if match:
                    phone = match.group()
            
            if phone:
                try:
                    # Format phone for WhatsApp
                    phone_clean = phone.replace('+', '').replace(' ', '').replace('whatsapp:', '')
                    whatsapp_number = f"+{phone_clean}"
                    
                    message = f"""❌ *Commande Non Validée*

📋 *Détails:*
• Client: {order.get('client_nom', 'N/A')}
• Produit: {order.get('produit_type', order.get('nature_produit', 'N/A'))}
{f'• Raison: {reason}' if reason else ''}

📞 Veuillez nous contacter pour plus d'informations."""
                    
                    result = whatsapp.send_reply(whatsapp_number, message)
                    if result:
                        whatsapp_sent = True
                        print(f"   📱 Notification rejet WhatsApp envoyée à {whatsapp_number}")
                except Exception as e:
                    print(f"   ⚠️ Erreur envoi WhatsApp: {e}")
    
    return jsonify({
        'success': True, 
        'message': 'Commande rejetée',
        'whatsapp_sent': whatsapp_sent,
        'email_sent': email_sent
    })


@app.route('/api/orders/<int:order_id>/update', methods=['POST'])
def api_update_order(order_id):
    """Update order fields."""
    updates = request.json
    # Remove fields that shouldn't be updated directly
    updates.pop('id', None)
    updates.pop('created_at', None)
    db.update_order(order_id, updates)
    return jsonify({'success': True, 'message': 'Commande mise à jour'})


@app.route('/api/stats')
def api_stats():
    """Get statistics."""
    stats = db.get_stats()
    return jsonify(stats)


@app.route('/api/orders')
def api_orders():
    """Get all orders as JSON."""
    orders = db.get_all_orders()
    return jsonify(orders)


# ============== ANALYTICS PAGES ==============

@app.route('/analytics')
def analytics_page():
    """Advanced analytics dashboard."""
    analytics = Analytics(db)
    stats = analytics.get_dashboard_stats()
    
    alert_system = AlertSystem(db)
    alerts = alert_system.check_alerts()
    
    return render_template('analytics.html', stats=stats, alerts=alerts)


@app.route('/clients')
def clients_page():
    """Client management and history."""
    clients = db.get_all_clients()
    history = ClientHistory(db)
    
    # Add preferences for each client
    for client in clients:
        prefs = history.get_client_preferences(client['nom'])
        client['preferences'] = prefs
    
    return render_template('clients.html', clients=clients)


@app.route('/clients/<int:client_id>')
def client_detail(client_id):
    """Client detail with order history."""
    cursor = db.connection.cursor()
    cursor.execute("SELECT * FROM clients WHERE id = ?", (client_id,))
    client = cursor.fetchone()
    
    if not client:
        return redirect(url_for('clients_page'))
    
    client = dict(client)
    
    history = ClientHistory(db)
    client['preferences'] = history.get_client_preferences(client['nom'])
    
    # Get client orders
    cursor.execute("""
        SELECT c.*, p.type as produit_type
        FROM commandes c
        LEFT JOIN produits p ON c.produit_id = p.id
        WHERE c.client_id = ?
        ORDER BY c.created_at DESC
    """, (client_id,))
    orders = [dict(row) for row in cursor.fetchall()]
    
    # Get predictions
    predictor = AIPredictor(db)
    prediction = predictor.predict_client_behavior(client['nom'])
    
    return render_template('client_detail.html', client=client, orders=orders, prediction=prediction)


@app.route('/alerts')
def alerts_page():
    """Alerts dashboard."""
    alert_system = AlertSystem(db)
    alerts = alert_system.check_alerts()
    return render_template('alerts.html', alerts=alerts)


# ============== NOTIFICATIONS API ==============

@app.route('/api/notifications/check')
def check_notifications():
    """Check for new orders since last check."""
    last_id = request.args.get('last_id', '0')
    
    try:
        cursor = db.connection.cursor()
        
        try:
            last_id_int = int(last_id)
        except:
            last_id_int = 0
        
        if last_id_int > 0:
            # Get orders with ID greater than last seen
            cursor.execute("""
                SELECT c.id, c.created_at, c.source, c.email_subject,
                       cl.nom as client, p.type as produit, c.nature_produit
                FROM commandes c
                LEFT JOIN clients cl ON c.client_id = cl.id
                LEFT JOIN produits p ON c.produit_id = p.id
                WHERE c.id > ?
                ORDER BY c.id DESC
                LIMIT 10
            """, (last_id_int,))
        else:
            # Get latest order ID only (for initialization)
            cursor.execute("SELECT MAX(id) as max_id FROM commandes")
            row = cursor.fetchone()
            max_id = row['max_id'] if row and row['max_id'] else 0
            return jsonify({'new_orders': [], 'last_id': max_id})
        
        new_orders = []
        max_seen_id = last_id_int
        
        for row in cursor.fetchall():
            order = dict(row)
            # Track highest ID
            if order['id'] > max_seen_id:
                max_seen_id = order['id']
            # Determine source
            source = order.get('source', 'email')
            if not source and order.get('email_subject'):
                source = 'whatsapp' if 'whatsapp' in order['email_subject'].lower() else 'email'
            order['source'] = source
            # Use nature_produit as fallback for produit
            if not order.get('produit'):
                order['produit'] = order.get('nature_produit', 'Produit')
            # Convert created_at to ISO format for JavaScript
            if order.get('created_at'):
                order['timestamp'] = order['created_at']
            else:
                from datetime import datetime
                order['timestamp'] = datetime.now().isoformat()
            new_orders.append(order)
        
        return jsonify({'new_orders': new_orders, 'last_id': max_seen_id})
    except Exception as e:
        return jsonify({'new_orders': [], 'error': str(e), 'last_id': 0})


@app.route('/whatsapp')
def whatsapp_page():
    """WhatsApp integration page."""
    cursor = db.connection.cursor()
    
    # Get WhatsApp orders
    cursor.execute("""
        SELECT c.*, cl.nom as client_nom, cl.telephone, p.type as produit_type
        FROM commandes c
        LEFT JOIN clients cl ON c.client_id = cl.id
        LEFT JOIN produits p ON c.produit_id = p.id
        WHERE c.source = 'whatsapp' OR c.email_subject LIKE 'WhatsApp%'
        ORDER BY c.created_at DESC
        LIMIT 20
    """)
    whatsapp_orders = [dict(row) for row in cursor.fetchall()]
    
    # WhatsApp stats
    cursor.execute("SELECT COUNT(*) FROM commandes WHERE source = 'whatsapp'")
    total = cursor.fetchone()[0]
    
    cursor.execute("SELECT COUNT(*) FROM commandes WHERE source = 'whatsapp' AND statut = 'en_attente'")
    pending = cursor.fetchone()[0]
    
    cursor.execute("SELECT COUNT(*) FROM commandes WHERE source = 'whatsapp' AND statut = 'validee'")
    validated = cursor.fetchone()[0]
    
    cursor.execute("SELECT COUNT(*) FROM commandes WHERE source = 'whatsapp' AND DATE(created_at) = DATE('now')")
    today = cursor.fetchone()[0]
    
    cursor.execute("SELECT COUNT(*) FROM commandes WHERE source = 'whatsapp' AND created_at >= DATE('now', '-7 days')")
    this_week = cursor.fetchone()[0]
    
    cursor.execute("SELECT COUNT(*) FROM commandes WHERE source = 'whatsapp' AND created_at >= DATE('now', 'start of month')")
    this_month = cursor.fetchone()[0]
    
    validation_rate = round((validated / total * 100) if total > 0 else 0)
    
    whatsapp_stats = {
        'total': total,
        'pending': pending,
        'validated': validated,
        'today': today,
        'this_week': this_week,
        'this_month': this_month,
        'validation_rate': validation_rate
    }
    
    # Top WhatsApp clients
    cursor.execute("""
        SELECT cl.id, cl.nom, COUNT(c.id) as total_orders
        FROM clients cl
        JOIN commandes c ON cl.id = c.client_id
        WHERE c.source = 'whatsapp'
        GROUP BY cl.id
        ORDER BY total_orders DESC
        LIMIT 5
    """)
    whatsapp_clients = [dict(row) for row in cursor.fetchall()]
    
    # Get ngrok URL from environment
    ngrok_url = os.getenv('NGROK_URL', 'https://rocio-unfoxy-liltingly.ngrok-free.dev')
    
    return render_template('whatsapp.html', 
                         whatsapp_orders=whatsapp_orders,
                         whatsapp_stats=whatsapp_stats,
                         whatsapp_clients=whatsapp_clients,
                         ngrok_url=ngrok_url)


# ============== EXPORT ENDPOINTS ==============

@app.route('/export/excel')
def export_excel():
    """Export orders to Excel."""
    reporter = ReportGenerator(db)
    filters = {}
    if request.args.get('status'):
        filters['status'] = request.args.get('status')
    if request.args.get('date_from'):
        filters['date_from'] = request.args.get('date_from')
    if request.args.get('date_to'):
        filters['date_to'] = request.args.get('date_to')
    
    filepath = reporter.export_to_excel(filters=filters if filters else None)
    return send_file(filepath, as_attachment=True, download_name='commandes.xlsx')


@app.route('/export/csv')
def export_csv():
    """Export orders to CSV."""
    reporter = ReportGenerator(db)
    filters = {}
    if request.args.get('status'):
        filters['status'] = request.args.get('status')
    
    filepath = reporter.export_to_csv(filters=filters if filters else None)
    return send_file(filepath, as_attachment=True, download_name='commandes.csv')


@app.route('/export/pdf')
def export_pdf():
    """Generate PDF report."""
    reporter = ReportGenerator(db)
    filepath = reporter.generate_pdf_report()
    return send_file(filepath, as_attachment=True, download_name='rapport_commandes.pdf')


# ============== API ANALYTICS ==============

@app.route('/api/analytics')
def api_analytics():
    """Get advanced analytics data."""
    analytics = Analytics(db)
    stats = analytics.get_dashboard_stats()
    return jsonify(stats)


@app.route('/api/alerts')
def api_alerts():
    """Get current alerts."""
    alert_system = AlertSystem(db)
    alerts = alert_system.check_alerts()
    return jsonify(alerts)


@app.route('/api/client/<int:client_id>/history')
def api_client_history(client_id):
    """Get client order history and preferences."""
    cursor = db.connection.cursor()
    cursor.execute("SELECT nom FROM clients WHERE id = ?", (client_id,))
    client = cursor.fetchone()
    
    if not client:
        return jsonify({'error': 'Client not found'}), 404
    
    history = ClientHistory(db)
    preferences = history.get_client_preferences(client['nom'])
    
    predictor = AIPredictor(db)
    prediction = predictor.predict_client_behavior(client['nom'])
    
    return jsonify({
        'preferences': preferences,
        'prediction': prediction
    })


# ============== WHATSAPP WEBHOOK ==============

@app.route('/webhook/whatsapp', methods=['POST'])
def whatsapp_webhook():
    """Receive incoming WhatsApp messages from Twilio."""
    try:
        print("\n" + "=" * 50)
        print("📱 MESSAGE WHATSAPP REÇU")
        print("=" * 50)
        
        # Get message data from Twilio
        message_data = request.form.to_dict()
        
        from_number = message_data.get('From', 'N/A')
        body = message_data.get('Body', '')
        
        print(f"   De: {from_number}")
        print(f"   Body: {body[:50]}...")
        print(f"   Media: {message_data.get('NumMedia', 0)} fichier(s)")
        
        # Process the message
        result = whatsapp.process_incoming_message(message_data)
        
        # Format for extraction
        formatted = whatsapp.format_for_extraction(result)
        
        # Extract order data using existing extractor with main db connection
        extractor = DataExtractor(db_manager=db)
        order_data = extractor.extract_from_email(formatted)
        
        response_message = ""
        
        if order_data and order_data.get('est_bon_commande'):
            # Save to database with source = whatsapp
            order_data['email_id'] = f"whatsapp_{result['timestamp']}"
            order_data['email_subject'] = f"WhatsApp - {from_number}"
            order_data['email_from'] = from_number
            order_data['source'] = 'whatsapp'
            order_data['whatsapp_from'] = from_number
            
            # Ensure client name defaults to phone number if not extracted
            if not order_data.get('entreprise_cliente'):
                phone = from_number.replace('whatsapp:', '')
                order_data['entreprise_cliente'] = f'Client WhatsApp {phone}'
            
            order_id = db.create_order(order_data)
            
            print(f"   ✅ Commande détectée et enregistrée (ID: {order_id})")
            
            # Prepare confirmation message
            client_name = order_data.get('entreprise_cliente', from_number)
            response_message = f"""✅ Commande reçue !

📦 Client: {client_name}
📋 Produit: {order_data.get('type_produit', 'N/A')}
🔢 Quantité: {order_data.get('quantite', 'N/A')} {order_data.get('unite', '')}
🎯 Confiance: {order_data.get('confiance', 0)}%

Votre commande est en attente de validation."""
            
        else:
            print("   ℹ️ Pas un bon de commande")
            response_message = "Message reçu. Si vous souhaitez passer une commande, veuillez préciser les détails (client, produit, quantité)."
        
        # Return TwiML response (Twilio will automatically send this as reply)
        from twilio.twiml.messaging_response import MessagingResponse
        resp = MessagingResponse()
        resp.message(response_message)
        
        return str(resp), 200, {'Content-Type': 'application/xml'}
        
    except Exception as e:
        print(f"   ❌ Erreur webhook WhatsApp: {e}")
        import traceback
        traceback.print_exc()
        return jsonify({'error': str(e)}), 500


@app.route('/api/whatsapp/status')
def whatsapp_status():
    """Check WhatsApp connection status."""
    try:
        connected = whatsapp.connect()
        return jsonify({
            'connected': connected,
            'account_sid': whatsapp.account_sid[:10] + "..." if whatsapp.account_sid else None
        })
    except Exception as e:
        return jsonify({'connected': False, 'error': str(e)})


if __name__ == '__main__':
    print("=" * 50)
    print("🚀 Démarrage de l'interface de validation")
    print("=" * 50)
    print("📍 URL: http://localhost:5000")
    print("=" * 50)
    app.run(debug=True, host='0.0.0.0', port=5000)
