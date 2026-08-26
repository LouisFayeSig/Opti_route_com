from __future__ import annotations

import io
from urllib.parse import quote

from openpyxl.styles import Font, PatternFill
from reportlab.lib import colors
from reportlab.lib.pagesizes import A4, landscape
from reportlab.lib.styles import getSampleStyleSheet
from reportlab.lib.units import mm
from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle

from .planner import RoutePlan


def _export_table(plan: RoutePlan):
    return plan.table.drop(columns=["Latitude", "Longitude"], errors="ignore").rename(
        columns={
            "Distance": "Distance depuis le précédent (km)",
            "Temps": "Temps depuis le précédent (min)",
            "Distance cumulée": "Distance cumulée (km)",
            "Temps cumulé": "Temps cumulé (min)",
        }
    )


def csv_bytes(plan: RoutePlan) -> bytes:
    return _export_table(plan).to_csv(index=False, sep=";", decimal=",", lineterminator="\n").encode(
        "utf-8-sig"
    )


def excel_bytes(plan: RoutePlan) -> bytes:
    output = io.BytesIO()
    with __import__("pandas").ExcelWriter(output, engine="openpyxl") as writer:
        _export_table(plan).to_excel(writer, sheet_name="Tournée", index=False)
        summary = __import__("pandas").DataFrame(
            {
                "Indicateur": [
                    "Départ",
                    "Nombre de visites",
                    "Distance totale (km)",
                    "Durée totale (min)",
                    "Retour au départ",
                    "Source des estimations",
                ],
                "Valeur": [
                    plan.start.label,
                    plan.visit_count,
                    round(plan.total_distance_m / 1000, 1),
                    round(plan.total_duration_s / 60),
                    "Oui" if plan.return_to_start else "Non",
                    plan.provider,
                ],
            }
        )
        summary.to_excel(writer, sheet_name="Synthèse", index=False)
        for sheet in writer.book.worksheets:
            for cell in sheet[1]:
                cell.font = Font(bold=True, color="FFFFFF")
                cell.fill = PatternFill("solid", fgColor="1565C0")
            for column in sheet.columns:
                width = min(55, max(len(str(cell.value or "")) for cell in column) + 2)
                sheet.column_dimensions[column[0].column_letter].width = width
    return output.getvalue()


def pdf_bytes(plan: RoutePlan) -> bytes:
    output = io.BytesIO()
    document = SimpleDocTemplate(
        output,
        pagesize=landscape(A4),
        rightMargin=12 * mm,
        leftMargin=12 * mm,
        topMargin=12 * mm,
        bottomMargin=12 * mm,
        title="Tournée commerciale",
    )
    styles = getSampleStyleSheet()
    content = [Paragraph("Tournée commerciale", styles["Title"])]
    content.append(
        Paragraph(
            f"{plan.visit_count} visites — {plan.total_distance_m / 1000:.1f} km — "
            f"{plan.total_duration_s / 60:.0f} min — {plan.provider}",
            styles["Normal"],
        )
    )
    content.append(Spacer(1, 6 * mm))
    export = _export_table(plan)
    columns = ["Ordre", "Client", "Ville", "Distance depuis le précédent (km)", "Temps depuis le précédent (min)"]
    rows = [columns]
    for _, row in export[columns].iterrows():
        rows.append(
            [
                str(row["Ordre"]),
                str(row["Client"]),
                str(row["Ville"]),
                f"{float(row['Distance depuis le précédent (km)']):.1f}",
                f"{float(row['Temps depuis le précédent (min)']):.0f}",
            ]
        )
    table = Table(rows, colWidths=[15 * mm, 75 * mm, 45 * mm, 50 * mm, 45 * mm], repeatRows=1)
    table.setStyle(
        TableStyle(
            [
                ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#1565C0")),
                ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
                ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
                ("GRID", (0, 0), (-1, -1), 0.25, colors.HexColor("#DDE3EA")),
                ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, colors.HexColor("#F5F7FA")]),
                ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
                ("FONTSIZE", (0, 0), (-1, -1), 8),
                ("TOPPADDING", (0, 0), (-1, -1), 5),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 5),
            ]
        )
    )
    content.append(table)
    document.build(content)
    return output.getvalue()


def google_maps_url(plan: RoutePlan) -> str:
    points = plan.route_coordinates
    origin = f"{points[0][0]:.6f},{points[0][1]:.6f}"
    destination_point = points[-1]
    destination = f"{destination_point[0]:.6f},{destination_point[1]:.6f}"
    waypoint_points = points[1:-1]
    waypoints = "|".join(f"{latitude:.6f},{longitude:.6f}" for latitude, longitude in waypoint_points)
    url = (
        "https://www.google.com/maps/dir/?api=1"
        f"&origin={quote(origin)}&destination={quote(destination)}&travelmode=driving"
    )
    if waypoints:
        url += f"&waypoints={quote(waypoints)}"
    return url
