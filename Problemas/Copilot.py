import os
import sys
import time
import math
import requests
import pandas as pd
from datetime import datetime, timedelta
from typing import Dict, Any, List, Optional
from dotenv import load_dotenv
import urllib3

# =========================
# Desactiva alertas de certificados
# =========================
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)



# =========================
# 1. Carga de configuración
# =========================

def get_dynatrace_config() -> Dict[str, str]:
    """
    Recupera dynatrace_url y dynatrace_token desde variables de entorno.
    """
    dynatrace_url = os.getenv("DYNATRACE_URL")
    dynatrace_token = os.getenv("DYNATRACE_TOKEN")

    if not dynatrace_url or not dynatrace_token:
        print("[ERROR] Faltan DYNATRACE_URL o DYNATRACE_TOKEN en el entorno.")
        sys.exit(1)

    # Normalizar: quitar barra final si existe
    dynatrace_url = dynatrace_url.rstrip("/")

    return {
        "url": dynatrace_url,
        "token": dynatrace_token,
    }


# ==========================================
# 2. Entrada de fechas y conversión a Epoch
# ==========================================

def parse_date_input(prompt: str) -> datetime:
    """
    Pide una fecha al usuario en formato DD/MM/AAAA y la devuelve como datetime.
    """
    while True:
        raw = input(prompt).strip()
        try:
            dt = datetime.strptime(raw, "%d/%m/%Y")
            return dt
        except ValueError:
            print("Formato inválido. Usa DD/MM/AAAA (ejemplo: 01/09/2024).")


def get_time_window() -> Dict[str, int]:
    """
    Solicita fecha de inicio y fin, valida y devuelve epoch en milisegundos.
    - Inicio: 00:00:00
    - Fin:    23:59:59
    """
    print("Introduce el rango de fechas para extraer problemas de Dynatrace.")
    start_date = parse_date_input("Fecha de inicio (DD/MM/AAAA): ")
    end_date = parse_date_input("Fecha de fin    (DD/MM/AAAA): ")

    # Normalizar horas
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

    start_epoch_ms = int(start_dt.timestamp() * 1000)
    end_epoch_ms = int(end_dt.timestamp() * 1000)

    print(f"Ventana seleccionada:")
    print(f"  Inicio: {start_dt}  -> {start_epoch_ms} ms")
    print(f"  Fin:    {end_dt} -> {end_epoch_ms} ms")

    return {
        "from": start_epoch_ms,
        "to": end_epoch_ms,
    }


# ==========================================
# 3. Extracción de datos desde Dynatrace v2
# ==========================================

def fetch_problems(
    base_url: str,
    token: str,
    from_ms: int,
    to_ms: int,
    page_size: int = 500,
) -> List[Dict[str, Any]]:
    """
    Llama al endpoint /api/v2/problems con paginación automática.
    Devuelve una lista de problemas (JSON).
    """
    headers = {
        "Authorization": f"Api-Token {token}",
        "Content-Type": "application/json",
    }

    problems: List[Dict[str, Any]] = []
    endpoint = f"{base_url}/api/v2/problems"

    params = {
        "from": from_ms,
        "to": to_ms,
        "pageSize": page_size,
        "problemSelector": 'problemFilterNames("CMS")',
    }

    next_page_key: Optional[str] = None

    while True:
        if next_page_key:
            # Cuando hay cursor, se usa nextPageKey en lugar de from/to
            params = {
                "nextPageKey": next_page_key
            }

        response = requests.get(endpoint, headers=headers, params=params, timeout=60, verify=False)

        if not response.ok:
            print(f"[ERROR] Llamada a Dynatrace falló: {response.status_code} {response.text}")
            sys.exit(1)

        data = response.json()

        batch = data.get("problems", [])
        problems.extend(batch)

        next_page_key = data.get("nextPageKey")
        if not next_page_key:
            break

        # Pequeña pausa para no saturar el API
        time.sleep(0.2)

    print(f"Total de problemas recuperados: {len(problems)}")
    return problems


# ==================================================
# 4. Transformación, limpieza y "aplanamiento" JSON
# ==================================================

def ms_to_datetime(ms: Optional[int]) -> Optional[datetime]:
    if ms is None:
        return None
    try:
        return datetime.fromtimestamp(ms / 1000.0)
    except Exception:
        return None


def compute_duration_minutes(start_ms: Optional[int], end_ms: Optional[int]) -> Optional[float]:
    """
    Calcula duración en minutos. Si end_ms es None, usa el tiempo actual.
    """
    if start_ms is None:
        return None

    if end_ms is None:
        end_ms = int(time.time() * 1000)

    duration_ms = max(0, end_ms - start_ms)
    return round(duration_ms / 1000.0 / 60.0, 2)


