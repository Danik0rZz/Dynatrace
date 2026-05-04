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


SCHEMA_ID = "builtin:alerting.maintenance-window"
FIELDS = "objectId,value,created,modified,createdBy,modifiedBy,author"
DEFAULT_OUTPUT = "dynatrace_maintenance_windows_report.xlsx"

MAINTENANCE_TYPE_LABELS = {
    "PLANNED": "Planificada",
    "UNPLANNED": "No planificada",
}

SUPPRESSION_LABELS = {
    "DETECT_PROBLEMS_AND_ALERT": "Detectar problemas y alertar",
    "DETECT_PROBLEMS_DONT_ALERT": "Detectar problemas sin alertar",
    "DONT_DETECT_PROBLEMS": "No detectar problemas",
}

SCHEDULE_TYPE_LABELS = {
    "DAILY": "Diaria",
    "ONCE": "Una vez",
    "WEEKLY": "Semanal",
    "MONTHLY": "Mensual",
}

DAY_OF_WEEK_LABELS = {
    "MONDAY": "Lunes",
    "TUESDAY": "Martes",
    "WEDNESDAY": "Miercoles",
    "THURSDAY": "Jueves",
    "FRIDAY": "Viernes",
    "SATURDAY": "Sabado",
    "SUNDAY": "Domingo",
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

def fetch_maintenance_windows(
    base_url: str,
    token: str,
    page_size: int = 500,
    admin_access: bool = False,
) -> List[Dict[str, Any]]:
    """
    Llama al endpoint /api/v2/settings/objects para obtener ventanas de
    mantenimiento con paginacion automatica por nextPageKey.
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

    print(f"Total de ventanas recuperadas: {len(items)}")
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

    print(f"Total de ventanas leidas desde JSON: {len(items)}")
    return items


# ==================================================
# 3. Transformacion y aplanamiento JSON
# ==================================================

def as_dict(value: Any) -> Dict[str, Any]:
    return value if isinstance(value, dict) else {}


def first_value(*values: Any) -> Any:
    for value in values:
        if value is not None and value != "":
            return value
    return ""


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


def parse_datetime_value(value: Any) -> Optional[datetime]:
    if not isinstance(value, str) or not value.strip():
        return None

    normalized = value.strip().replace("Z", "+00:00")

    try:
        return datetime.fromisoformat(normalized)
    except ValueError:
        pass

    for fmt in ("%Y-%m-%d", "%d/%m/%Y", "%Y-%m-%d %H:%M:%S"):
        try:
            return datetime.strptime(normalized, fmt)
        except ValueError:
            continue

    return None


def format_date(value: Any) -> str:
    dt = parse_datetime_value(value)
    if not dt:
        return "" if value is None else str(value)
    return dt.strftime("%d/%m/%Y")


def format_once_datetime(value: Any) -> str:
    dt = parse_datetime_value(value)
    if not dt:
        return "" if value is None else str(value)
    return dt.strftime("%d/%m/%Y %H:%M")


def date_part(value: Any) -> str:
    """
    Extrae YYYY-MM-DD desde valores tipo 2026-04-28T23:50:00.
    Si no reconoce ese formato, devuelve cadena vacia.
    """
    if not isinstance(value, str):
        return ""
    if "T" in value:
        return value.split("T", 1)[0]
    if len(value) >= 10 and value[4:5] == "-" and value[7:8] == "-":
        return value[:10]
    return ""


def get_schedule_parts(schedule: Dict[str, Any]) -> Dict[str, Any]:
    """
    Extrae los campos comunes de dailyRecurrence, onceRecurrence,
    weeklyRecurrence y, si aparece, monthlyRecurrence.
    """
    schedule_type = schedule.get("scheduleType", "")
    recurrence = {}
    time_window = {}
    day_of_week = ""

    if "dailyRecurrence" in schedule:
        recurrence = as_dict(schedule.get("dailyRecurrence"))
        time_window = as_dict(recurrence.get("timeWindow"))
    elif "onceRecurrence" in schedule:
        recurrence = as_dict(schedule.get("onceRecurrence"))
        time_window = as_dict(recurrence.get("timeWindow")) or recurrence
    elif "weeklyRecurrence" in schedule:
        recurrence = as_dict(schedule.get("weeklyRecurrence"))
        time_window = as_dict(recurrence.get("timeWindow"))
        day_of_week = recurrence.get("dayOfWeek", "")
    elif "monthlyRecurrence" in schedule:
        recurrence = as_dict(schedule.get("monthlyRecurrence"))
        time_window = as_dict(recurrence.get("timeWindow"))

    recurrence_range = as_dict(recurrence.get("recurrenceRange"))
    start_time = first_value(time_window.get("startTime"), recurrence.get("startTime"))
    end_time = first_value(time_window.get("endTime"), recurrence.get("endTime"))

    schedule_start_date = first_value(
        recurrence_range.get("scheduleStartDate"),
        date_part(start_time) if schedule_type == "ONCE" else "",
    )
    schedule_end_date = first_value(
        recurrence_range.get("scheduleEndDate"),
        date_part(end_time) if schedule_type == "ONCE" else "",
    )

    return {
        "Planificación": translate_value(schedule_type, SCHEDULE_TYPE_LABELS),
        "Inicio": format_once_datetime(start_time) if schedule_type == "ONCE" else start_time,
        "Fin": format_once_datetime(end_time) if schedule_type == "ONCE" else end_time,
        "DiaSemana": translate_value(day_of_week, DAY_OF_WEEK_LABELS),
        "ZonaHoraria": first_value(time_window.get("timeZone"), recurrence.get("timeZone")),
        "Comienzo": format_date(schedule_start_date),
        "Finalizacion": format_date(schedule_end_date),
    }


def flatten_maintenance_windows(items: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """
    Aplana una ventana de mantenimiento por fila.
    """
    rows: List[Dict[str, Any]] = []

    for item in items:
        value = as_dict(item.get("value"))
        general_properties = as_dict(value.get("generalProperties"))
        schedule = as_dict(value.get("schedule"))
        schedule_parts = get_schedule_parts(schedule)

        rows.append({
            "Nombre": general_properties.get("name", ""),
            "Ventana": translate_value(
                general_properties.get("maintenanceType", ""),
                MAINTENANCE_TYPE_LABELS,
            ),
            "Supresion": translate_value(
                general_properties.get("suppression", ""),
                SUPPRESSION_LABELS,
            ),
            "Sinteticos": translate_bool(
                general_properties.get("disableSyntheticMonitorExecution", "")
            ),
            **schedule_parts,
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
            df.to_excel(writer, index=False, sheet_name="Ventanas")
            worksheet = writer.sheets["Ventanas"]
            worksheet.freeze_panes = "A2"
            worksheet.auto_filter.ref = worksheet.dimensions

            for column_cells in worksheet.columns:
                header = str(column_cells[0].value)
                max_length = max(len(str(cell.value or "")) for cell in column_cells)
                worksheet.column_dimensions[column_cells[0].column_letter].width = min(
                    max(max_length + 2, len(header) + 2),
                    60,
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
        description="Genera un Excel de ventanas de mantenimiento de Dynatrace."
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
        maintenance_windows = load_items_from_json(args.input_json)
    else:
        load_environment_files()
        cfg = get_dynatrace_config()
        maintenance_windows = fetch_maintenance_windows(
            base_url=cfg["url"],
            token=cfg["token"],
            page_size=args.page_size,
            admin_access=False,
        )

    if not maintenance_windows:
        print("No se encontraron ventanas de mantenimiento.")
        return

    rows = flatten_maintenance_windows(maintenance_windows)
    if not rows:
        print("Tras el aplanamiento no quedaron filas para exportar.")
        return

    column_order = [
        "Nombre",
        "Ventana",
        "Supresion",
        "Sinteticos",
        "Planificación",
        "Inicio",
        "Fin",
        "DiaSemana",
        "ZonaHoraria",
        "Comienzo",
        "Finalizacion",
    ]
    df = pd.DataFrame(rows, columns=column_order)
    export_to_excel(df, filename=args.output)


if __name__ == "__main__":
    main()
