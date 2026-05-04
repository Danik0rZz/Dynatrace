# -*- coding: utf-8 -*-
import argparse
import json
import os
import sys
import time
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

import pandas as pd
import requests
import urllib3
from dotenv import load_dotenv

# =========================
# Desactiva alertas de certificados
# =========================
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)


SCHEMA_ID = "builtin:anomaly-detection.metric-events"
FIELDS = "objectId,value,created,modified,createdBy,modifiedBy,author"
DEFAULT_OUTPUT = "dynatrace_custom_metrics_report.xlsx"

QUERY_TYPE_LABELS = {
    "METRIC_KEY": "Clave de metrica",
    "METRIC_SELECTOR": "Selector de metricas",
}

MODEL_TYPE_LABELS = {
    "STATIC_THRESHOLD": "Umbral estatico",
    "AUTO_ADAPTIVE_THRESHOLD": "Umbral adaptativo automatico",
    "SEASONAL_BASELINE": "Linea base estacional",
}

ALERT_CONDITION_LABELS = {
    "ABOVE": "Por encima",
    "BELOW": "Por debajo",
}

EVENT_TYPE_LABELS = {
    "AVAILABILITY": "Disponibilidad",
    "CUSTOM_ALERT": "Alerta custom",
    "ERROR": "Error",
    "RESOURCE": "Recurso",
    "SLOWDOWN": "Lentitud",
}


# =========================
# 1. Carga de configuracion
# =========================

def load_environment_files() -> None:
    """
    Carga variables desde .env si existe en el directorio actual, junto al script
    o en la carpeta Problemas.
    """
    script_dir = Path(__file__).resolve().parent
    env_candidates = [
        Path.cwd() / ".env",
        script_dir / ".env",
        script_dir / "Problemas" / ".env",
    ]

    for env_path in env_candidates:
        if env_path.exists():
            load_dotenv(env_path, override=False)


def get_dynatrace_config() -> Dict[str, str]:
    """
    Recupera dynatrace_url y dynatrace_token desde variables de entorno.
    """
    dynatrace_url = os.getenv("DYNATRACE_URL")
    dynatrace_token = os.getenv("DYNATRACE_TOKEN")

    if not dynatrace_url or not dynatrace_token:
        print("[ERROR] Faltan DYNATRACE_URL o DYNATRACE_TOKEN en el entorno.")
        sys.exit(1)

    return {
        "url": dynatrace_url.rstrip("/"),
        "token": dynatrace_token,
    }


# ==========================================
# 2. Extraccion desde Dynatrace Settings API
# ==========================================

def fetch_custom_metrics(
    base_url: str,
    token: str,
    page_size: int = 500,
    admin_access: bool = False,
) -> List[Dict[str, Any]]:
    """
    Llama al endpoint /api/v2/settings/objects para obtener metric events
    custom con paginacion automatica por nextPageKey.
    """
    headers = {
        "Authorization": f"Api-Token {token}",
        "Content-Type": "application/json",
    }

    endpoint = f"{base_url}/api/v2/settings/objects"
    params: Dict[str, Any] = {
        "schemaIds": SCHEMA_ID,
        "fields": FIELDS,
        "pageSize": page_size,
        "adminAccess": str(admin_access).lower(),
    }

    items: List[Dict[str, Any]] = []
    next_page_key: Optional[str] = None

    while True:
        if next_page_key:
            params = {"nextPageKey": next_page_key}

        response = requests.get(endpoint, headers=headers, params=params, timeout=60, verify=False)

        if not response.ok:
            print(f"[ERROR] Llamada a Dynatrace fallo: {response.status_code} {response.text}")
            sys.exit(1)

        data = response.json()
        batch = data.get("items", [])
        items.extend(batch)

        next_page_key = data.get("nextPageKey")
        if not next_page_key:
            break

        time.sleep(0.2)

    print(f"Total de metricas custom recuperadas: {len(items)}")
    return items


def load_items_from_json(filename: str) -> List[Dict[str, Any]]:
    """
    Permite probar el informe con una respuesta JSON local de Dynatrace.
    Acepta tanto {"items": [...]} como una lista directa de items.
    """
    with open(filename, "r", encoding="utf-8") as fh:
        data = json.load(fh)

    if isinstance(data, dict):
        items = data.get("items", [])
    elif isinstance(data, list):
        items = data
    else:
        items = []

    print(f"Total de metricas custom leidas desde JSON: {len(items)}")
    return items


# ==================================================
# 3. Transformacion y resumen JSON
# ==================================================

def as_dict(value: Any) -> Dict[str, Any]:
    return value if isinstance(value, dict) else {}


