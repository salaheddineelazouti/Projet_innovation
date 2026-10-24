"""Envoi des emails de notification aux clients (réception, validation, rejet de commande).

Les messages sont envoyés en HTML avec une version texte de secours, par SMTP avec
chiffrement STARTTLS. Les valeurs issues de la commande sont échappées avant d'être insérées
dans le HTML : elles proviennent de messages externes.
"""

from __future__ import annotations

import html
import logging
import smtplib
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from typing import Any

from tecpap.config import Settings, load_settings

logger = logging.getLogger(__name__)


def _is_valid_address(address: str | None) -> bool:
    return bool(address) and "@" in address


def _order_summary(order: dict[str, Any]) -> dict[str, str]:
    """Valeurs communes aux trois emails, en texte brut."""
    return {
        "client_name": str(order.get("client_nom") or "Cher client"),
        "product": str(order.get("produit_type") or order.get("nature_produit") or "N/A"),
        "quantity": str(order.get("quantite", "N/A")),
        "unit": str(order.get("unite") or ""),
        "order_number": str(order.get("numero_commande") or f"CMD-{order.get('id')}"),
    }


def _escaped(values: dict[str, str]) -> dict[str, str]:
    return {key: html.escape(value) for key, value in values.items()}


# Gabarits des emails (HTML et texte brut).


def _validation_html(
    *, client_name: str, product: str, quantity: str, unit: str, order_number: str, delivery_date: str,
) -> str:
    return f"""
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

                    <!-- En-tête TECPAP -->
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

                    <!-- Corps du message -->
                    <tr>
                        <td style="padding: 40px;">
                            <p style="color: #1e293b; font-size: 16px; line-height: 1.6; margin: 0 0 24px;">
                                Bonjour <strong>{client_name}</strong>,
                            </p>
                            <p style="color: #64748b; font-size: 15px; line-height: 1.7; margin: 0 0 32px;">
                                Nous avons le plaisir de vous confirmer que votre commande a été validée par notre équipe commerciale et est en cours de préparation.
                            </p>

                            <!-- Récapitulatif de la commande -->
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

                            <!-- Suivi de commande -->
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

                    <!-- Pied de page -->
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


def _validation_text(
    *, client_name: str, product: str, quantity: str, unit: str, order_number: str, delivery_date: str,
) -> str:
    return f"""
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
3. Expédition (prochaine étape)

Pour toute question, notre équipe reste à votre disposition.

Merci pour votre confiance,
TECPAP - Sacs en Papier Kraft
www.tecpap.ma | +212 5 22 86 56 83
"""


def _rejection_html(
    *, client_name: str, product: str, quantity: str, unit: str, order_number: str, reason_text: str,
) -> str:
    return f"""
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

                    <!-- En-tête TECPAP -->
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

                    <!-- Corps du message -->
                    <tr>
                        <td style="padding: 40px;">
                            <p style="color: #1e293b; font-size: 16px; line-height: 1.6; margin: 0 0 24px;">
                                Bonjour <strong>{client_name}</strong>,
                            </p>
                            <p style="color: #64748b; font-size: 15px; line-height: 1.7; margin: 0 0 32px;">
                                Nous avons bien reçu votre demande et nous vous remercions de votre intérêt. Après examen par notre équipe commerciale, nous ne sommes pas en mesure de valider cette commande en l'état.
                            </p>

                            <!-- Récapitulatif de la commande -->
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

                            <!-- Motif de la décision -->
                            <div style="background-color: #fef2f2; border-radius: 12px; padding: 24px; margin-bottom: 24px; border: 1px solid #fecaca;">
                                <h4 style="color: #991b1b; margin: 0 0 12px; font-size: 14px; font-weight: 600;">Motif de la décision</h4>
                                <p style="color: #7f1d1d; font-size: 14px; line-height: 1.6; margin: 0;">
                                    {reason_text}
                                </p>
                            </div>

                            <!-- Étapes suivantes -->
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

                    <!-- Pied de page -->
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


