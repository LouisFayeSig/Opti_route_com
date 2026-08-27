from __future__ import annotations

import io
from urllib.parse import quote

import pandas as pd
from openpyxl.styles import Font, PatternFill
from PIL import Image as PillowImage
from PIL import ImageDraw
from reportlab.lib import colors
from reportlab.lib.pagesizes import A4, landscape
from reportlab.lib.styles import getSampleStyleSheet
from reportlab.lib.units import mm
from reportlab.platypus import Image, Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle

from .planner import RoutePlan

_FORMULA_PREFIXES = ("=", "+", "-", "@", "\t", "\r", "\n", "\x00", "＝", "＋", "－", "＠")


def _export_table(plan: RoutePlan):
    return plan.itinerary_table().rename(
        columns={
            "Distance": "Distance depuis le précédent (km)",
            "Temps": "Temps depuis le précédent (min)",
            "Distance cumulée": "Distance cumulée (km)",
            "Temps cumulé": "Temps cumulé (min)",
        }
    )


def sanitize_spreadsheet_value(value):
    """Force les chaînes pouvant être interprétées comme des formules à rester du texte."""
    if not isinstance(value, str):
        return value
    candidate = value.lstrip(" ")
    if candidate.startswith(_FORMULA_PREFIXES):
        return "'" + value
    return value


def _sanitize_spreadsheet_frame(frame: pd.DataFrame) -> pd.DataFrame:
    sanitized = frame.copy()
    for column in sanitized.columns:
        sanitized[column] = sanitized[column].map(sanitize_spreadsheet_value)
    return sanitized


def csv_bytes(plan: RoutePlan) -> bytes:
    return _sanitize_spreadsheet_frame(_export_table(plan)).to_csv(
        index=False,
        sep=";",
        decimal=",",
        lineterminator="\n",
    ).encode("utf-8-sig")


def excel_bytes(plan: RoutePlan) -> bytes:
    output = io.BytesIO()
    with pd.ExcelWriter(output, engine="openpyxl") as writer:
        _sanitize_spreadsheet_frame(_export_table(plan)).to_excel(
            writer, sheet_name="Tournée", index=False
        )
        summary = pd.DataFrame(
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
        _sanitize_spreadsheet_frame(summary).to_excel(writer, sheet_name="Synthèse", index=False)
        for sheet in writer.book.worksheets:
            for cell in sheet[1]:
                cell.font = Font(bold=True, color="FFFFFF")
                cell.fill = PatternFill("solid", fgColor="1565C0")
            for column in sheet.columns:
                width = min(55, max(len(str(cell.value or "")) for cell in column) + 2)
                sheet.column_dimensions[column[0].column_letter].width = width
    return output.getvalue()


def _fallback_route_image(plan: RoutePlan, width: int = 1200, height: int = 600) -> bytes:
    image = PillowImage.new("RGB", (width, height), "#F4F7FA")
    draw = ImageDraw.Draw(image)
    for x in range(0, width, 100):
        draw.line((x, 0, x, height), fill="#E5EAF0", width=1)
    for y in range(0, height, 100):
        draw.line((0, y, width, y), fill="#E5EAF0", width=1)

    all_points = plan.geometry or plan.route_coordinates
    latitudes = [latitude for latitude, _ in all_points]
    longitudes = [longitude for _, longitude in all_points]
    min_latitude, max_latitude = min(latitudes), max(latitudes)
    min_longitude, max_longitude = min(longitudes), max(longitudes)
    latitude_span = max(max_latitude - min_latitude, 0.001)
    longitude_span = max(max_longitude - min_longitude, 0.001)
    padding = 55

    def pixel(point: tuple[float, float]) -> tuple[int, int]:
        latitude, longitude = point
        x = padding + (longitude - min_longitude) / longitude_span * (width - 2 * padding)
        y = height - padding - (latitude - min_latitude) / latitude_span * (height - 2 * padding)
        return round(x), round(y)

    route_pixels = [pixel(point) for point in all_points]
    if len(route_pixels) > 1:
        draw.line(route_pixels, fill="#1565C0", width=7, joint="curve")

    stop_points = plan.route_coordinates[:-1] if plan.return_to_start else plan.route_coordinates
    for index, point in enumerate(stop_points):
        x, y = pixel(point)
        is_start = index == 0
        is_arrival = not plan.return_to_start and index == len(stop_points) - 1
        color = "#1565C0" if is_start else "#2E7D32" if is_arrival else "#D32F2F"
        radius = 16 if is_start else 14
        draw.ellipse((x - radius, y - radius, x + radius, y + radius), fill=color, outline="white", width=3)
        label = "D" if is_start else str(index)
        box = draw.textbbox((0, 0), label)
        text_width, text_height = box[2] - box[0], box[3] - box[1]
        draw.text((x - text_width / 2, y - text_height / 2 - 1), label, fill="white")

    draw.text((padding, 18), f"Tournée · {plan.visit_count} visites", fill="#263238")
    draw.text(
        (padding, height - 30),
        "Bleu : départ   Rouge : visite   Vert : arrivée   —   Schéma sans fond cartographique",
        fill="#607080",
    )
    output = io.BytesIO()
    image.save(output, format="PNG")
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
    map_bytes = plan.map_image or _fallback_route_image(plan)
    map_flowable = Image(io.BytesIO(map_bytes), width=250 * mm, height=135 * mm)
    content.append(map_flowable)
    content.append(Spacer(1, 5 * mm))
    content.append(
        Paragraph(
            "<font color='#1565C0'>●</font> Départ &nbsp;&nbsp; "
            "<font color='#D32F2F'>●</font> Visite &nbsp;&nbsp; "
            "<font color='#2E7D32'>●</font> Arrivée",
            styles["Normal"],
        )
    )
    content.append(Spacer(1, 8 * mm))
    export = _export_table(plan)
    columns = ["Étape", "Client", "Ville", "Distance depuis le précédent (km)", "Temps depuis le précédent (min)"]
    rows = [columns]
    for _, row in export[columns].iterrows():
        rows.append(
            [
                str(row["Étape"]),
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
