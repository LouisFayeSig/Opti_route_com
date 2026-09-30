from __future__ import annotations

from collections.abc import Sequence

from PIL import Image, ImageDraw, ImageFont


def _label_font(size: int = 16) -> ImageFont.FreeTypeFont | ImageFont.ImageFont:
    for candidate in (
        "DejaVuSans-Bold.ttf",
        "Arial Bold.ttf",
        "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
    ):
        try:
            return ImageFont.truetype(candidate, size)
        except OSError:
            continue
    return ImageFont.load_default()


def _fit_text(
    draw: ImageDraw.ImageDraw,
    text: str,
    font: ImageFont.FreeTypeFont | ImageFont.ImageFont,
    max_width: int,
) -> str:
    if draw.textlength(text, font=font) <= max_width:
        return text
    suffix = "…"
    shortened = text
    while shortened and draw.textlength(shortened + suffix, font=font) > max_width:
        shortened = shortened[:-1]
    return shortened.rstrip() + suffix


def _overlap_area(first: tuple[int, int, int, int], second: tuple[int, int, int, int]) -> int:
    width = max(0, min(first[2], second[2]) - max(first[0], second[0]))
    height = max(0, min(first[3], second[3]) - max(first[1], second[1]))
    return width * height


def draw_stop_labels(
    image: Image.Image,
    pixel_points: Sequence[tuple[int, int]],
    labels: Sequence[str],
    colors: Sequence[str],
) -> None:
    """Dessine des repères numérotés et place les noms en limitant les chevauchements."""
    draw = ImageDraw.Draw(image)
    font = _label_font()
    marker_font = _label_font(14)
    marker_radius = 14
    occupied: list[tuple[int, int, int, int]] = [
        (x - marker_radius - 3, y - marker_radius - 3, x + marker_radius + 3, y + marker_radius + 3)
        for x, y in pixel_points
    ]

    for (x, y), label, color in zip(pixel_points, labels, colors, strict=False):
        draw.ellipse(
            (x - marker_radius, y - marker_radius, x + marker_radius, y + marker_radius),
            fill=color,
            outline="white",
            width=3,
        )
        marker = label.partition(" · ")[0]
        marker_box = draw.textbbox((0, 0), marker, font=marker_font)
        marker_width = marker_box[2] - marker_box[0]
        marker_height = marker_box[3] - marker_box[1]
        draw.text(
            (x - marker_width / 2, y - marker_height / 2 - 1),
            marker,
            fill="white",
            font=marker_font,
        )

    for index, ((x, y), raw_label) in enumerate(zip(pixel_points, labels, strict=False)):
        label = _fit_text(draw, str(raw_label), font, max_width=320)
        text_box = draw.textbbox((0, 0), label, font=font)
        label_width = text_box[2] - text_box[0] + 14
        label_height = text_box[3] - text_box[1] + 10
        vertical_shifts = [0, 28, -28, 56, -56, 84, -84]
        candidates: list[tuple[int, int, int, int]] = []
        for shift in vertical_shifts:
            if index % 2:
                raw_candidates = [
                    (x - label_width - 19, y - label_height - 8 + shift),
                    (x + 19, y + 8 + shift),
                    (x + 19, y - label_height - 8 + shift),
                    (x - label_width - 19, y + 8 + shift),
                ]
            else:
                raw_candidates = [
                    (x + 19, y - label_height - 8 + shift),
                    (x - label_width - 19, y + 8 + shift),
                    (x - label_width - 19, y - label_height - 8 + shift),
                    (x + 19, y + 8 + shift),
                ]
            for left, top in raw_candidates:
                left = max(4, min(image.width - label_width - 4, left))
                top = max(4, min(image.height - label_height - 4, top))
                candidates.append((left, top, left + label_width, top + label_height))

        box = min(
            candidates,
            key=lambda candidate: sum(_overlap_area(candidate, item) for item in occupied),
        )
        occupied.append(box)
        nearest_x = min(max(x, box[0]), box[2])
        nearest_y = min(max(y, box[1]), box[3])
        draw.line((x, y, nearest_x, nearest_y), fill="#667085", width=2)
        draw.rounded_rectangle(box, radius=5, fill="white", outline="#CBD5E1", width=1)
        draw.text(
            (box[0] + 7, box[1] + 5),
            label,
            fill="#1F2937",
            font=font,
        )