def _rejection_text(
    *, client_name: str, product: str, quantity: str, unit: str, order_number: str, reason_text: str,
) -> str:
    return f"""
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


def _received_html(
    *, client_name: str, product: str, quantity: str, unit: str, order_number: str,
) -> str:
    return f"""
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

                    <!-- En-tête TECPAP -->
                    <tr>
                        <td style="background: linear-gradient(135deg, #3b82f6 0%, #1d4ed8 100%); padding: 48px 40px; text-align: center;">
                            <div style="margin-bottom: 16px;">
                                <span style="font-size: 28px; color: #ffffff; font-weight: 700;">TECPAP</span>
                            </div>
                            <h1 style="color: #ffffff; margin: 0; font-size: 26px; font-weight: 600; letter-spacing: -0.5px;">Commande Reçue</h1>
                            <p style="color: rgba(255,255,255,0.85); margin: 12px 0 0; font-size: 15px; font-weight: 400;">Nous avons bien reçu votre commande</p>
                        </td>
                    </tr>

                    <!-- Corps du message -->
                    <tr>
                        <td style="padding: 40px;">
                            <p style="color: #1e293b; font-size: 16px; line-height: 1.6; margin: 0 0 24px;">
                                Bonjour <strong>{client_name}</strong>,
                            </p>
                            <p style="color: #64748b; font-size: 15px; line-height: 1.7; margin: 0 0 32px;">
                                Nous avons bien reçu votre demande de commande. Notre équipe commerciale va l'examiner dans les plus brefs délais et vous tiendra informé de la suite.
                            </p>

                            <!-- Récapitulatif de la commande -->
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

                            <!-- Prochaines étapes -->
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

                    <!-- Pied de page -->
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


def _received_text(
    *, client_name: str, product: str, quantity: str, unit: str, order_number: str,
) -> str:
    return f"""
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


class EmailSender:
    """Envoi d'emails par SMTP avec le compte Gmail de l'entreprise.

    Args:
        settings: configuration ; par défaut celle de l'environnement (:func:`load_settings`).
    """

    company_name = "TECPAP"

    def __init__(self, settings: Settings | None = None):
        self.settings = settings or load_settings()
        self.smtp_server = self.settings.smtp_server
        self.smtp_port = self.settings.smtp_port
        self.email = self.settings.gmail_email
        self.password = self.settings.gmail_app_password
        self.company_email = self.email

    def send_email(self, to_email: str | None, subject: str, html_content: str,
                   text_content: str | None = None) -> bool:
        """Envoie un email HTML (avec version texte facultative). Renvoie ``True`` en cas de succès."""
        if not self.email or not self.password:
            logger.warning("Configuration email absente (GMAIL_EMAIL ou GMAIL_APP_PASSWORD)")
            return False
        if not _is_valid_address(to_email):
            logger.warning("Adresse email invalide : %r", to_email)
            return False

        try:
            msg = MIMEMultipart("alternative")
            msg["Subject"] = subject
            msg["From"] = f"{self.company_name} <{self.email}>"
            msg["To"] = to_email
            if text_content:
                msg.attach(MIMEText(text_content, "plain", "utf-8"))
            msg.attach(MIMEText(html_content, "html", "utf-8"))

            with smtplib.SMTP(self.smtp_server, self.smtp_port, timeout=30) as server:
                server.starttls()
                # Les mots de passe d'application Gmail sont affichés par groupes séparés d'espaces.
                server.login(self.email, self.password.replace(" ", ""))
                server.send_message(msg)

            logger.info("Email envoyé à %s", to_email)
            return True
        except (smtplib.SMTPException, OSError):
            logger.exception("Échec de l'envoi de l'email à %s", to_email)
            return False

    def send_validation_email(self, order: dict[str, Any]) -> bool:
        """Envoie au client la confirmation que sa commande est validée."""
        to_email = order.get("email_from", "")
        if not _is_valid_address(to_email):
            return False
        values = _order_summary(order)
        delivery = order.get("date_livraison")
        values["delivery_date"] = "À confirmer" if delivery in (None, "", "None") else str(delivery)
        subject = f"Confirmation de votre commande {values['order_number']} - TECPAP"
        return self.send_email(to_email, subject, _validation_html(**_escaped(values)), _validation_text(**values))

    def send_rejection_email(self, order: dict[str, Any], reason: str = "") -> bool:
        """Informe le client que sa commande n'a pas été validée, avec le motif."""
        to_email = order.get("email_from", "")
        if not _is_valid_address(to_email):
            return False
        values = _order_summary(order)
        values["reason_text"] = reason or "Informations insuffisantes pour traiter la commande"
        subject = f"Information concernant votre demande {values['order_number']} - TECPAP"
        return self.send_email(to_email, subject, _rejection_html(**_escaped(values)), _rejection_text(**values))

    def send_order_received_email(self, order: dict[str, Any]) -> bool:
        """Accuse réception de la commande auprès du client."""
        to_email = order.get("email_from", "")
        if not _is_valid_address(to_email):
            return False
        values = _order_summary(order)
        subject = f"Commande reçue {values['order_number']} - TECPAP"
        return self.send_email(to_email, subject, _received_html(**_escaped(values)), _received_text(**values))
