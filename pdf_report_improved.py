"""
TECPAP - Professional PDF Report Generator
Rapport BI avec analyses avancées, graphiques et KPIs
"""

import os
import io
from datetime import datetime, timedelta

def generate_pdf_report_improved(db, filepath="exports/rapport.pdf", period="month"):
    """Generate professional BI PDF report with logo, charts and analytics."""
    from reportlab.lib import colors
    from reportlab.lib.pagesizes import A4
    from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
    from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle, Image, PageBreak, HRFlowable, KeepTogether
    from reportlab.lib.units import cm, mm
    from reportlab.lib.enums import TA_CENTER, TA_LEFT, TA_RIGHT
    import matplotlib
    matplotlib.use('Agg')
    import matplotlib.pyplot as plt
    import numpy as np

    os.makedirs(os.path.dirname(filepath), exist_ok=True)

    # Logo path
    logo_path = os.path.join(os.path.dirname(__file__), "static", "images", "logo_tecpap.png")

    # Colors
    TECPAP_GREEN = '#16a34a'
    TECPAP_DARK = '#334155'
    TECPAP_LIGHT = '#f0fdf4'

    # Page template with header/footer and logo
    def add_header_footer(canvas, doc):
        canvas.saveState()

        # Header with logo
        if os.path.exists(logo_path):
            canvas.drawImage(logo_path, 2*cm, A4[1] - 2.2*cm, width=3*cm, height=1.5*cm, preserveAspectRatio=True, mask='auto')

        # Header line
        canvas.setStrokeColor(colors.HexColor(TECPAP_GREEN))
        canvas.setLineWidth(2)
        canvas.line(2*cm, A4[1] - 2.5*cm, A4[0] - 2*cm, A4[1] - 2.5*cm)

        # Header text
        canvas.setFont('Helvetica-Bold', 10)
        canvas.setFillColor(colors.HexColor(TECPAP_DARK))
        canvas.drawRightString(A4[0] - 2*cm, A4[1] - 1.5*cm, "Rapport Business Intelligence")
        canvas.setFont('Helvetica', 8)
        canvas.drawRightString(A4[0] - 2*cm, A4[1] - 2*cm, datetime.now().strftime('%d/%m/%Y %H:%M'))

        # Footer
        canvas.setStrokeColor(colors.HexColor('#e2e8f0'))
        canvas.setLineWidth(1)
        canvas.line(2*cm, 2*cm, A4[0] - 2*cm, 2*cm)

        canvas.setFont('Helvetica', 8)
        canvas.setFillColor(colors.HexColor('#64748b'))
        canvas.drawString(2*cm, 1.5*cm, "TECPAP - Fabrication de sacs en papier Kraft biodégradables | www.tecpap.ma")
        canvas.drawRightString(A4[0] - 2*cm, 1.5*cm, f"Page {doc.page}")

        canvas.restoreState()

    doc = SimpleDocTemplate(filepath, pagesize=A4, rightMargin=2*cm, leftMargin=2*cm, topMargin=3*cm, bottomMargin=2.5*cm)
    styles = getSampleStyleSheet()
    elements = []

    # Custom styles
    title_style = ParagraphStyle('Title', parent=styles['Heading1'], fontSize=24, textColor=colors.HexColor(TECPAP_GREEN), spaceAfter=5, alignment=TA_CENTER, fontName='Helvetica-Bold')
    subtitle_style = ParagraphStyle('Subtitle', parent=styles['Normal'], fontSize=11, textColor=colors.HexColor('#64748b'), alignment=TA_CENTER, spaceAfter=15)
    section_style = ParagraphStyle('Section', parent=styles['Heading2'], fontSize=14, textColor=colors.HexColor(TECPAP_GREEN), spaceBefore=15, spaceAfter=8, fontName='Helvetica-Bold')
    subsection_style = ParagraphStyle('Subsection', parent=styles['Heading3'], fontSize=11, textColor=colors.HexColor(TECPAP_DARK), spaceBefore=10, spaceAfter=5, fontName='Helvetica-Bold')
    body_style = ParagraphStyle('Body', parent=styles['Normal'], fontSize=9, textColor=colors.HexColor(TECPAP_DARK), spaceAfter=8)
    kpi_value_style = ParagraphStyle('KPIValue', fontSize=22, textColor=colors.HexColor(TECPAP_GREEN), alignment=TA_CENTER, fontName='Helvetica-Bold')
    kpi_label_style = ParagraphStyle('KPILabel', fontSize=8, textColor=colors.HexColor('#64748b'), alignment=TA_CENTER)

    # ============ GET ALL DATA ============
    from analytics import Analytics
    analytics = Analytics(db)
    stats = analytics.get_dashboard_stats()
    cursor = db.connection.cursor()

    # Additional queries for BI
    cursor.execute("SELECT COUNT(*) FROM commandes WHERE source = 'whatsapp'")
    whatsapp_count = cursor.fetchone()[0]
    cursor.execute("SELECT COUNT(*) FROM commandes WHERE source = 'email' OR source IS NULL")
    email_count = cursor.fetchone()[0]

    # Weekly trend
    cursor.execute("""
        SELECT DATE(created_at) as date, COUNT(*) as count, SUM(quantite) as qty
        FROM commandes WHERE created_at >= datetime('now', '-7 days')
        GROUP BY DATE(created_at) ORDER BY date
    """)
    weekly_data = [dict(row) for row in cursor.fetchall()]

    # Validation rate by source
    cursor.execute("""
        SELECT source,
               COUNT(*) as total,
               SUM(CASE WHEN statut = 'validee' THEN 1 ELSE 0 END) as validated
        FROM commandes GROUP BY source
    """)
    source_validation = {row[0] or 'email': {'total': row[1], 'validated': row[2]} for row in cursor.fetchall()}

    # Average processing time (orders validated today)
    cursor.execute("""
        SELECT AVG(julianday(validated_at) - julianday(created_at)) * 24 as avg_hours
        FROM commandes WHERE statut = 'validee' AND validated_at IS NOT NULL
    """)
    avg_processing = cursor.fetchone()[0] or 0

    # Top products with revenue
    cursor.execute("""
        SELECT p.type, COUNT(*) as orders, SUM(c.quantite) as qty, SUM(c.prix_total) as revenue
        FROM commandes c
        LEFT JOIN produits p ON c.produit_id = p.id
        WHERE p.type IS NOT NULL
        GROUP BY p.type ORDER BY orders DESC
    """)
    products_data = [dict(row) for row in cursor.fetchall()]

    # Client segments
    cursor.execute("""
        SELECT
            CASE
                WHEN order_count >= 5 THEN 'Fidèles (5+)'
                WHEN order_count >= 2 THEN 'Réguliers (2-4)'
                ELSE 'Nouveaux (1)'
            END as segment,
            COUNT(*) as clients
        FROM (
            SELECT client_id, COUNT(*) as order_count
            FROM commandes GROUP BY client_id
        ) GROUP BY segment
    """)
    client_segments = {row[0]: row[1] for row in cursor.fetchall()}

    # ============ PAGE 1: EXECUTIVE SUMMARY ============
    elements.append(Spacer(1, 1*cm))
    elements.append(Paragraph("Tableau de Bord des Commandes", title_style))
    elements.append(Paragraph("Analyse Business Intelligence - TECPAP", subtitle_style))
    elements.append(Paragraph(f"Période: Toutes les données | Généré le {datetime.now().strftime('%d/%m/%Y à %H:%M')}", subtitle_style))
    elements.append(Spacer(1, 0.5*cm))

    # KPI CARDS
    elements.append(Paragraph("Indicateurs Clés de Performance (KPIs)", section_style))

    validation_rate = (stats["validated_orders"] / stats["total_orders"] * 100) if stats["total_orders"] > 0 else 0
    rejection_rate = (stats["rejected_orders"] / stats["total_orders"] * 100) if stats["total_orders"] > 0 else 0
    whatsapp_rate = (whatsapp_count / stats["total_orders"] * 100) if stats["total_orders"] > 0 else 0

    kpi_data = [
        [
            create_kpi_box("COMMANDES", str(stats["total_orders"]), "Total reçues", TECPAP_GREEN),
            create_kpi_box("VALIDÉES", str(stats["validated_orders"]), f"{validation_rate:.1f}% du total", "#16a34a"),
            create_kpi_box("EN ATTENTE", str(stats["pending_orders"]), "À traiter", "#f59e0b"),
            create_kpi_box("REJETÉES", str(stats["rejected_orders"]), f"{rejection_rate:.1f}% du total", "#ef4444"),
        ],
        [
            create_kpi_box("CA TOTAL", f"{stats['total_revenue']:,.0f}", "MAD", "#0ea5e9"),
            create_kpi_box("PANIER MOYEN", f"{stats['avg_order_value']:,.0f}", "MAD/commande", "#8b5cf6"),
            create_kpi_box("CLIENTS", str(stats["total_clients"]), "Actifs", "#06b6d4"),
            create_kpi_box("WHATSAPP", f"{whatsapp_rate:.0f}%", f"{whatsapp_count} commandes", "#25D366"),
        ]
    ]

    kpi_table = Table(kpi_data, colWidths=[4*cm, 4*cm, 4*cm, 4*cm])
    kpi_table.setStyle(TableStyle([
        ('ALIGN', (0, 0), (-1, -1), 'CENTER'),
        ('VALIGN', (0, 0), (-1, -1), 'MIDDLE'),
        ('TOPPADDING', (0, 0), (-1, -1), 5),
        ('BOTTOMPADDING', (0, 0), (-1, -1), 5),
    ]))
    elements.append(kpi_table)
    elements.append(Spacer(1, 0.8*cm))

    # CHARTS ROW 1: Status & Source Distribution
    elements.append(Paragraph("Analyse de la Répartition", section_style))

    fig, axes = plt.subplots(1, 3, figsize=(14, 4))

    # Chart 1: Status Distribution
    status_labels = ['Validées', 'En Attente', 'Rejetées']
    status_values = [stats["validated_orders"], stats["pending_orders"], stats["rejected_orders"]]
    status_colors = ['#16a34a', '#f59e0b', '#ef4444']

    if sum(status_values) > 0:
        wedges, texts, autotexts = axes[0].pie(status_values, labels=status_labels, colors=status_colors,
                                               autopct='%1.1f%%', startangle=90, explode=(0.02, 0.02, 0.02))
        axes[0].set_title('Répartition par Statut', fontweight='bold', fontsize=11, color='#334155', pad=10)
        for autotext in autotexts:
            autotext.set_fontsize(9)
            autotext.set_fontweight('bold')

    # Chart 2: Source Distribution
    source_labels = ['WhatsApp', 'Email']
    source_values = [whatsapp_count, email_count]
    source_colors = ['#25D366', '#3b82f6']

    if sum(source_values) > 0:
        wedges2, texts2, autotexts2 = axes[1].pie(source_values, labels=source_labels, colors=source_colors,
                                                   autopct='%1.1f%%', startangle=90, explode=(0.02, 0.02))
        axes[1].set_title('Répartition par Canal', fontweight='bold', fontsize=11, color='#334155', pad=10)
        for autotext in autotexts2:
            autotext.set_fontsize(9)
            autotext.set_fontweight('bold')

    # Chart 3: Client Segments
    if client_segments:
        seg_labels = list(client_segments.keys())
        seg_values = list(client_segments.values())
        seg_colors = ['#16a34a', '#0ea5e9', '#f59e0b'][:len(seg_labels)]

        wedges3, texts3, autotexts3 = axes[2].pie(seg_values, labels=seg_labels, colors=seg_colors,
                                                   autopct='%1.1f%%', startangle=90)
        axes[2].set_title('Segmentation Clients', fontweight='bold', fontsize=11, color='#334155', pad=10)
        for autotext in autotexts3:
            autotext.set_fontsize(9)
            autotext.set_fontweight('bold')

    plt.tight_layout()
    chart1_buffer = io.BytesIO()
    plt.savefig(chart1_buffer, format='png', dpi=150, bbox_inches='tight', facecolor='white', edgecolor='none')
    chart1_buffer.seek(0)
    plt.close()

    elements.append(Image(chart1_buffer, width=17*cm, height=5.5*cm))
    elements.append(Spacer(1, 0.5*cm))

    # PERFORMANCE TABLE
    elements.append(Paragraph("Performance par Canal", subsection_style))

    perf_data = [["Canal", "Commandes", "Validées", "Taux Validation", "Tendance"]]
    for source, data in source_validation.items():
        rate = (data['validated'] / data['total'] * 100) if data['total'] > 0 else 0
        trend = "↑ Bon" if rate >= 70 else "→ Moyen" if rate >= 50 else "↓ À améliorer"
        source_name = "WhatsApp" if source == "whatsapp" else "Email"
        perf_data.append([source_name, str(data['total']), str(data['validated']), f"{rate:.1f}%", trend])

    perf_table = Table(perf_data, colWidths=[3.5*cm, 3*cm, 3*cm, 3.5*cm, 3*cm])
    perf_table.setStyle(TableStyle([
        ('BACKGROUND', (0, 0), (-1, 0), colors.HexColor(TECPAP_GREEN)),
        ('TEXTCOLOR', (0, 0), (-1, 0), colors.white),
        ('ALIGN', (0, 0), (-1, -1), 'CENTER'),
        ('FONTNAME', (0, 0), (-1, 0), 'Helvetica-Bold'),
        ('FONTSIZE', (0, 0), (-1, -1), 9),
        ('TOPPADDING', (0, 0), (-1, -1), 6),
        ('BOTTOMPADDING', (0, 0), (-1, -1), 6),
        ('ROWBACKGROUNDS', (0, 1), (-1, -1), [colors.HexColor('#f0fdf4'), colors.white]),
        ('GRID', (0, 0), (-1, -1), 0.5, colors.HexColor('#bbf7d0')),
        ('BOX', (0, 0), (-1, -1), 1, colors.HexColor(TECPAP_GREEN)),
    ]))
    elements.append(perf_table)

    elements.append(PageBreak())

    # ============ PAGE 2: DETAILED ANALYTICS ============
    elements.append(Paragraph("Analyse Détaillée", title_style))
    elements.append(Spacer(1, 0.5*cm))

    # TREND ANALYSIS
    elements.append(Paragraph("Évolution des Commandes", section_style))

    fig, axes = plt.subplots(1, 2, figsize=(14, 4.5))

    # Monthly trend bar chart
    if stats["monthly_trends"]:
        months = [t["month"] for t in reversed(stats["monthly_trends"][:6])]
        counts = [t["count"] for t in reversed(stats["monthly_trends"][:6])]
        revenues = [(t["revenue"] or 0)/1000 for t in reversed(stats["monthly_trends"][:6])]

        x = np.arange(len(months))
        width = 0.35

        bars1 = axes[0].bar(x - width/2, counts, width, label='Commandes', color='#16a34a', edgecolor='#15803d')
        ax2 = axes[0].twinx()
        bars2 = ax2.bar(x + width/2, revenues, width, label='CA (K MAD)', color='#0ea5e9', edgecolor='#0284c7')

        axes[0].set_xlabel('Mois', fontweight='bold', fontsize=10)
        axes[0].set_ylabel('Nombre de Commandes', fontweight='bold', fontsize=10, color='#16a34a')
        ax2.set_ylabel('Chiffre d\'Affaires (K MAD)', fontweight='bold', fontsize=10, color='#0ea5e9')
        axes[0].set_title('Tendance Mensuelle: Commandes vs CA', fontweight='bold', fontsize=11, color='#334155', pad=10)
        axes[0].set_xticks(x)
        axes[0].set_xticklabels(months, rotation=45, ha='right')
        axes[0].legend(loc='upper left', fontsize=8)
        ax2.legend(loc='upper right', fontsize=8)

        for bar, count in zip(bars1, counts):
            axes[0].text(bar.get_x() + bar.get_width()/2, bar.get_height() + 0.3, str(count),
                        ha='center', va='bottom', fontsize=8, fontweight='bold', color='#16a34a')

    # Weekly trend line chart
    if weekly_data:
        days = [d["date"][-5:] for d in weekly_data]  # MM-DD format
        daily_counts = [d["count"] for d in weekly_data]

        axes[1].plot(days, daily_counts, marker='o', linewidth=2, markersize=8, color='#16a34a')
        axes[1].fill_between(days, daily_counts, alpha=0.3, color='#16a34a')
        axes[1].set_xlabel('Date', fontweight='bold', fontsize=10)
        axes[1].set_ylabel('Commandes', fontweight='bold', fontsize=10)
        axes[1].set_title('Tendance des 7 Derniers Jours', fontweight='bold', fontsize=11, color='#334155', pad=10)
        axes[1].grid(True, alpha=0.3)

        for i, (day, count) in enumerate(zip(days, daily_counts)):
            axes[1].annotate(str(count), (day, count), textcoords="offset points",
                           xytext=(0, 10), ha='center', fontsize=9, fontweight='bold')

    plt.tight_layout()
    chart2_buffer = io.BytesIO()
    plt.savefig(chart2_buffer, format='png', dpi=150, bbox_inches='tight', facecolor='white')
    chart2_buffer.seek(0)
    plt.close()

    elements.append(Image(chart2_buffer, width=17*cm, height=6*cm))
    elements.append(Spacer(1, 0.5*cm))

    # TOP CLIENTS TABLE
    elements.append(Paragraph("Top 10 Clients par Chiffre d'Affaires", section_style))

    if stats["top_clients"]:
        client_header = [["#", "Client", "Commandes", "CA Total (MAD)", "Panier Moyen", "Part CA"]]
        total_ca = stats["total_revenue"] or 1

        for i, client in enumerate(stats["top_clients"][:10], 1):
            ca = client['total_spent'] or 0
            avg = ca / client['order_count'] if client['order_count'] > 0 else 0
            share = (ca / total_ca * 100)
            client_header.append([
                str(i),
                (client["nom"] or "N/A")[:20],
                str(client["order_count"]),
                f"{ca:,.0f}",
                f"{avg:,.0f}",
                f"{share:.1f}%"
            ])

        client_table = Table(client_header, colWidths=[1*cm, 5*cm, 2.5*cm, 3*cm, 3*cm, 2*cm])
        client_table.setStyle(TableStyle([
            ('BACKGROUND', (0, 0), (-1, 0), colors.HexColor('#0ea5e9')),
            ('TEXTCOLOR', (0, 0), (-1, 0), colors.white),
            ('ALIGN', (0, 0), (-1, -1), 'CENTER'),
            ('ALIGN', (1, 1), (1, -1), 'LEFT'),
            ('FONTNAME', (0, 0), (-1, 0), 'Helvetica-Bold'),
            ('FONTSIZE', (0, 0), (-1, -1), 9),
            ('TOPPADDING', (0, 0), (-1, -1), 5),
            ('BOTTOMPADDING', (0, 0), (-1, -1), 5),
            ('ROWBACKGROUNDS', (0, 1), (-1, -1), [colors.HexColor('#f0f9ff'), colors.white]),
            ('GRID', (0, 0), (-1, -1), 0.5, colors.HexColor('#bae6fd')),
            ('BOX', (0, 0), (-1, -1), 1, colors.HexColor('#0ea5e9')),
        ]))
        elements.append(client_table)

    elements.append(Spacer(1, 0.5*cm))

    # PRODUCTS ANALYSIS
    elements.append(Paragraph("Analyse par Produit", section_style))

    if products_data:
        fig, axes = plt.subplots(1, 2, figsize=(14, 4))

        # Product distribution
        prod_names = [(p["type"] or "Autre")[:15] for p in products_data[:5]]
        prod_orders = [p["orders"] for p in products_data[:5]]
        prod_colors = ['#16a34a', '#0ea5e9', '#8b5cf6', '#f59e0b', '#ef4444']

        bars = axes[0].barh(prod_names, prod_orders, color=prod_colors[:len(prod_names)], edgecolor='white')
        axes[0].set_xlabel('Nombre de Commandes', fontweight='bold', fontsize=10)
        axes[0].set_title('Top 5 Produits par Volume', fontweight='bold', fontsize=11, color='#334155', pad=10)
        axes[0].invert_yaxis()

        for bar, count in zip(bars, prod_orders):
            axes[0].text(bar.get_width() + 0.5, bar.get_y() + bar.get_height()/2, str(count),
                        va='center', fontsize=9, fontweight='bold')

        # Product revenue
        prod_revenues = [(p["revenue"] or 0)/1000 for p in products_data[:5]]

        bars2 = axes[1].barh(prod_names, prod_revenues, color=prod_colors[:len(prod_names)], edgecolor='white')
        axes[1].set_xlabel('Chiffre d\'Affaires (K MAD)', fontweight='bold', fontsize=10)
        axes[1].set_title('Top 5 Produits par CA', fontweight='bold', fontsize=11, color='#334155', pad=10)
        axes[1].invert_yaxis()

        for bar, rev in zip(bars2, prod_revenues):
            axes[1].text(bar.get_width() + 0.2, bar.get_y() + bar.get_height()/2, f"{rev:.1f}K",
                        va='center', fontsize=9, fontweight='bold')

        plt.tight_layout()
        chart3_buffer = io.BytesIO()
        plt.savefig(chart3_buffer, format='png', dpi=150, bbox_inches='tight', facecolor='white')
        chart3_buffer.seek(0)
        plt.close()

        elements.append(Image(chart3_buffer, width=17*cm, height=5.5*cm))

    elements.append(PageBreak())

    # ============ PAGE 3: RECENT ORDERS & RECOMMENDATIONS ============
    elements.append(Paragraph("Détail des Commandes Récentes", title_style))
    elements.append(Spacer(1, 0.5*cm))

    # RECENT ORDERS TABLE
    elements.append(Paragraph("15 Dernières Commandes", section_style))

    cursor.execute("""
        SELECT c.id, c.numero_commande, cl.nom, c.nature_produit, c.quantite,
               c.prix_total, c.statut, c.source, c.created_at
        FROM commandes c LEFT JOIN clients cl ON c.client_id = cl.id
        ORDER BY c.created_at DESC LIMIT 15
    """)
    recent_orders = cursor.fetchall()

    if recent_orders:
        orders_header = [["ID", "Client", "Produit", "Qté", "Montant", "Statut", "Source", "Date"]]
        for order in recent_orders:
            statut_display = {"en_attente": "⏳ Attente", "validee": "✅ Validée", "rejetee": "❌ Rejetée"}.get(order[6], order[6])
            source_display = "📱 WhatsApp" if order[7] == "whatsapp" else "📧 Email"
            montant = f"{order[5]:,.0f}" if order[5] else "-"
            orders_header.append([
                str(order[0]),
                (order[2] or "N/A")[:12],
                (order[3] or "N/A")[:15],
                str(order[4] or 0),
                montant,
                statut_display,
                source_display,
                (order[8] or "N/A")[:10]
            ])

        orders_table = Table(orders_header, colWidths=[1*cm, 2.5*cm, 3.5*cm, 1.2*cm, 2*cm, 2*cm, 2.3*cm, 2*cm])
        orders_table.setStyle(TableStyle([
            ('BACKGROUND', (0, 0), (-1, 0), colors.HexColor(TECPAP_DARK)),
            ('TEXTCOLOR', (0, 0), (-1, 0), colors.white),
            ('ALIGN', (0, 0), (-1, -1), 'CENTER'),
            ('FONTNAME', (0, 0), (-1, 0), 'Helvetica-Bold'),
            ('FONTSIZE', (0, 0), (-1, 0), 8),
            ('FONTSIZE', (0, 1), (-1, -1), 7),
            ('TOPPADDING', (0, 0), (-1, -1), 4),
            ('BOTTOMPADDING', (0, 0), (-1, -1), 4),
            ('ROWBACKGROUNDS', (0, 1), (-1, -1), [colors.HexColor('#f8fafc'), colors.white]),
            ('GRID', (0, 0), (-1, -1), 0.5, colors.HexColor('#e2e8f0')),
            ('BOX', (0, 0), (-1, -1), 1, colors.HexColor(TECPAP_DARK)),
        ]))
        elements.append(orders_table)

    elements.append(Spacer(1, 0.8*cm))

    # INSIGHTS & RECOMMENDATIONS
    elements.append(Paragraph("Insights & Recommandations", section_style))

    insights = []

    # Validation rate insight
    if validation_rate >= 80:
        insights.append(("✅ Excellent taux de validation", f"Le taux de validation de {validation_rate:.1f}% est excellent. Continuez ainsi!", "#16a34a"))
    elif validation_rate >= 60:
        insights.append(("⚠️ Taux de validation moyen", f"Le taux de {validation_rate:.1f}% peut être amélioré. Analysez les rejets.", "#f59e0b"))
    else:
        insights.append(("❌ Taux de validation faible", f"Avec {validation_rate:.1f}%, il faut revoir le processus de qualification.", "#ef4444"))

    # WhatsApp insight
    if whatsapp_rate > 50:
        insights.append(("📱 Canal WhatsApp dominant", f"{whatsapp_rate:.0f}% des commandes viennent de WhatsApp. Optimisez ce canal!", "#25D366"))

    # Pending orders insight
    if stats["pending_orders"] > 5:
        insights.append(("⏰ Commandes en attente", f"{stats['pending_orders']} commandes nécessitent une validation urgente.", "#f59e0b"))

    # Top client concentration
    if stats["top_clients"]:
        top_client_share = ((stats["top_clients"][0]["total_spent"] or 0) / total_ca * 100) if total_ca > 0 else 0
        if top_client_share > 30:
            insights.append(("📊 Concentration client", f"Le top client représente {top_client_share:.0f}% du CA. Diversifiez!", "#0ea5e9"))

    for title, desc, color in insights:
        insight_data = [[Paragraph(f"<b>{title}</b><br/><font size='8'>{desc}</font>",
                                   ParagraphStyle('Insight', fontSize=9, textColor=colors.HexColor(TECPAP_DARK)))]]
        insight_table = Table(insight_data, colWidths=[16*cm])
        insight_table.setStyle(TableStyle([
            ('BACKGROUND', (0, 0), (-1, -1), colors.HexColor('#f8fafc')),
            ('LEFTPADDING', (0, 0), (-1, -1), 10),
            ('TOPPADDING', (0, 0), (-1, -1), 8),
            ('BOTTOMPADDING', (0, 0), (-1, -1), 8),
            ('BOX', (0, 0), (-1, -1), 2, colors.HexColor(color)),
        ]))
        elements.append(insight_table)
        elements.append(Spacer(1, 0.3*cm))

    # SUMMARY BOX
    elements.append(Spacer(1, 0.5*cm))
    summary_text = f"""
    <b>Résumé Exécutif</b><br/><br/>
    • <b>{stats['total_orders']}</b> commandes traitées avec un CA de <b>{stats['total_revenue']:,.0f} MAD</b><br/>
    • Taux de validation: <b>{validation_rate:.1f}%</b> | Panier moyen: <b>{stats['avg_order_value']:,.0f} MAD</b><br/>
    • Canal principal: <b>{'WhatsApp' if whatsapp_count > email_count else 'Email'}</b> ({max(whatsapp_rate, 100-whatsapp_rate):.0f}%)<br/>
    • <b>{stats['total_clients']}</b> clients actifs | Top client: <b>{(stats['top_clients'][0]['nom'] if stats['top_clients'] else 'N/A')[:15]}</b>
    """

    summary_para = Paragraph(summary_text, ParagraphStyle('Summary', fontSize=10, textColor=colors.HexColor(TECPAP_DARK), leading=14))
    summary_table = Table([[summary_para]], colWidths=[16*cm])
    summary_table.setStyle(TableStyle([
        ('BACKGROUND', (0, 0), (-1, -1), colors.HexColor(TECPAP_LIGHT)),
        ('LEFTPADDING', (0, 0), (-1, -1), 15),
        ('RIGHTPADDING', (0, 0), (-1, -1), 15),
        ('TOPPADDING', (0, 0), (-1, -1), 12),
        ('BOTTOMPADDING', (0, 0), (-1, -1), 12),
        ('BOX', (0, 0), (-1, -1), 2, colors.HexColor(TECPAP_GREEN)),
    ]))
    elements.append(summary_table)

    # Build PDF
    doc.build(elements, onFirstPage=add_header_footer, onLaterPages=add_header_footer)
    return filepath


def create_kpi_box(title, value, subtitle, color):
    """Create a styled KPI box."""
    from reportlab.platypus import Paragraph
    from reportlab.lib.styles import ParagraphStyle
    from reportlab.lib.enums import TA_CENTER
    from reportlab.lib import colors

    style = ParagraphStyle('KPI', fontSize=9, alignment=TA_CENTER, leading=12)
    html = f"""
    <font color="{color}" size="18"><b>{value}</b></font><br/>
    <font color="#334155" size="9"><b>{title}</b></font><br/>
    <font color="#64748b" size="7">{subtitle}</font>
    """
    return Paragraph(html, style)


# Test
if __name__ == "__main__":
    from database import DatabaseManager

    db = DatabaseManager()
    db.connect()
    db.init_database()

    print("🚀 Génération du rapport PDF BI...")
    filepath = generate_pdf_report_improved(db)
    print(f"✅ Rapport généré: {filepath}")

    # Get file size
    size = os.path.getsize(filepath)
    print(f"📄 Taille: {size/1024:.1f} Ko")

    db.disconnect()
