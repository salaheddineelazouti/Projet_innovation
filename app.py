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

app = Flask(__name__)
whatsapp = WhatsAppReceiver()
app.secret_key = os.urandom(24)

db = DatabaseManager()


@app.before_request
def before_request():
    """Connect to database before each request."""
    db.connect()
    db.init_database()


@app.teardown_request
def teardown_request(exception):
    """Disconnect from database after each request."""
    db.disconnect()


# ============== PAGES ==============

@app.route('/')
def index():
    """Dashboard page."""
    stats = db.get_stats()
    recent_orders = db.get_all_orders()[:5]
    return render_template('index.html', stats=stats, recent_orders=recent_orders)


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
    """Validate an order and send WhatsApp confirmation if applicable."""
    validated_by = request.json.get('validated_by', 'Commercial')
    db.update_order_status(order_id, 'validee', validated_by)
    
    # Send WhatsApp confirmation if order came from WhatsApp
    order = db.get_order(order_id)
    if order and order.get('email_subject', '').startswith('WhatsApp'):
        phone = order.get('email_from', '')
        if phone:
            try:
                message = f"""✅ *Commande Validée!*

📋 *Détails:*
• Client: {order.get('client_nom', 'N/A')}
• Produit: {order.get('produit_type', order.get('nature_produit', 'N/A'))}
• Quantité: {order.get('quantite', 'N/A')} {order.get('unite', '')}
• N° Commande: {order.get('numero_commande') or f"CMD-{order_id}"}

✨ Merci pour votre confiance!
📞 Pour toute question, contactez-nous."""
                
                whatsapp.send_reply(phone, message)
                print(f"   📱 Confirmation WhatsApp envoyée à {phone}")
            except Exception as e:
                print(f"   ⚠️ Erreur envoi WhatsApp: {e}")
    
    return jsonify({'success': True, 'message': 'Commande validée'})


@app.route('/api/orders/<int:order_id>/reject', methods=['POST'])
def api_reject_order(order_id):
    """Reject an order and send WhatsApp notification if applicable."""
    reason = request.json.get('reason', '') if request.json else ''
    
    # Get order before updating status
    order = db.get_order(order_id)
    
    db.update_order_status(order_id, 'rejetee')
    
    # Send WhatsApp notification if order came from WhatsApp
    if order and order.get('email_subject', '').startswith('WhatsApp'):
        phone = order.get('email_from', '')
        if phone:
            try:
                message = f"""❌ *Commande Non Validée*

📋 *Détails:*
• Client: {order.get('client_nom', 'N/A')}
• Produit: {order.get('produit_type', order.get('nature_produit', 'N/A'))}
{f'• Raison: {reason}' if reason else ''}

📞 Veuillez nous contacter pour plus d'informations."""
                
                whatsapp.send_reply(phone, message)
                print(f"   📱 Notification rejet WhatsApp envoyée à {phone}")
            except Exception as e:
                print(f"   ⚠️ Erreur envoi WhatsApp: {e}")
    
    return jsonify({'success': True, 'message': 'Commande rejetée'})


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


@app.route('/whatsapp')
def whatsapp_page():
    """WhatsApp integration page."""
    cursor = db.connection.cursor()
    cursor.execute("""
        SELECT c.*, cl.nom as client_nom, p.type as produit_type
        FROM commandes c
        LEFT JOIN clients cl ON c.client_id = cl.id
        LEFT JOIN produits p ON c.produit_id = p.id
        WHERE c.email_subject LIKE 'WhatsApp%'
        ORDER BY c.created_at DESC
        LIMIT 20
    """)
    whatsapp_orders = [dict(row) for row in cursor.fetchall()]
    
    # Get ngrok URL from environment
    ngrok_url = os.getenv('NGROK_URL', 'https://rocio-unfoxy-liltingly.ngrok-free.dev')
    
    return render_template('whatsapp.html', 
                         whatsapp_orders=whatsapp_orders,
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
        
        print(f"   De: {message_data.get('From', 'N/A')}")
        print(f"   Body: {message_data.get('Body', '')[:50]}...")
        print(f"   Media: {message_data.get('NumMedia', 0)} fichier(s)")
        
        # Process the message
        result = whatsapp.process_incoming_message(message_data)
        
        # Format for extraction
        formatted = whatsapp.format_for_extraction(result)
        
        # Extract order data using existing extractor
        extractor = DataExtractor(db_manager=db)
        order_data = extractor.extract_from_email(formatted)
        
        response_message = ""
        
        if order_data and order_data.get('est_bon_commande'):
            # Save to database
            order_data['email_id'] = f"whatsapp_{result['timestamp']}"
            order_data['email_subject'] = f"WhatsApp - {result['from']}"
            order_data['email_from'] = result['from']
            
            order_id = db.create_order(order_data)
            
            print(f"   ✅ Commande détectée et enregistrée (ID: {order_id})")
            
            # Prepare confirmation message
            response_message = f"""✅ Commande reçue !

📦 Client: {order_data.get('entreprise_cliente', 'N/A')}
📋 Produit: {order_data.get('type_produit', 'N/A')}
🔢 Quantité: {order_data.get('quantite', 'N/A')} {order_data.get('unite', '')}
🎯 Confiance: {order_data.get('confiance', 0)}%

Votre commande est en attente de validation."""
            
        else:
            print("   ℹ️ Pas un bon de commande")
            response_message = "Message reçu. Si vous souhaitez passer une commande, veuillez préciser les détails (client, produit, quantité)."
        
        # Send reply (optional)
        if result.get('from'):
            whatsapp.send_reply(result['from'], response_message)
        
        # Return TwiML response
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
