from __future__ import annotations

import io
from urllib.parse import quote
from xml.sax.saxutils import escape

import pandas as pd
from openpyxl.styles import Font, PatternFill
from PIL import Image as PillowImage
from PIL import ImageDraw
from reportlab.lib import colors
from reportlab.lib.pagesizes import A4, landscape
from reportlab.lib.styles import getSampleStyleSheet
from reportlab.lib.units import mm
from reportlab.platypus import Image, Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle

from .image_labels import draw_stop_labels
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
    return (
        _sanitize_spreadsheet_frame(_export_table(plan))
        .to_csv(
            index=False,
            sep=";",
            decimal=",",
            lineterminator="\n",
        )
        .encode("utf-8-sig")
    )


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
                    "Fin de tournée",
                    "Nombre de visites",
                    "Distance totale (km)",
                    "Durée totale (min)",
                    "Source des estimations",
                ],
                "Valeur": [
                    plan.start.label,
                    plan.end.label
                    if plan.end is not None
                    else "Dernière entreprise visitée",
                    plan.visit_count,
                    round(plan.total_distance_m / 1000, 1),
                    round(plan.total_duration_s / 60),
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
    padding = 95

    def pixel(point: tuple[float, float]) -> tuple[int, int]:
        latitude, longitude = point
        x = padding + (longitude - min_longitude) / longitude_span * (width - 2 * padding)
        y = height - padding - (latitude - min_latitude) / latitude_span * (height - 2 * padding)
        return round(x), round(y)

    route_pixels = [pixel(point) for point in all_points]
    if len(route_pixels) > 1:
        draw.line(route_pixels, fill="#1565C0", width=7, joint="curve")

    stop_points = plan.route_coordinates[:-1] if plan.return_to_start else plan.route_coordinates
    stop_pixels = [pixel(point) for point in stop_points]
    colors_by_stop = ["#1565C0"]
    colors_by_stop.extend(
        "#2E7D32" if not plan.return_to_start and index == len(stop_points) - 1 else "#D32F2F"
        for index in range(1, len(stop_points))
    )
    draw_stop_labels(image, stop_pixels, plan.map_stop_labels, colors_by_stop)

    draw.text((padding, 18), f"Tournée · {plan.visit_count} visites", fill="#263238")
    draw.text(
        (padding, height - 30),
        "Bleu : départ   Rouge : visite   Vert : dernière visite   —   Schéma sans fond cartographique",
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
            f"{plan.total_duration_s / 60:.0f} min — {plan.provider}. "
            "La tournée se termine après la dernière visite.",
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
            "<font color='#2E7D32'>●</font> Dernière visite",
            styles["Normal"],
        )
    )
    content.append(Spacer(1, 8 * mm))
    export = plan.review_table()
    columns = [
        "Ordre",
        "Entreprise",
        "Ville",
        "Adresse",
        "Distance",
        "Temps",
        "Distance cumulée",
        "Temps cumulé",
        "Rendez-vous planifié",
    ]
    labels = [
        "Ordre",
        "Entreprise",
        "Ville",
        "Adresse",
        "Distance",
        "Temps",
        "Cumul (km)",
        "Cumul temps",
        "Rendez-vous",
    ]
    header_style = styles["BodyText"].clone("PdfTableHeader")
    header_style.fontName = "Helvetica-Bold"
    header_style.fontSize = 7
    header_style.leading = 8.5
    header_style.textColor = colors.white
    rows = [[Paragraph(escape(label), header_style) for label in labels]]
    cell_style = styles["BodyText"].clone("PdfTableCell")
    cell_style.fontName = "Helvetica"
    cell_style.fontSize = 7
    cell_style.leading = 8.5
    for _, row in export[columns].iterrows():
        rows.append(
            [
                str(int(row["Ordre"])),
                Paragraph(escape(str(row["Entreprise"])), cell_style),
                Paragraph(escape(str(row["Ville"])), cell_style),
                Paragraph(escape(str(row["Adresse"])), cell_style),
                f"{float(row['Distance']):.1f} km",
                f"{float(row['Temps']):.0f} min",
                f"{float(row['Distance cumulée']):.1f}",
                f"{float(row['Temps cumulé']):.0f} min",
                "Oui" if bool(row["Rendez-vous planifié"]) else "—",
            ]
        )
    table = Table(
        rows,
        colWidths=[12 * mm, 43 * mm, 27 * mm, 67 * mm, 22 * mm, 20 * mm, 23 * mm, 24 * mm, 26 * mm],
        repeatRows=1,
    )
    table.setStyle(
        TableStyle(
            [
                ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#1565C0")),
                ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
                ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
                ("GRID", (0, 0), (-1, -1), 0.25, colors.HexColor("#DDE3EA")),
                ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, colors.HexColor("#F5F7FA")]),
                ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
                ("FONTSIZE", (0, 0), (-1, -1), 7),
                ("TOPPADDING", (0, 0), (-1, -1), 4),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
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
    waypoints = "|".join(
        f"{latitude:.6f},{longitude:.6f}" for latitude, longitude in waypoint_points
    )
    url = (
        "https://www.google.com/maps/dir/?api=1"
        f"&origin={quote(origin)}&destination={quote(destination)}&travelmode=driving"
    )
    if waypoints:
        url += f"&waypoints={quote(waypoints)}"
    return url
