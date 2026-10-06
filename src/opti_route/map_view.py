from __future__ import annotations

import json
import math

import numpy as np
import pandas as pd
import pydeck as pdk
import streamlit as st
import streamlit.components.v1 as components
from pydeck.types import String

from .planner import RoutePlan


def _map_points(plan: RoutePlan) -> list[dict[str, object]]:
    points: list[dict[str, object]] = [
        {
            "latitude": plan.start.latitude,
            "longitude": plan.start.longitude,
            "label": plan.start.label,
            "map_label": "Départ",
            "order": "D",
            "kind": "route",
            "color": "#1565C0",
            "rgb": [21, 101, 192],
            "radius": 520,
        }
    ]
    for _, row in plan.table.iterrows():
        points.append(
            {
                "latitude": float(row["Latitude"]),
                "longitude": float(row["Longitude"]),
                "label": str(row["Client"]),
                "map_label": f"{int(row['Ordre'])}. {row['Client']}",
                "order": str(int(row["Ordre"])),
                "kind": "route",
                "color": "#D32F2F",
                "rgb": [211, 47, 47],
                "radius": 350,
            }
        )
    if plan.end is not None:
        points.append(
            {
                "latitude": plan.end.latitude,
                "longitude": plan.end.longitude,
                "label": plan.end.label,
                "map_label": "Arrivée",
                "order": "A",
                "kind": "route",
                "color": "#2E7D32",
                "rgb": [46, 125, 50],
                "radius": 520,
            }
        )
    placements = [
        ([0, -20], "middle", "bottom"),
        ([18, -14], "start", "bottom"),
        ([-18, -14], "end", "bottom"),
        ([18, 14], "start", "top"),
        ([-18, 14], "end", "top"),
    ]
    for index, point in enumerate(points):
        offset, anchor, baseline = placements[index % len(placements)]
        point["label_offset"] = offset
        point["label_anchor"] = anchor
        point["label_baseline"] = baseline
    return points


def possibility_points_from_candidates(
    plan: RoutePlan,
    candidates: pd.DataFrame,
    start_client_id: str | None,
) -> tuple[list[dict[str, object]], int, int, float]:
    """Classe les entreprises hors tournée selon leur distance directe au départ."""
    if candidates.empty:
        return [], 0, 0, 0.0

    route_ids = set(plan.table["Code client"].astype(str))
    excluded_ids = route_ids | ({str(start_client_id)} if start_client_id else set())
    points = candidates.copy()
    points["latitude"] = pd.to_numeric(points["latitude"], errors="coerce")
    points["longitude"] = pd.to_numeric(points["longitude"], errors="coerce")
    points = points[
        points["latitude"].notna()
        & points["longitude"].notna()
        & ~points["client_id"].astype(str).isin(excluded_ids)
    ]
    if points.empty:
        return [], 0, 0, 0.0

    route_latitudes = plan.table["Latitude"].astype(float).to_numpy()
    route_longitudes = plan.table["Longitude"].astype(float).to_numpy()
    start_latitude = np.radians(plan.start.latitude)
    start_longitude = np.radians(plan.start.longitude)
    route_distances = 2 * 6371.0088 * np.arcsin(
        np.sqrt(
            np.sin((np.radians(route_latitudes) - start_latitude) / 2) ** 2
            + np.cos(start_latitude)
            * np.cos(np.radians(route_latitudes))
            * np.sin((np.radians(route_longitudes) - start_longitude) / 2) ** 2
        )
    )
    furthest_selected_km = float(route_distances.max())

    latitudes = points["latitude"].astype(float).to_numpy()
    longitudes = points["longitude"].astype(float).to_numpy()
    distances = 2 * 6371.0088 * np.arcsin(
        np.sqrt(
            np.sin((np.radians(latitudes) - start_latitude) / 2) ** 2
            + np.cos(start_latitude)
            * np.cos(np.radians(latitudes))
            * np.sin((np.radians(longitudes) - start_longitude) / 2) ** 2
        )
    )
    within_selected_range = distances <= furthest_selected_km + 1e-9
    overlay: list[dict[str, object]] = []
    details = points[["client_name", "latitude", "longitude"]].itertuples(index=False)
    for detail, distance, is_nearby in zip(
        details, distances, within_selected_range, strict=True
    ):
        company, latitude, longitude = detail
        overlay.append(
            {
                "latitude": float(latitude),
                "longitude": float(longitude),
                "label": str(company),
                "order": "Possibilité" if is_nearby else "Plus loin",
                "kind": "possibility",
                "color": "#FBBF24" if is_nearby else "#22C55E",
                "rgb": [251, 191, 36] if is_nearby else [34, 197, 94],
                "radius": 260,
                "distance_from_start_km": round(float(distance), 1),
            }
        )
    nearby_count = int(within_selected_range.sum())
    return overlay, nearby_count, len(overlay) - nearby_count, furthest_selected_km


