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
# Constantes
# =========================

urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

MAINTENANCE_SCHEMA_ID = "builtin:alerting.maintenance-window"
METRIC_EVENTS_SCHEMA_ID = "builtin:anomaly-detection.metric-events"
SETTINGS_FIELDS = "objectId,value,created,modified,createdBy,modifiedBy,author"
DEFAULT_OUTPUT = "dynatrace_informe_completo.xlsx"
DEFAULT_PROBLEM_SELECTOR = 'problemFilterNames("CMS")'

COL_ANIO = "A\u00f1o"
COL_PLANIFICACION = "Planificaci\u00f3n"

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
# Utilidades generales
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
    dynatrace_url = os.getenv("DYNATRACE_URL")
    dynatrace_token = os.getenv("DYNATRACE_TOKEN")

    if not dynatrace_url or not dynatrace_token:
        print("[ERROR] Faltan DYNATRACE_URL o DYNATRACE_TOKEN en el entorno.")
        sys.exit(1)

    return {
        "url": dynatrace_url.rstrip("/"),
        "token": dynatrace_token,
    }


def as_dict(value: Any) -> Dict[str, Any]:
    return value if isinstance(value, dict) else {}


def as_list(value: Any) -> List[Any]:
    return value if isinstance(value, list) else []


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


def read_items_json(filename: str) -> List[Dict[str, Any]]:
    with open(filename, "r", encoding="utf-8") as fh:
        data = json.load(fh)

    if isinstance(data, dict):
        return data.get("items", [])
    if isinstance(data, list):
        return data
    return []


# =========================
# Fechas de problemas
# =========================

def parse_date_arg(value: str) -> datetime:
    try:
        return datetime.strptime(value.strip(), "%d/%m/%Y")
    except ValueError:
        print(f"[ERROR] Fecha invalida: {value}. Usa DD/MM/AAAA.")
        sys.exit(1)


def parse_date_input(prompt: str) -> datetime:
    while True:
        raw = input(prompt).strip()
        try:
            return datetime.strptime(raw, "%d/%m/%Y")
        except ValueError:
            print("Formato invalido. Usa DD/MM/AAAA (ejemplo: 01/09/2024).")


def build_time_window(start_date: datetime, end_date: datetime) -> Dict[str, int]:
    start_dt = datetime(
        year=start_date.year,
        month=start_date.month,
        day=start_date.day,
        hour=0,
        minute=0,
        second=0,
        microsecond=0,
    )
    end_dt = datetime(
        year=end_date.year,
        month=end_date.month,
        day=end_date.day,
        hour=23,
        minute=59,
        second=59,
        microsecond=999000,
    )

    if start_dt > end_dt:
        print("[ERROR] La fecha de inicio es posterior a la fecha de fin.")
        sys.exit(1)

    return {
        "from": int(start_dt.timestamp() * 1000),
        "to": int(end_dt.timestamp() * 1000),
    }


def get_time_window(args: argparse.Namespace) -> Dict[str, int]:
    if args.fecha_inicio and args.fecha_fin:
        start_date = parse_date_arg(args.fecha_inicio)
        end_date = parse_date_arg(args.fecha_fin)
    else:
        print("Introduce el rango de fechas para extraer problemas de Dynatrace.")
        start_date = parse_date_input("Fecha de inicio (DD/MM/AAAA): ")
        end_date = parse_date_input("Fecha de fin    (DD/MM/AAAA): ")

    window = build_time_window(start_date, end_date)
    print(f"Ventana de problemas seleccionada: {start_date:%d/%m/%Y} - {end_date:%d/%m/%Y}")
    return window


# =========================
# APIs Dynatrace
# =========================

def fetch_problems(
    base_url: str,
    token: str,
    from_ms: int,
    to_ms: int,
    page_size: int = 500,
    problem_selector: str = DEFAULT_PROBLEM_SELECTOR,
) -> List[Dict[str, Any]]:
    headers = {
        "Authorization": f"Api-Token {token}",
        "Content-Type": "application/json",
    }

    endpoint = f"{base_url}/api/v2/problems"
    params: Dict[str, Any] = {
        "from": from_ms,
        "to": to_ms,
        "pageSize": page_size,
    }
    if problem_selector:
        params["problemSelector"] = problem_selector

    problems: List[Dict[str, Any]] = []
    next_page_key: Optional[str] = None

    while True:
        request_params = {"nextPageKey": next_page_key} if next_page_key else params
        response = requests.get(endpoint, headers=headers, params=request_params, timeout=60, verify=False)

        if not response.ok:
            print(f"[ERROR] Llamada a Dynatrace problems fallo: {response.status_code} {response.text}")
            sys.exit(1)

        data = response.json()
        problems.extend(data.get("problems", []))

        next_page_key = data.get("nextPageKey")
        if not next_page_key:
            break

        time.sleep(0.2)

    print(f"Total de problemas recuperados: {len(problems)}")
    return problems


