"""
Email Sender Module for TECPAP
Sends professional HTML emails for order validation/rejection notifications.
"""
import smtplib
import os
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from datetime import datetime
from dotenv import load_dotenv

load_dotenv()


class EmailSender:
    def __init__(self):
        self.smtp_server = "smtp.gmail.com"
        self.smtp_port = 587
        self.email = os.getenv("GMAIL_EMAIL")
        self.password = os.getenv("GMAIL_APP_PASSWORD")
        self.company_name = "TECPAP"
        self.company_email = self.email
        
    def send_email(self, to_email, subject, html_content, text_content=None):
        """Send an email with HTML content."""
        if not self.email or not self.password:
            print("   ⚠️ Configuration email manquante (GMAIL_EMAIL ou GMAIL_APP_PASSWORD)")
            return False
            
        if not to_email or '@' not in to_email:
            print(f"   ⚠️ Email invalide: {to_email}")
            return False
        
        try:
            msg = MIMEMultipart('alternative')
            msg['Subject'] = subject
            msg['From'] = f"{self.company_name} <{self.email}>"
            msg['To'] = to_email
            
            # Plain text fallback
            if text_content:
                part1 = MIMEText(text_content, 'plain', 'utf-8')
                msg.attach(part1)
            
            # HTML content
            part2 = MIMEText(html_content, 'html', 'utf-8')
            msg.attach(part2)
            
            # Connect and send
            with smtplib.SMTP(self.smtp_server, self.smtp_port) as server:
                server.starttls()
                server.login(self.email, self.password.replace(' ', ''))
                server.send_message(msg)
            
            print(f"   📧 Email envoyé à {to_email}")
            return True
            
        except Exception as e:
            print(f"   ❌ Erreur envoi email: {e}")
            return False
    
    def send_validation_email(self, order):
        """Send order validation confirmation email."""
        to_email = order.get('email_from', '')
        
        # Skip if no valid email
        if not to_email or '@' not in to_email:
            return False
        
        client_name = order.get('client_nom', 'Cher client')
        product = order.get('produit_type', order.get('nature_produit', 'N/A'))
        quantity = order.get('quantite', 'N/A')
        unit = order.get('unite', '')
        order_number = order.get('numero_commande') or f"CMD-{order.get('id')}"
        delivery_date = order.get('date_livraison') or 'À confirmer'
        if delivery_date == 'None' or delivery_date is None:
            delivery_date = 'À confirmer'
        
        subject = f"Confirmation de votre commande {order_number} - TECPAP"
        
        html_content = f"""
<!DOCTYPE html>
<html>
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
</head>
<body style="margin: 0; padding: 0; font-family: 'Segoe UI', Tahoma, Geneva, Verdana, sans-serif; background-color: #f8fafc;">
    <table role="presentation" style="width: 100%; border-collapse: collapse;">
        <tr>
            <td align="center" style="padding: 40px 20px;">
                <table role="presentation" style="width: 100%; max-width: 600px; border-collapse: collapse; background-color: #ffffff; border-radius: 16px; overflow: hidden; box-shadow: 0 4px 24px rgba(0,0,0,0.08);">
                    
                    <!-- Header TECPAP -->
                    <tr>
                        <td style="background: linear-gradient(135deg, #7CB342 0%, #5D4037 100%); padding: 48px 40px; text-align: center;">
                            <div style="margin-bottom: 16px;">
                                <span style="font-size: 28px; color: #ffffff; font-weight: 700;">TECPAP</span>
                            </div>
                            <div style="width: 72px; height: 72px; background-color: rgba(255,255,255,0.15); border-radius: 50%; margin: 0 auto 24px; line-height: 72px;">
                                <span style="font-size: 32px; color: #ffffff;">&#10003;</span>
                            </div>
                            <h1 style="color: #ffffff; margin: 0; font-size: 26px; font-weight: 600; letter-spacing: -0.5px;">Commande Confirmée</h1>
                            <p style="color: rgba(255,255,255,0.85); margin: 12px 0 0; font-size: 15px; font-weight: 400;">Votre commande a été validée avec succès</p>
                        </td>
                    </tr>
                    
                    <!-- Body -->
                    <tr>
                        <td style="padding: 40px;">
                            <p style="color: #1e293b; font-size: 16px; line-height: 1.6; margin: 0 0 24px;">
                                Bonjour <strong>{client_name}</strong>,
                            </p>
                            <p style="color: #64748b; font-size: 15px; line-height: 1.7; margin: 0 0 32px;">
                                Nous avons le plaisir de vous confirmer que votre commande a été validée par notre équipe commerciale et est en cours de préparation.
                            </p>
                            
                            <!-- Order Details Box -->
                            <div style="background-color: #f8fafc; border-radius: 12px; padding: 28px; margin-bottom: 32px; border: 1px solid #e2e8f0;">
                                <h3 style="color: #0f172a; margin: 0 0 20px; font-size: 15px; font-weight: 600; text-transform: uppercase; letter-spacing: 0.5px;">Récapitulatif de commande</h3>
                                <table style="width: 100%; border-collapse: collapse;">
                                    <tr>
                                        <td style="padding: 14px 0; color: #64748b; font-size: 14px; border-bottom: 1px solid #e2e8f0;">Référence</td>
                                        <td style="padding: 14px 0; color: #0f172a; font-size: 14px; font-weight: 600; text-align: right; border-bottom: 1px solid #e2e8f0;">{order_number}</td>
                                    </tr>
                                    <tr>
                                        <td style="padding: 14px 0; color: #64748b; font-size: 14px; border-bottom: 1px solid #e2e8f0;">Produit</td>
                                        <td style="padding: 14px 0; color: #0f172a; font-size: 14px; font-weight: 600; text-align: right; border-bottom: 1px solid #e2e8f0;">{product}</td>
                                    </tr>
                                    <tr>
                                        <td style="padding: 14px 0; color: #64748b; font-size: 14px; border-bottom: 1px solid #e2e8f0;">Quantité</td>
                                        <td style="padding: 14px 0; color: #0f172a; font-size: 14px; font-weight: 600; text-align: right; border-bottom: 1px solid #e2e8f0;">{quantity} {unit}</td>
                                    </tr>
                                    <tr>
                                        <td style="padding: 14px 0; color: #64748b; font-size: 14px;">Livraison estimée</td>
                                        <td style="padding: 14px 0; color: #0f172a; font-size: 14px; font-weight: 600; text-align: right;">{delivery_date}</td>
                                    </tr>
                                </table>
                            </div>
                            
                            <!-- Status Timeline -->
                            <div style="margin-bottom: 32px;">
                                <h3 style="color: #0f172a; margin: 0 0 20px; font-size: 15px; font-weight: 600; text-transform: uppercase; letter-spacing: 0.5px;">Suivi de commande</h3>
                                <table style="width: 100%; border-collapse: collapse;">
                                    <tr>
                                        <td style="width: 32px; vertical-align: top; padding: 0 16px 24px 0;">
                                            <div style="width: 32px; height: 32px; background-color: #059669; border-radius: 50%; text-align: center; line-height: 32px;">
                                                <span style="color: #ffffff; font-size: 14px;">&#10003;</span>
                                            </div>
                                        </td>
                                        <td style="vertical-align: top; padding: 0 0 24px 0;">
                                            <p style="margin: 0; color: #0f172a; font-size: 14px; font-weight: 600;">Commande validée</p>
                                            <p style="margin: 4px 0 0; color: #64748b; font-size: 13px;">Votre commande est confirmée</p>
                                        </td>
                                    </tr>
                                    <tr>
                                        <td style="width: 32px; vertical-align: top; padding: 0 16px 24px 0;">
                                            <div style="width: 32px; height: 32px; background-color: #e2e8f0; border-radius: 50%; text-align: center; line-height: 32px;">
                                                <span style="color: #94a3b8; font-size: 14px;">2</span>
                                            </div>
                                        </td>
                                        <td style="vertical-align: top; padding: 0 0 24px 0;">
                                            <p style="margin: 0; color: #64748b; font-size: 14px; font-weight: 500;">En préparation</p>
                                            <p style="margin: 4px 0 0; color: #94a3b8; font-size: 13px;">Votre commande sera bientôt prête</p>
                                        </td>
                                    </tr>
                                    <tr>
                                        <td style="width: 32px; vertical-align: top; padding: 0 16px 0 0;">
                                            <div style="width: 32px; height: 32px; background-color: #e2e8f0; border-radius: 50%; text-align: center; line-height: 32px;">
                                                <span style="color: #94a3b8; font-size: 14px;">3</span>
                                            </div>
                                        </td>
                                        <td style="vertical-align: top;">
                                            <p style="margin: 0; color: #64748b; font-size: 14px; font-weight: 500;">Expédition</p>
                                            <p style="margin: 4px 0 0; color: #94a3b8; font-size: 13px;">Vous serez notifié lors de l'envoi</p>
                                        </td>
                                    </tr>
                                </table>
                            </div>
                            
                            <p style="color: #64748b; font-size: 14px; line-height: 1.6; margin: 0; padding-top: 16px; border-top: 1px solid #e2e8f0;">
                                Pour toute question concernant votre commande, notre équipe reste à votre disposition.
                            </p>
                        </td>
                    </tr>
                    
                    <!-- Footer -->
                    <tr>
                        <td style="background-color: #f8fafc; padding: 32px 40px; text-align: center; border-top: 1px solid #e2e8f0;">
                            <p style="color: #64748b; font-size: 14px; margin: 0 0 8px; font-weight: 500;">
                                Merci pour votre confiance
                            </p>
                            <p style="color: #5D4037; font-size: 13px; margin: 0 0 4px; font-weight: 600;">
                                TECPAP - Sacs en Papier Kraft
                            </p>
                            <p style="color: #94a3b8; font-size: 11px; margin: 0;">
                                100% biodégradables - 100% recyclables
                            </p>
                            <p style="color: #94a3b8; font-size: 11px; margin: 8px 0 0;">
                                Bouskoura, Casablanca | +212 5 22 86 56 83 | www.tecpap.ma
                            </p>
                        </td>
                    </tr>
                    
                </table>
            </td>
        </tr>
    </table>
</body>
</html>
"""
        
        text_content = f"""
COMMANDE CONFIRMÉE - TECPAP

Bonjour {client_name},

Nous avons le plaisir de vous confirmer que votre commande a été validée.

RÉCAPITULATIF:
• Référence: {order_number}
• Produit: {product}
• Quantité: {quantity} {unit}
• Livraison estimée: {delivery_date}

SUIVI:
1. Commande validée ✓
2. En préparation (en cours)
3. Expédition (à venir)

Pour toute question, notre équipe reste à votre disposition.

Merci pour votre confiance,
TECPAP - Sacs en Papier Kraft
www.tecpap.ma | +212 5 22 86 56 83
"""
        
        return self.send_email(to_email, subject, html_content, text_content)
    
    def send_rejection_email(self, order, reason=''):
        """Send order rejection notification email."""
        to_email = order.get('email_from', '')
        
        # Skip if no valid email
        if not to_email or '@' not in to_email:
            return False
        
        client_name = order.get('client_nom', 'Cher client')
        product = order.get('produit_type', order.get('nature_produit', 'N/A'))
        quantity = order.get('quantite', 'N/A')
        unit = order.get('unite', '')
        order_number = order.get('numero_commande') or f"CMD-{order.get('id')}"
        
        reason_text = reason if reason else "Informations insuffisantes pour traiter la commande"
        
        subject = f"Information concernant votre demande {order_number} - TECPAP"
        
        html_content = f"""
<!DOCTYPE html>
<html>
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
</head>
<body style="margin: 0; padding: 0; font-family: 'Segoe UI', Tahoma, Geneva, Verdana, sans-serif; background-color: #f8fafc;">
    <table role="presentation" style="width: 100%; border-collapse: collapse;">
        <tr>
            <td align="center" style="padding: 40px 20px;">
                <table role="presentation" style="width: 100%; max-width: 600px; border-collapse: collapse; background-color: #ffffff; border-radius: 16px; overflow: hidden; box-shadow: 0 4px 24px rgba(0,0,0,0.08);">
                    
                    <!-- Header TECPAP -->
                    <tr>
                        <td style="background: linear-gradient(135deg, #64748b 0%, #5D4037 100%); padding: 48px 40px; text-align: center;">
                            <div style="margin-bottom: 16px;">
                                <span style="font-size: 28px; color: #ffffff; font-weight: 700;">TECPAP</span>
                            </div>
                            <div style="width: 72px; height: 72px; background-color: rgba(255,255,255,0.15); border-radius: 50%; margin: 0 auto 24px; line-height: 72px;">
                                <span style="font-size: 28px; color: #ffffff;">&#9432;</span>
                            </div>
                            <h1 style="color: #ffffff; margin: 0; font-size: 26px; font-weight: 600; letter-spacing: -0.5px;">Information Importante</h1>
                            <p style="color: rgba(255,255,255,0.85); margin: 12px 0 0; font-size: 15px; font-weight: 400;">Concernant votre demande de commande</p>
                        </td>
                    </tr>
                    
                    <!-- Body -->
                    <tr>
                        <td style="padding: 40px;">
                            <p style="color: #1e293b; font-size: 16px; line-height: 1.6; margin: 0 0 24px;">
                                Bonjour <strong>{client_name}</strong>,
                            </p>
                            <p style="color: #64748b; font-size: 15px; line-height: 1.7; margin: 0 0 32px;">
                                Nous avons bien reçu votre demande et nous vous remercions de votre intérêt. Après examen par notre équipe commerciale, nous ne sommes pas en mesure de valider cette commande en l'état.
                            </p>
                            
                            <!-- Order Details Box -->
                            <div style="background-color: #f8fafc; border-radius: 12px; padding: 28px; margin-bottom: 24px; border: 1px solid #e2e8f0;">
                                <h3 style="color: #0f172a; margin: 0 0 20px; font-size: 15px; font-weight: 600; text-transform: uppercase; letter-spacing: 0.5px;">Détails de la demande</h3>
                                <table style="width: 100%; border-collapse: collapse;">
                                    <tr>
                                        <td style="padding: 14px 0; color: #64748b; font-size: 14px; border-bottom: 1px solid #e2e8f0;">Référence</td>
                                        <td style="padding: 14px 0; color: #0f172a; font-size: 14px; font-weight: 600; text-align: right; border-bottom: 1px solid #e2e8f0;">{order_number}</td>
                                    </tr>
                                    <tr>
                                        <td style="padding: 14px 0; color: #64748b; font-size: 14px; border-bottom: 1px solid #e2e8f0;">Produit</td>
                                        <td style="padding: 14px 0; color: #0f172a; font-size: 14px; font-weight: 600; text-align: right; border-bottom: 1px solid #e2e8f0;">{product}</td>
                                    </tr>
                                    <tr>
                                        <td style="padding: 14px 0; color: #64748b; font-size: 14px;">Quantité</td>
                                        <td style="padding: 14px 0; color: #0f172a; font-size: 14px; font-weight: 600; text-align: right;">{quantity} {unit}</td>
                                    </tr>
                                </table>
                            </div>
                            
                            <!-- Reason Box -->
                            <div style="background-color: #fef2f2; border-radius: 12px; padding: 24px; margin-bottom: 24px; border: 1px solid #fecaca;">
                                <h4 style="color: #991b1b; margin: 0 0 12px; font-size: 14px; font-weight: 600;">Motif de la décision</h4>
                                <p style="color: #7f1d1d; font-size: 14px; line-height: 1.6; margin: 0;">
                                    {reason_text}
                                </p>
                            </div>
                            
                            <!-- Next Steps -->
                            <div style="background-color: #f0f9ff; border-radius: 12px; padding: 24px; margin-bottom: 32px; border: 1px solid #bae6fd;">
                                <h4 style="color: #0369a1; margin: 0 0 16px; font-size: 14px; font-weight: 600;">Comment procéder</h4>
                                <ul style="color: #0c4a6e; font-size: 14px; line-height: 1.8; margin: 0; padding-left: 20px;">
                                    <li>Vérifiez les informations de votre commande</li>
                                    <li>Contactez notre équipe pour plus de précisions</li>
                                    <li>Soumettez une nouvelle demande si nécessaire</li>
                                </ul>
                            </div>
                            
                            <p style="color: #64748b; font-size: 14px; line-height: 1.6; margin: 0; padding-top: 16px; border-top: 1px solid #e2e8f0;">
                                Notre équipe reste à votre disposition pour vous accompagner dans votre démarche.
                            </p>
                        </td>
                    </tr>
                    
                    <!-- Footer -->
                    <tr>
                        <td style="background-color: #f8fafc; padding: 32px 40px; text-align: center; border-top: 1px solid #e2e8f0;">
                            <p style="color: #64748b; font-size: 14px; margin: 0 0 8px; font-weight: 500;">
                                Nous restons à votre écoute
                            </p>
                            <p style="color: #5D4037; font-size: 13px; margin: 0 0 4px; font-weight: 600;">
                                TECPAP - Sacs en Papier Kraft
                            </p>
                            <p style="color: #94a3b8; font-size: 11px; margin: 8px 0 0;">
                                Bouskoura, Casablanca | +212 5 22 86 56 83 | www.tecpap.ma
                            </p>
                        </td>
                    </tr>
                    
                </table>
            </td>
        </tr>
    </table>
</body>
</html>
"""
        
        text_content = f"""
INFORMATION IMPORTANTE - TECPAP

Bonjour {client_name},

Nous avons bien reçu votre demande et nous vous remercions de votre intérêt.
Après examen, nous ne sommes pas en mesure de valider cette commande en l'état.

DÉTAILS:
- Référence: {order_number}
- Produit: {product}
- Quantité: {quantity} {unit}

MOTIF: {reason_text}

COMMENT PROCÉDER:
- Vérifiez les informations de votre commande
- Contactez notre équipe pour plus de précisions
- Soumettez une nouvelle demande si nécessaire

Notre équipe reste à votre disposition.

Cordialement,
TECPAP - Sacs en Papier Kraft
www.tecpap.ma | +212 5 22 86 56 83
"""
        
        return self.send_email(to_email, subject, html_content, text_content)

    def send_order_received_email(self, order):
        """Send order received confirmation email."""
        to_email = order.get('email_from', '')
        
        # Skip if no valid email
        if not to_email or '@' not in to_email:
            return False
        
        client_name = order.get('client_nom', 'Cher client')
        product = order.get('produit_type', order.get('nature_produit', 'N/A'))
        quantity = order.get('quantite', 'N/A')
        unit = order.get('unite', '')
        order_number = order.get('numero_commande') or f"CMD-{order.get('id')}"
        
        subject = f"Commande reçue {order_number} - TECPAP"
        
        html_content = f"""
<!DOCTYPE html>
<html>
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
</head>
<body style="margin: 0; padding: 0; font-family: 'Segoe UI', Tahoma, Geneva, Verdana, sans-serif; background-color: #f8fafc;">
    <table role="presentation" style="width: 100%; border-collapse: collapse;">
        <tr>
            <td align="center" style="padding: 40px 20px;">
                <table role="presentation" style="width: 100%; max-width: 600px; border-collapse: collapse; background-color: #ffffff; border-radius: 16px; overflow: hidden; box-shadow: 0 4px 24px rgba(0,0,0,0.08);">
                    
                    <!-- Header TECPAP -->
                    <tr>
                        <td style="background: linear-gradient(135deg, #3b82f6 0%, #1d4ed8 100%); padding: 48px 40px; text-align: center;">
                            <div style="margin-bottom: 16px;">
                                <span style="font-size: 28px; color: #ffffff; font-weight: 700;">TECPAP</span>
                            </div>
                            <h1 style="color: #ffffff; margin: 0; font-size: 26px; font-weight: 600; letter-spacing: -0.5px;">Commande Reçue</h1>
                            <p style="color: rgba(255,255,255,0.85); margin: 12px 0 0; font-size: 15px; font-weight: 400;">Nous avons bien reçu votre commande</p>
                        </td>
                    </tr>
                    
                    <!-- Body -->
                    <tr>
                        <td style="padding: 40px;">
                            <p style="color: #1e293b; font-size: 16px; line-height: 1.6; margin: 0 0 24px;">
                                Bonjour <strong>{client_name}</strong>,
                            </p>
                            <p style="color: #64748b; font-size: 15px; line-height: 1.7; margin: 0 0 32px;">
                                Nous avons bien reçu votre demande de commande. Notre équipe commerciale va l'examiner dans les plus brefs délais et vous tiendra informé de la suite.
                            </p>
                            
                            <!-- Order Details Box -->
                            <div style="background-color: #f8fafc; border-radius: 12px; padding: 28px; margin-bottom: 32px; border: 1px solid #e2e8f0;">
                                <h3 style="color: #0f172a; margin: 0 0 20px; font-size: 15px; font-weight: 600; text-transform: uppercase; letter-spacing: 0.5px;">Détails de votre commande</h3>
                                <table style="width: 100%; border-collapse: collapse;">
                                    <tr>
                                        <td style="padding: 14px 0; color: #64748b; font-size: 14px; border-bottom: 1px solid #e2e8f0;">Référence</td>
                                        <td style="padding: 14px 0; color: #0f172a; font-size: 14px; font-weight: 600; text-align: right; border-bottom: 1px solid #e2e8f0;">{order_number}</td>
                                    </tr>
                                    <tr>
                                        <td style="padding: 14px 0; color: #64748b; font-size: 14px; border-bottom: 1px solid #e2e8f0;">Produit</td>
                                        <td style="padding: 14px 0; color: #0f172a; font-size: 14px; font-weight: 600; text-align: right; border-bottom: 1px solid #e2e8f0;">{product}</td>
                                    </tr>
                                    <tr>
                                        <td style="padding: 14px 0; color: #64748b; font-size: 14px;">Quantité</td>
                                        <td style="padding: 14px 0; color: #0f172a; font-size: 14px; font-weight: 600; text-align: right;">{quantity} {unit}</td>
                                    </tr>
                                </table>
                            </div>
                            
                            <!-- Status Info -->
                            <div style="background-color: #eff6ff; border-radius: 12px; padding: 24px; margin-bottom: 32px; border: 1px solid #bfdbfe;">
                                <h4 style="color: #1d4ed8; margin: 0 0 12px; font-size: 14px; font-weight: 600;">Prochaines étapes</h4>
                                <ul style="color: #1e40af; font-size: 14px; line-height: 1.8; margin: 0; padding-left: 20px;">
                                    <li>Examen de votre commande par notre équipe</li>
                                    <li>Confirmation de validation sous 24-48h</li>
                                    <li>Notification par email du statut final</li>
                                </ul>
                            </div>
                            
                            <p style="color: #64748b; font-size: 14px; line-height: 1.6; margin: 0; padding-top: 16px; border-top: 1px solid #e2e8f0;">
                                Pour toute question, n'hésitez pas à nous contacter. Merci pour votre confiance !
                            </p>
                        </td>
                    </tr>
                    
                    <!-- Footer -->
                    <tr>
                        <td style="background-color: #f8fafc; padding: 32px 40px; text-align: center; border-top: 1px solid #e2e8f0;">
                            <p style="color: #64748b; font-size: 14px; margin: 0 0 8px; font-weight: 500;">
                                Merci pour votre confiance
                            </p>
                            <p style="color: #5D4037; font-size: 13px; margin: 0 0 4px; font-weight: 600;">
                                TECPAP - Sacs en Papier Kraft
                            </p>
                            <p style="color: #94a3b8; font-size: 11px; margin: 0;">
                                100% biodégradables - 100% recyclables
                            </p>
                            <p style="color: #94a3b8; font-size: 11px; margin: 8px 0 0;">
                                Bouskoura, Casablanca | +212 5 22 86 56 83 | www.tecpap.ma
                            </p>
                        </td>
                    </tr>
                    
                </table>
            </td>
        </tr>
    </table>
</body>
</html>
"""
        
        text_content = f"""
COMMANDE REÇUE - TECPAP

Bonjour {client_name},

Nous avons bien reçu votre demande de commande.
Notre équipe commerciale va l'examiner dans les plus brefs délais.

DÉTAILS:
• Référence: {order_number}
• Produit: {product}
• Quantité: {quantity} {unit}

PROCHAINES ÉTAPES:
1. Examen de votre commande par notre équipe
2. Confirmation de validation sous 24-48h
3. Notification par email du statut final

Pour toute question, n'hésitez pas à nous contacter.

Merci pour votre confiance,
TECPAP - Sacs en Papier Kraft
www.tecpap.ma | +212 5 22 86 56 83
"""
        
        return self.send_email(to_email, subject, html_content, text_content)


# Singleton instance
email_sender = EmailSender()