def as_list(value: Any) -> List[Any]:
    return value if isinstance(value, list) else []


def translate_value(value: Any, labels: Dict[str, str]) -> Any:
    if value is None or value == "":
        return ""
    return labels.get(str(value), value)


def translate_bool(value: Any) -> str:
    if isinstance(value, bool):
        return "Si" if value else "No"

    if isinstance(value, str):
        normalized = value.strip().lower()
        if normalized == "true":
            return "Si"
        if normalized == "false":
            return "No"

    if value is None:
        return ""

    return str(value)


def format_epoch_ms(value: Any) -> str:
    if value in (None, ""):
        return ""

    try:
        timestamp_ms = int(value)
        return datetime.fromtimestamp(timestamp_ms / 1000.0).strftime("%d/%m/%Y %H:%M")
    except (TypeError, ValueError, OSError):
        return str(value)


def clean_multiline(value: Any) -> str:
    if value is None:
        return ""
    return " ".join(str(value).replace("\r", "\n").split())


def summarize_conditions(conditions: Any) -> str:
    """
    Convierte entityFilter.conditions en una cadena corta:
    TAG EQUALS [CONTEXTLESS]BIZTALK:OK | NAME CONTAINS foo
    """
    parts: List[str] = []
    for condition in as_list(conditions):
        condition_dict = as_dict(condition)
        condition_type = condition_dict.get("type", "")
        operator = condition_dict.get("operator", "")
        value = condition_dict.get("value", "")
        condition_text = " ".join(str(part) for part in (condition_type, operator, value) if part != "")
        if condition_text:
            parts.append(condition_text)

    return " | ".join(parts)


def summarize_dimension_filters(filters: Any) -> str:
    """
    Convierte dimensionFilter en una cadena corta:
    service=BTSSvc$XXX | Pool=pool_name
    """
    parts: List[str] = []
    for dimension_filter in as_list(filters):
        filter_dict = as_dict(dimension_filter)
        key = filter_dict.get("dimensionKey", "")
        value = filter_dict.get("dimensionValue", "")
        if key or value:
            parts.append(f"{key}={value}" if key and value else str(key or value))

    return " | ".join(parts)


def summarize_metadata(metadata: Any) -> str:
    """
    Resume eventTemplate.metadata sin depender de una forma unica.
    """
    parts: List[str] = []
    for metadata_item in as_list(metadata):
        item = as_dict(metadata_item)
        if not item:
            continue

        key = item.get("key") or item.get("metadataKey") or item.get("name")
        value = item.get("value") or item.get("metadataValue")
        if key and value:
            parts.append(f"{key}={value}")
        elif key:
            parts.append(str(key))
        else:
            parts.append(json.dumps(item, ensure_ascii=False, sort_keys=True))

    return " | ".join(parts)


def metric_identifier(query_definition: Dict[str, Any]) -> str:
    return query_definition.get("metricKey") or query_definition.get("metricSelector") or ""