def _possibility_layer(points: list[dict[str, object]]) -> pdk.Layer:
    return pdk.Layer(
        "ScatterplotLayer",
        id="possibility-overlay",
        data=points,
        get_position="[longitude, latitude]",
        get_fill_color="rgb",
        get_radius="radius",
        radius_min_pixels=5,
        radius_max_pixels=11,
        pickable=True,
        stroked=True,
        get_line_color=[255, 255, 255],
        line_width_min_pixels=1,
    )


def _direction_arrows(geometry: list[tuple[float, float]]) -> list[dict[str, object]]:
    if len(geometry) < 2:
        return []
    arrow_count = min(6, max(1, len(geometry) // 8))
    arrows: list[dict[str, object]] = []
    used_indices: set[int] = set()
    for position in range(1, arrow_count + 1):
        index = min(
            len(geometry) - 2,
            round((len(geometry) - 2) * position / (arrow_count + 1)),
        )
        if index in used_indices:
            continue
        used_indices.add(index)
        latitude, longitude = geometry[index]
        next_latitude, next_longitude = geometry[index + 1]
        horizontal = (next_longitude - longitude) * math.cos(math.radians(latitude))
        vertical = -(next_latitude - latitude)
        arrows.append(
            {
                "latitude": latitude,
                "longitude": longitude,
                "angle": math.degrees(math.atan2(vertical, horizontal)),
                "arrow": "➤",
            }
        )
    return arrows


def _persistent_label_layer(points: list[dict[str, object]]) -> pdk.Layer:
    return pdk.Layer(
        "TextLayer",
        id="persistent-company-labels",
        data=points,
        get_position="[longitude, latitude]",
        get_text="map_label",
        get_color=[31, 41, 55, 255],
        get_size=11,
        get_pixel_offset="label_offset",
        get_alignment_baseline="label_baseline",
        get_text_anchor="label_anchor",
        billboard=True,
        background=True,
        get_background_color=[255, 255, 255, 225],
        background_padding=[3, 2],
        background_border_radius=4,
        font_family=String("Arial, sans-serif"),
        font_weight=600,
        character_set=String("auto"),
        pickable=False,
    )


def render_pydeck_map(
    plan: RoutePlan,
    height: int = 560,
    possibility_points: list[dict[str, object]] | None = None,
) -> None:
    points = _map_points(plan)
    possibilities = possibility_points or []
    arrows = _direction_arrows(plan.geometry)
    path = [[longitude, latitude] for latitude, longitude in plan.geometry]
    layers = [
        *([_possibility_layer(possibilities)] if possibilities else []),
        pdk.Layer(
            "PathLayer",
            data=[{"path": path}],
            get_path="path",
            get_color=[21, 101, 192],
            width_min_pixels=4,
        ),
        pdk.Layer(
            "ScatterplotLayer",
            data=points,
            get_position="[longitude, latitude]",
            get_fill_color="rgb",
            get_radius="radius",
            radius_min_pixels=8,
            radius_max_pixels=16,
            pickable=True,
            stroked=True,
            get_line_color=[255, 255, 255],
            line_width_min_pixels=2,
        ),
        pdk.Layer(
            "TextLayer",
            data=points,
            get_position="[longitude, latitude]",
            get_text="order",
            get_color=[255, 255, 255],
            get_size=12,
            get_alignment_baseline=String("center"),
            get_text_anchor=String("middle"),
        ),
        _persistent_label_layer(points),
        pdk.Layer(
            "TextLayer",
            data=arrows,
            get_position="[longitude, latitude]",
            get_text="arrow",
            get_color=[21, 101, 192],
            get_size=22,
            get_angle="angle",
            get_alignment_baseline=String("center"),
            get_text_anchor=String("middle"),
        ),
    ]
    all_points = [*points, *possibilities]
    latitude_span = max(point["latitude"] for point in all_points) - min(
        point["latitude"] for point in all_points
    )
    longitude_span = max(point["longitude"] for point in all_points) - min(
        point["longitude"] for point in all_points
    )
    largest_span = max(float(latitude_span), float(longitude_span), 0.01)
    zoom = max(3.5, min(14.0, math.log2(360 / largest_span) - 1.6))
    view = pdk.ViewState(
        latitude=sum(point["latitude"] for point in all_points) / len(all_points),
        longitude=sum(point["longitude"] for point in all_points) / len(all_points),
        zoom=zoom,
    )
    st.pydeck_chart(
        pdk.Deck(
            layers=layers,
            initial_view_state=view,
            tooltip={"text": "{order} — {label}"},
            map_style=pdk.map_styles.LIGHT,
        ),
        height=height,
        use_container_width=True,
    )


def render_azure_map(
    plan: RoutePlan,
    subscription_key: str,
    height: int = 560,
    possibility_points: list[dict[str, object]] | None = None,
) -> None:
    points = _map_points(plan)
    payload = {
        "points": points,
        "possibilityPoints": possibility_points or [],
        "path": [[longitude, latitude] for latitude, longitude in plan.geometry],
        "arrows": _direction_arrows(plan.geometry),
    }
    # Empêche une valeur issue du fichier importé de fermer la balise script.
    payload_json = json.dumps(payload, ensure_ascii=False).replace("</", "<\\/")
    key_json = json.dumps(subscription_key)
    document = f"""
    <!doctype html>
    <html lang="fr">
    <head>
      <meta charset="utf-8">
      <link rel="stylesheet" href="https://atlas.microsoft.com/sdk/javascript/mapcontrol/3/atlas.min.css" />
      <script src="https://atlas.microsoft.com/sdk/javascript/mapcontrol/3/atlas.min.js"></script>
      <style>html,body,#map{{margin:0;width:100%;height:100%;font-family:Arial,sans-serif}}</style>
    </head>
    <body><div id="map"></div>
    <script>
      const data = {payload_json};
      const map = new atlas.Map('map', {{
        authOptions: {{authType: 'subscriptionKey', subscriptionKey: {key_json}}},
        style: 'road', language: 'fr-FR', view: 'Auto'
      }});
      map.events.add('ready', () => {{
        const source = new atlas.source.DataSource();
        map.sources.add(source);
        if (data.path.length > 1) {{
          source.add(new atlas.data.Feature(new atlas.data.LineString(data.path), {{kind:'route'}}));
        }}
        data.points.forEach(p => source.add(new atlas.data.Feature(
          new atlas.data.Point([p.longitude, p.latitude]), p
        )));
        data.possibilityPoints.forEach(p => source.add(new atlas.data.Feature(
          new atlas.data.Point([p.longitude, p.latitude]), p
        )));
        data.arrows.forEach(a => map.markers.add(new atlas.HtmlMarker({{
          position: [a.longitude, a.latitude],
          htmlContent: `<div style="transform:rotate(${{a.angle}}deg);color:#1565C0;` +
            `font-size:21px;font-weight:bold;text-shadow:0 0 3px white">➤</div>`,
          anchor: 'center'
        }})));
        map.layers.add(new atlas.layer.LineLayer(source, null, {{
          filter:['==',['geometry-type'],'LineString'], strokeColor:'#1565C0', strokeWidth:5
        }}));
        map.layers.add(new atlas.layer.BubbleLayer(source, 'stops', {{
          filter:['==',['geometry-type'],'Point'], color:['get','color'], radius:13,
          strokeColor:'#FFFFFF', strokeWidth:2
        }}));
        map.layers.add(new atlas.layer.SymbolLayer(source, 'labels', {{
          filter:['all',['==',['geometry-type'],'Point'],['==',['get','kind'],'route']],
          textOptions: {{textField:['get','order'], color:'#FFFFFF', size:12, font:['StandardFont-Bold']}}
        }}));
        map.layers.add(new atlas.layer.SymbolLayer(source, 'company-labels', {{
          filter:['all',['==',['geometry-type'],'Point'],['==',['get','kind'],'route']],
          iconOptions: {{image: 'none'}},
          textOptions: {{
            textField:['get','map_label'], color:'#1F2937', size:11,
            font:['StandardFont-Bold'], offset:[0,-1.7],
            haloColor:'#FFFFFF', haloWidth:2,
            allowOverlap:false, ignorePlacement:false, optional:true
          }}
        }}));
        const popup = new atlas.Popup({{pixelOffset:[0,-18]}});
        map.events.add('click', 'stops', event => {{
          if (!event.shapes || !event.shapes.length) return;
          const props = event.shapes[0].getProperties();
          const content = document.createElement('div');
          content.style.padding = '10px';
          content.style.fontWeight = '600';
          content.textContent = `${{props.order}} — ${{props.label}}`;
          popup.setOptions({{
            position:event.shapes[0].getCoordinates(),
            content
          }}).open(map);
        }});
        const bounds = atlas.data.BoundingBox.fromPositions([
          ...data.points.map(p => [p.longitude,p.latitude]),
          ...data.possibilityPoints.map(p => [p.longitude,p.latitude]),
          ...data.path
        ]);
        map.setCamera({{bounds, padding:55, maxZoom:14}});
      }});
    </script></body></html>
    """
    components.html(document, height=height, scrolling=False)


def render_map(
    plan: RoutePlan,
    subscription_key: str | None,
    height: int = 560,
    renderer: str = "pydeck",
    possibility_points: list[dict[str, object]] | None = None,
) -> None:
    if renderer == "azure" and subscription_key:
        render_azure_map(
            plan,
            subscription_key,
            height=height,
            possibility_points=possibility_points,
        )
    else:
        render_pydeck_map(plan, height=height, possibility_points=possibility_points)