def fetch_settings_objects(
    base_url: str,
    token: str,
    schema_id: str,
    page_size: int = 500,
    admin_access: bool = False,
) -> List[Dict[str, Any]]:
    headers = {
        "Authorization": f"Api-Token {token}",
        "Content-Type": "application/json",
    }

    endpoint = f"{base_url}/api/v2/settings/objects"
    params: Dict[str, Any] = {
        "schemaIds": schema_id,
        "fields": SETTINGS_FIELDS,
        "pageSize": page_size,
        "adminAccess": str(admin_access).lower(),
    }

    items: List[Dict[str, Any]] = []
    next_page_key: Optional[str] = None

    while True:
        request_params = {"nextPageKey": next_page_key} if next_page_key else params
        response = requests.get(endpoint, headers=headers, params=request_params, timeout=60, verify=False)

        if not response.ok:
            print(f"[ERROR] Llamada a Dynatrace settings fallo: {response.status_code} {response.text}")
            sys.exit(1)

        data = response.json()
        items.extend(data.get("items", []))

        next_page_key = data.get("nextPageKey")
        if not next_page_key:
            break

        time.sleep(0.2)

    print(f"Total de objetos settings recuperados para {schema_id}: {len(items)}")
    return items


# =========================
# Problemas
# =========================

def ms_to_datetime(ms: Optional[int]) -> Optional[datetime]:
    if ms is None:
        return None
    try:
        return datetime.fromtimestamp(ms / 1000.0)
    except Exception:
        return None


def compute_duration_minutes(start_ms: Optional[int], end_ms: Optional[int]) -> Optional[float]:
    if start_ms is None:
        return None

    if end_ms is None:
        end_ms = int(time.time() * 1000)

    duration_ms = max(0, end_ms - start_ms)
    return round(duration_ms / 1000.0 / 60.0, 2)


def flatten_problems(
    problems: List[Dict[str, Any]],
    from_ms: int,
) -> List[Dict[str, Any]]:
    rows: List[Dict[str, Any]] = []

    for problem in problems:
        problem_id = problem.get("problemId") or problem.get("id")
        display_id = problem.get("displayId", "")
        title = problem.get("title", "")
        status = problem.get("status", "")
        impact_level = problem.get("impactLevel", "")
        severity_level = problem.get("severityLevel", "")

        start_time_ms = problem.get("startTime")
        end_time_ms = problem.get("endTime")

        if start_time_ms is not None and start_time_ms < from_ms:
            continue

        start_dt = ms_to_datetime(start_time_ms)
        end_dt = ms_to_datetime(end_time_ms)
        duration_minutes = compute_duration_minutes(start_time_ms, end_time_ms)

        dia = ""
        mes_esp = ""
        anio = ""
        hora_hh = ""
        semana_iso = ""
        if start_dt:
            dia = start_dt.day
            meses = {
                1: "Enero", 2: "Febrero", 3: "Marzo", 4: "Abril",
                5: "Mayo", 6: "Junio", 7: "Julio", 8: "Agosto",
                9: "Septiembre", 10: "Octubre", 11: "Noviembre", 12: "Diciembre",
            }
            mes_esp = meses.get(start_dt.month, "")
            anio = start_dt.year
            hora_hh = start_dt.strftime("%H")
            semana_iso = start_dt.isocalendar()[1]

        root_cause_entity = problem.get("rootCauseEntity") or {}
        root_cause_name = root_cause_entity.get("name", "")
        root_cause_type = root_cause_entity.get("entityId", {}).get("type", "")
        root_cause_id = root_cause_entity.get("entityId", {}).get("id", "")

        k8s_namespaces = problem.get("k8s.namespace.name", [])
        if isinstance(k8s_namespaces, list):
            k8s_namespace = ", ".join(k8s_namespaces)
        else:
            k8s_namespace = str(k8s_namespaces) if k8s_namespaces else ""

        base_row = {
            "ProblemId": problem_id,
            "Problem": display_id,
            "Titulo": title,
            "Estado": status,
            "NivelDeImpacto": impact_level,
            "NivelDeSeveridad": severity_level,
            "KubernetesNamespace": k8s_namespace,
            "CausaRaiz": root_cause_name,
            "CausaRaizTipo": root_cause_type,
            "CausaRaizID": root_cause_id,
            "Inicio": start_dt,
            "Dia": dia,
            "Mes": mes_esp,
            COL_ANIO: anio,
            "Hora": hora_hh,
            "Semana": semana_iso,
            "Fin": end_dt,
            "DuracionMinutos": duration_minutes,
        }

        affected_entities = problem.get("affectedEntities", [])
        if not isinstance(affected_entities, list) or not affected_entities:
            rows.append({
                **base_row,
                "EntidadID": "",
                "EntidadNombre": "",
                "EntidadTipo": "",
            })
            continue

        for entity in affected_entities:
            rows.append({
                **base_row,
                "EntidadID": entity.get("entityId", {}).get("id", ""),
                "EntidadNombre": entity.get("name", ""),
                "EntidadTipo": entity.get("entityId", {}).get("type", ""),
            })

    print(f"Total de filas de problemas: {len(rows)}")
    return rows