def explode_problems(
    problems: List[Dict[str, Any]],
    base_url: str,
    from_ms: int,
    to_ms: int,
) -> List[Dict[str, Any]]:
    """
    Aplana la estructura de problemas:
    - Calcula duración
    - Descarta problemas que empezaron antes del rango (startTime < from_ms)
    - Explota entidades afectadas en filas individuales
    """
    rows: List[Dict[str, Any]] = []

    for p in problems:
        problem_id = p.get("problemId") or p.get("id")
        display_id = p.get("displayId", "")
        title = p.get("title", "")
        status = p.get("status", "")
        impact_level = p.get("impactLevel", "")
        severity_level = p.get("severityLevel", "")

        start_time_ms = p.get("startTime")
        end_time_ms = p.get("endTime")

        # Filtrado: descartar problemas que se originaron antes del rango
        if start_time_ms is not None and start_time_ms < from_ms:
            continue

        duration_minutes = compute_duration_minutes(start_time_ms, end_time_ms)

        start_dt = ms_to_datetime(start_time_ms)
        end_dt = ms_to_datetime(end_time_ms)

        root_cause_entity = p.get("rootCauseEntity") or {}
        root_cause_name = root_cause_entity.get("name", "")
        root_cause_type = root_cause_entity.get("entityId", {}).get("type", "")
        root_cause_id = root_cause_entity.get("entityId", {}).get("id", "")

        k8s_namespaces = p.get("k8s.namespace.name", [])
        k8s_namespace = ", ".join(k8s_namespaces) if isinstance(k8s_namespaces, list) else str(k8s_namespaces) if k8s_namespaces else ""

        affected_entities = p.get("affectedEntities", [])
        if not isinstance(affected_entities, list) or not affected_entities:
            # Si no hay entidades, igualmente generamos una fila "genérica"
            rows.append({
                "ProblemId": problem_id,
                "Problem": display_id,
                "Titulo": title,
                "Estado": status,
                "NivelDeImpacto": impact_level,
                "NivelDeSeveridad": severity_level,
                "EntidadID": "",
                "EntidadNombre": "",
                "EntidadTipo": "",
                "KubernetesNamespace": k8s_namespace,
                "CausaRaiz": root_cause_name,
                "CausaRaizTipo": root_cause_type,
                "CausaRaizID": root_cause_id,
                "Inicio": start_dt,
                "Fin": end_dt,
                "DuracionMinutos": duration_minutes,
            })
            continue

        # Explode: una fila por entidad afectada
        for entity in affected_entities:
            entity_name = entity.get("name", "")
            entity_type = entity.get("entityId", {}).get("type", "")
            entity_id = entity.get("entityId", {}).get("id", "")

            rows.append({
                "ProblemId": problem_id,
                "Problem": display_id,
                "Titulo": title,
                "Estado": status,
                "NivelDeImpacto": impact_level,
                "NivelDeSeveridad": severity_level,
                "EntidadID": entity_id,
                "EntidadNombre": entity_name,
                "EntidadTipo": entity_type,
                "KubernetesNamespace": k8s_namespace,
                "CausaRaiz": root_cause_name,
                "CausaRaizTipo": root_cause_type,
                "CausaRaizID": root_cause_id,
                "Inicio": start_dt,
                "Fin": end_dt,
                "DuracionMinutos": duration_minutes,
            })

    print(f"Total de filas tras 'explode' de entidades: {len(rows)}")
    return rows


# ==========================================
# 5. Generación del Excel con formato
# ==========================================

def export_to_excel(df: pd.DataFrame, filename: str = "dynatrace_problems_report.xlsx") -> None:
    """
    Exporta el DataFrame a Excel de forma sencilla (motor: openpyxl).
    En caso de error (por ejemplo, si no tienes openpyxl instalado), genera un CSV alternativo.
    """
    if df.empty:
        print("[WARN] DataFrame vacío. No se generará reporte.")
        return

    try:
        # Usa openpyxl explícitamente y guarda sin formato
        df.to_excel(filename, index=False, engine="openpyxl")
        print(f"Excel generado correctamente: {filename}")
    except Exception as e:
        print(f"[ERROR] No se pudo crear el archivo Excel. Asegúrate de tener openpyxl instalado. Detalles: {e}")
        csv_filename = filename.replace(".xlsx", ".csv")
        print(f"Generando versión en formato CSV como alternativa...")
        df.to_csv(csv_filename, index=False, sep=";", encoding="utf-8-sig")
        print(f"Reporte generado: {csv_filename}")


# ==========================
# 6. Función principal main
# ==========================

def main():
    # 1. Cargar .env y configuración
    load_dotenv(".env")
    cfg = get_dynatrace_config()

    # 2. Obtener ventana de tiempo
    window = get_time_window()
    from_ms = window["from"]
    to_ms = window["to"]

    # 3. Extraer problemas desde Dynatrace
    problems = fetch_problems(
        base_url=cfg["url"],
        token=cfg["token"],
        from_ms=from_ms,
        to_ms=to_ms,
        page_size=500,
    )

    if not problems:
        print("No se encontraron problemas en el rango especificado.")
        return

    # 4. Transformar y aplanar
    rows = explode_problems(
        problems=problems,
        base_url=cfg["url"],
        from_ms=from_ms,
        to_ms=to_ms,
    )

    if not rows:
        print("Tras el filtrado y aplanamiento no quedaron filas para exportar.")
        return

    df = pd.DataFrame(rows)

    # 5. Exportar a Excel con formato
    export_to_excel(df, filename="dynatrace_problems_report.xlsx")


if __name__ == "__main__":
    main()