def flatten_custom_metrics(items: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """
    Aplana una metrica custom por fila, manteniendo solo la informacion
    relevante de value y algunos metadatos de settings.
    """
    rows: List[Dict[str, Any]] = []

    for item in items:
        value = as_dict(item.get("value"))
        query_definition = as_dict(value.get("queryDefinition"))
        model_properties = as_dict(value.get("modelProperties"))
        event_template = as_dict(value.get("eventTemplate"))
        entity_filter = as_dict(query_definition.get("entityFilter"))

        rows.append({
            "Resumen": value.get("summary", ""),
            "Habilitado": translate_bool(value.get("enabled", "")),
            "TipoConsulta": translate_value(query_definition.get("type", ""), QUERY_TYPE_LABELS),
            "Metrica": metric_identifier(query_definition),
            "Agregacion": query_definition.get("aggregation", ""),
            "ManagementZone": query_definition.get("managementZone", ""),
            "FiltroEntidadDimension": entity_filter.get("dimensionKey", ""),
            "FiltroEntidadCondiciones": summarize_conditions(entity_filter.get("conditions", [])),
            "FiltroDimensiones": summarize_dimension_filters(query_definition.get("dimensionFilter", [])),
            "Modelo": translate_value(model_properties.get("type", ""), MODEL_TYPE_LABELS),
            "Condicion": translate_value(model_properties.get("alertCondition", ""), ALERT_CONDITION_LABELS),
            "Umbral": model_properties.get("threshold", ""),
            "AlertarSinDatos": translate_bool(model_properties.get("alertOnNoData", "")),
            "MuestrasViolacion": model_properties.get("violatingSamples", ""),
            "Muestras": model_properties.get("samples", ""),
            "MuestrasDesalerta": model_properties.get("dealertingSamples", ""),
            "FluctuacionSenal": model_properties.get("signalFluctuation", ""),
            "Tolerancia": model_properties.get("tolerance", ""),
            "TipoEvento": translate_value(event_template.get("eventType", ""), EVENT_TYPE_LABELS),
            "TituloEvento": event_template.get("title", ""),
            "DescripcionEvento": clean_multiline(event_template.get("description", "")),
            "FusionDavis": translate_bool(event_template.get("davisMerge", "")),
            "Metadata": summarize_metadata(event_template.get("metadata", [])),
            "DimensionEntidadEvento": value.get("eventEntityDimensionKey", ""),
            "LegacyId": value.get("legacyId", ""),
            "Autor": item.get("author", ""),
            "Creado": format_epoch_ms(item.get("created")),
            "Modificado": format_epoch_ms(item.get("modified")),
            "CreadoPor": item.get("createdBy", ""),
            "ModificadoPor": item.get("modifiedBy", ""),
            "ObjectId": item.get("objectId", ""),
        })

    print(f"Total de filas generadas: {len(rows)}")
    return rows


# ==========================================
# 4. Generacion del Excel
# ==========================================

def export_to_excel(df: pd.DataFrame, filename: str = DEFAULT_OUTPUT) -> None:
    """
    Exporta el DataFrame a Excel. Si no esta disponible openpyxl, genera un CSV
    alternativo.
    """
    if df.empty:
        print("[WARN] DataFrame vacio. No se generara reporte.")
        return

    try:
        with pd.ExcelWriter(filename, engine="openpyxl") as writer:
            df.to_excel(writer, index=False, sheet_name="MetricasCustom")
            worksheet = writer.sheets["MetricasCustom"]
            worksheet.freeze_panes = "A2"
            worksheet.auto_filter.ref = worksheet.dimensions

            for column_cells in worksheet.columns:
                header = str(column_cells[0].value)
                max_length = max(len(str(cell.value or "")) for cell in column_cells)
                worksheet.column_dimensions[column_cells[0].column_letter].width = min(
                    max(max_length + 2, len(header) + 2),
                    80,
                )

        print(f"Excel generado correctamente: {filename}")
    except Exception as e:
        print(f"[ERROR] No se pudo crear el archivo Excel. Detalles: {e}")
        csv_filename = filename.replace(".xlsx", ".csv")
        print("Generando version en formato CSV como alternativa...")
        df.to_csv(csv_filename, index=False, sep=";", encoding="utf-8-sig")
        print(f"Reporte generado: {csv_filename}")


# ==========================
# 5. Funcion principal main
# ==========================

def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Genera un Excel de metricas custom de Dynatrace."
    )
    parser.add_argument(
        "--input-json",
        help="Lee una respuesta JSON local en vez de consultar la API.",
    )
    parser.add_argument(
        "--output",
        default=DEFAULT_OUTPUT,
        help=f"Nombre del Excel de salida. Por defecto: {DEFAULT_OUTPUT}",
    )
    parser.add_argument(
        "--page-size",
        type=int,
        default=500,
        help="Tamano de pagina para la API de Dynatrace.",
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()

    if args.input_json:
        custom_metrics = load_items_from_json(args.input_json)
    else:
        load_environment_files()
        cfg = get_dynatrace_config()
        custom_metrics = fetch_custom_metrics(
            base_url=cfg["url"],
            token=cfg["token"],
            page_size=args.page_size,
            admin_access=False,
        )

    if not custom_metrics:
        print("No se encontraron metricas custom.")
        return

    rows = flatten_custom_metrics(custom_metrics)
    if not rows:
        print("Tras el aplanamiento no quedaron filas para exportar.")
        return

    column_order = [
        "Resumen",
        "Habilitado",
        "TipoConsulta",
        "Metrica",
        "Agregacion",
        "ManagementZone",
        "FiltroEntidadDimension",
        "FiltroEntidadCondiciones",
        "FiltroDimensiones",
        "Modelo",
        "Condicion",
        "Umbral",
        "AlertarSinDatos",
        "MuestrasViolacion",
        "Muestras",
        "MuestrasDesalerta",
        "FluctuacionSenal",
        "Tolerancia",
        "TipoEvento",
        "TituloEvento",
        "DescripcionEvento",
        "FusionDavis",
        "Metadata",
        "DimensionEntidadEvento",
        "LegacyId",
        "Autor",
        "Creado",
        "Modificado",
        "CreadoPor",
        "ModificadoPor",
        "ObjectId",
    ]
    df = pd.DataFrame(rows, columns=column_order)
    export_to_excel(df, filename=args.output)


if __name__ == "__main__":
    main()