# =========================
# Ventanas de mantenimiento
# =========================

def date_part(value: Any) -> str:
    if not isinstance(value, str):
        return ""
    if "T" in value:
        return value.split("T", 1)[0]
    if len(value) >= 10 and value[4:5] == "-" and value[7:8] == "-":
        return value[:10]
    return ""


def get_schedule_parts(schedule: Dict[str, Any]) -> Dict[str, Any]:
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
        COL_PLANIFICACION: translate_value(schedule_type, SCHEDULE_TYPE_LABELS),
        "Inicio": format_once_datetime(start_time) if schedule_type == "ONCE" else start_time,
        "Fin": format_once_datetime(end_time) if schedule_type == "ONCE" else end_time,
        "DiaSemana": translate_value(day_of_week, DAY_OF_WEEK_LABELS),
        "ZonaHoraria": first_value(time_window.get("timeZone"), recurrence.get("timeZone")),
        "Comienzo": format_date(schedule_start_date),
        "Finalizacion": format_date(schedule_end_date),
    }


def flatten_maintenance_windows(items: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    rows: List[Dict[str, Any]] = []

    for item in items:
        value = as_dict(item.get("value"))
        general_properties = as_dict(value.get("generalProperties"))
        schedule = as_dict(value.get("schedule"))

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
            **get_schedule_parts(schedule),
            "Autor": item.get("author", ""),
            "Creado": format_epoch_ms(item.get("created")),
            "Modificado": format_epoch_ms(item.get("modified")),
        })

    print(f"Total de filas de ventanas: {len(rows)}")
    return rows


# =========================
# Metricas custom
# =========================

def summarize_conditions(conditions: Any) -> str:
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
    parts: List[str] = []
    for dimension_filter in as_list(filters):
        filter_dict = as_dict(dimension_filter)
        key = filter_dict.get("dimensionKey", "")
        value = filter_dict.get("dimensionValue", "")
        if key or value:
            parts.append(f"{key}={value}" if key and value else str(key or value))

    return " | ".join(parts)


def summarize_metadata(metadata: Any) -> str:
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

    print(f"Total de filas de metricas custom: {len(rows)}")
    return rows


# =========================
# Excel
# =========================

def dataframe_from_rows(rows: List[Dict[str, Any]], columns: List[str]) -> pd.DataFrame:
    return pd.DataFrame(rows, columns=columns)


def write_sheet(writer: pd.ExcelWriter, sheet_name: str, df: pd.DataFrame) -> None:
    df.to_excel(writer, index=False, sheet_name=sheet_name)
    worksheet = writer.sheets[sheet_name]
    worksheet.freeze_panes = "A2"

    if not df.empty:
        worksheet.auto_filter.ref = worksheet.dimensions

    for column_cells in worksheet.columns:
        header = str(column_cells[0].value)
        max_length = max(len(str(cell.value or "")) for cell in column_cells)
        worksheet.column_dimensions[column_cells[0].column_letter].width = min(
            max(max_length + 2, len(header) + 2),
            80,
        )


def export_workbook(
    output: str,
    problems_df: pd.DataFrame,
    windows_df: pd.DataFrame,
    metrics_df: pd.DataFrame,
) -> None:
    summary_df = pd.DataFrame([
        {"Hoja": "Problemas", "Filas": len(problems_df)},
        {"Hoja": "Ventanas", "Filas": len(windows_df)},
        {"Hoja": "MetricasCustom", "Filas": len(metrics_df)},
    ])

    try:
        with pd.ExcelWriter(output, engine="openpyxl") as writer:
            write_sheet(writer, "Resumen", summary_df)
            write_sheet(writer, "Problemas", problems_df)
            write_sheet(writer, "Ventanas", windows_df)
            write_sheet(writer, "MetricasCustom", metrics_df)

        print(f"Excel generado correctamente: {output}")
    except Exception as exc:
        print(f"[ERROR] No se pudo crear el archivo Excel. Detalles: {exc}")
        sys.exit(1)


# =========================
# Main
# =========================

