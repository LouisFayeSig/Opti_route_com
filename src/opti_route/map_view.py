from __future__ import annotations

import json
import math

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
            "color": "#1565C0",
            "rgb": [21, 101, 192],
            "radius": 520,
        }
    ]
    for _, row in plan.table.iterrows():
        is_last = (
            int(row["Ordre"]) == plan.visit_count and not plan.return_to_start and plan.end is None
        )
        points.append(
            {
                "latitude": float(row["Latitude"]),
                "longitude": float(row["Longitude"]),
                "label": str(row["Client"]),
                "map_label": f"{int(row['Ordre'])}. {row['Client']}",
                "order": str(int(row["Ordre"])),
                "color": "#2E7D32" if is_last else "#D32F2F",
                "rgb": [46, 125, 50] if is_last else [211, 47, 47],
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
                "color": "#2E7D32",
                "rgb": [46, 125, 50],
                "radius": 520,
            }
        )
    return points


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
        get_size=15,
        get_pixel_offset=[0, -22],
        get_alignment_baseline=String("bottom"),
        get_text_anchor=String("middle"),
        billboard=True,
        background=True,
        get_background_color=[255, 255, 255, 225],
        background_padding=[5, 3],
        background_border_radius=4,
        font_family=String("Arial, sans-serif"),
        font_weight=600,
        character_set=String("auto"),
        pickable=False,
    )


def render_pydeck_map(plan: RoutePlan, height: int = 560) -> None:
    points = _map_points(plan)
    arrows = _direction_arrows(plan.geometry)
    path = [[longitude, latitude] for latitude, longitude in plan.geometry]
    layers = [
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
    latitude_span = max(point["latitude"] for point in points) - min(
        point["latitude"] for point in points
    )
    longitude_span = max(point["longitude"] for point in points) - min(
        point["longitude"] for point in points
    )
    largest_span = max(float(latitude_span), float(longitude_span), 0.01)
    zoom = max(3.5, min(14.0, math.log2(360 / largest_span) - 1.6))
    view = pdk.ViewState(
        latitude=sum(point["latitude"] for point in points) / len(points),
        longitude=sum(point["longitude"] for point in points) / len(points),
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


def render_azure_map(plan: RoutePlan, subscription_key: str, height: int = 560) -> None:
    points = _map_points(plan)
    payload = {
        "points": points,
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
          filter:['==',['geometry-type'],'Point'],
          textOptions: {{textField:['get','order'], color:'#FFFFFF', size:12, font:['StandardFont-Bold']}}
        }}));
        map.layers.add(new atlas.layer.SymbolLayer(source, 'company-labels', {{
          filter:['==',['geometry-type'],'Point'],
          iconOptions: {{image: 'none'}},
          textOptions: {{
            textField:['get','map_label'], color:'#1F2937', size:14,
            font:['StandardFont-Bold'], offset:[0,-1.7],
            haloColor:'#FFFFFF', haloWidth:2,
            allowOverlap:true, ignorePlacement:true
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
          ...data.points.map(p => [p.longitude,p.latitude]), ...data.path
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
) -> None:
    if renderer == "azure" and subscription_key:
        render_azure_map(plan, subscription_key, height=height)
    else:
        render_pydeck_map(plan, height=height)