def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Genera un unico Excel con problemas, ventanas de mantenimiento y metricas custom."
    )
    parser.add_argument("--fecha-inicio", help="Fecha de inicio para problemas en formato DD/MM/AAAA.")
    parser.add_argument("--fecha-fin", help="Fecha de fin para problemas en formato DD/MM/AAAA.")
    parser.add_argument("--output", default=DEFAULT_OUTPUT, help=f"Excel de salida. Por defecto: {DEFAULT_OUTPUT}")
    parser.add_argument("--problem-selector", default=DEFAULT_PROBLEM_SELECTOR, help="Selector de problemas.")
    parser.add_argument("--problem-page-size", type=int, default=500, help="Tamano de pagina para problemas.")
    parser.add_argument("--settings-page-size", type=int, default=500, help="Tamano de pagina para settings.")
    parser.add_argument("--skip-problemas", action="store_true", help="No consulta problemas.")
    parser.add_argument("--skip-ventanas", action="store_true", help="No consulta ventanas de mantenimiento.")
    parser.add_argument("--skip-metricas", action="store_true", help="No consulta metricas custom.")
    parser.add_argument("--ventanas-json", help="Lee ventanas desde un JSON local en vez de consultar la API.")
    parser.add_argument("--metricas-json", help="Lee metricas custom desde un JSON local en vez de consultar la API.")
    return parser.parse_args()


def main() -> None:
    args = parse_args()

    problems_columns = [
        "ProblemId", "Problem", "Titulo", "Estado", "NivelDeImpacto", "NivelDeSeveridad",
        "EntidadID", "EntidadNombre", "EntidadTipo", "KubernetesNamespace", "CausaRaiz",
        "CausaRaizTipo", "CausaRaizID", "Inicio", "Dia", "Mes", COL_ANIO, "Hora",
        "Semana", "Fin", "DuracionMinutos",
    ]
    windows_columns = [
        "Nombre", "Ventana", "Supresion", "Sinteticos", COL_PLANIFICACION, "Inicio",
        "Fin", "DiaSemana", "ZonaHoraria", "Comienzo", "Finalizacion",
        "Autor", "Creado", "Modificado",
    ]
    metrics_columns = [
        "Resumen", "Habilitado", "TipoConsulta", "Metrica", "Agregacion", "ManagementZone",
        "FiltroEntidadDimension", "FiltroEntidadCondiciones", "FiltroDimensiones", "Modelo",
        "Condicion", "Umbral", "AlertarSinDatos", "MuestrasViolacion", "Muestras",
        "MuestrasDesalerta", "FluctuacionSenal", "Tolerancia", "TipoEvento", "TituloEvento",
        "DescripcionEvento", "FusionDavis", "Metadata", "DimensionEntidadEvento", "LegacyId",
        "Autor", "Creado", "Modificado", "CreadoPor", "ModificadoPor", "ObjectId",
    ]

    cfg: Optional[Dict[str, str]] = None
    needs_api = (
        not args.skip_problemas
        or (not args.skip_ventanas and not args.ventanas_json)
        or (not args.skip_metricas and not args.metricas_json)
    )

    if needs_api:
        load_environment_files()
        cfg = get_dynatrace_config()

    problems_rows: List[Dict[str, Any]] = []
    windows_rows: List[Dict[str, Any]] = []
    metrics_rows: List[Dict[str, Any]] = []

    if not args.skip_problemas:
        assert cfg is not None
        window = get_time_window(args)
        problems = fetch_problems(
            base_url=cfg["url"],
            token=cfg["token"],
            from_ms=window["from"],
            to_ms=window["to"],
            page_size=args.problem_page_size,
            problem_selector=args.problem_selector,
        )
        problems_rows = flatten_problems(problems, from_ms=window["from"])

    if not args.skip_ventanas:
        if args.ventanas_json:
            windows_items = read_items_json(args.ventanas_json)
            print(f"Total de ventanas leidas desde JSON: {len(windows_items)}")
        else:
            assert cfg is not None
            windows_items = fetch_settings_objects(
                base_url=cfg["url"],
                token=cfg["token"],
                schema_id=MAINTENANCE_SCHEMA_ID,
                page_size=args.settings_page_size,
            )
        windows_rows = flatten_maintenance_windows(windows_items)

    if not args.skip_metricas:
        if args.metricas_json:
            metric_items = read_items_json(args.metricas_json)
            print(f"Total de metricas custom leidas desde JSON: {len(metric_items)}")
        else:
            assert cfg is not None
            metric_items = fetch_settings_objects(
                base_url=cfg["url"],
                token=cfg["token"],
                schema_id=METRIC_EVENTS_SCHEMA_ID,
                page_size=args.settings_page_size,
            )
        metrics_rows = flatten_custom_metrics(metric_items)

    problems_df = dataframe_from_rows(problems_rows, problems_columns)
    windows_df = dataframe_from_rows(windows_rows, windows_columns)
    metrics_df = dataframe_from_rows(metrics_rows, metrics_columns)

    export_workbook(
        output=args.output,
        problems_df=problems_df,
        windows_df=windows_df,
        metrics_df=metrics_df,
    )


if __name__ == "__main__":
    main()
