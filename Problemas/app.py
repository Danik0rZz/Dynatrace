import os
import streamlit as st
import pandas as pd
from typing import List
from streamlit_echarts import st_echarts

# ==========================================
# Configuración inicial de Streamlit
# ==========================================
st.set_page_config(
    page_title="Dynatrace Executive Dashboard",
    page_icon="⚡",
    layout="wide",
    initial_sidebar_state="expanded"
)

# Estilizado oscuro del layout por defecto mediante CSS inyectado (para rematar detalles que Streamlit no toca por defecto)
st.markdown("""
<style>
    .css-18e3th9 { padding-top: 2rem; padding-bottom: 2rem; }
    div[data-testid="metric-container"] {
        background-color: #1e1e1e;
        border-radius: 8px;
        padding: 15px;
        border: 1px solid #333;
        box-shadow: 2px 2px 5px rgba(0,0,0,0.5);
    }
    div[data-testid="stExpander"] { background-color: #1e1e1e; }
    label { color: #cfcfcf !important; }
</style>
""", unsafe_allow_html=True)


# ==========================================
# 1. Carga Optimizada y Parseo de Datos
# ==========================================
@st.cache_data
def load_data() -> pd.DataFrame:
    """
    Carga y procesa el dataset en formato CSV o Excel con cacheo en memoria.
    Convierte las fechas a formato datetime y asegura de que no hay NaN descontrolados.
    """
    file_csv = "dynatrace_problems_report.csv"
    file_excel = "dynatrace_problems_report.xlsx"

    # Fallback transparente por si el archivo está en .xlsx como escupe históricamente Copilot.py
    if os.path.exists(file_csv):
        df = pd.read_csv(file_csv)
    elif os.path.exists(file_excel):
        df = pd.read_excel(file_excel)
    else:
        st.error(f"No se encontró el archivo base de datos en este directorio.")
        return pd.DataFrame()

    # Parseo de fechas
    if 'Inicio' in df.columns:
        df['Inicio'] = pd.to_datetime(df['Inicio'], errors='coerce')
    if 'Fin' in df.columns:
        df['Fin'] = pd.to_datetime(df['Fin'], errors='coerce')

    # Limpieza de nulos genéricos
    df.fillna("", inplace=True)

    # Aseguramos que la columna DuracionMinutos sea numérico
    if 'DuracionMinutos' in df.columns:
        df['DuracionMinutos'] = pd.to_numeric(df['DuracionMinutos'], errors='coerce').fillna(0)

    return df


# ==========================================
# 2. Funciones de Renderizado Lógico (Sidebar)
# ==========================================
def sidebar_filters(df: pd.DataFrame) -> pd.DataFrame:
    """
    Despliega los controles interactivos en el lateral y aplica todos los filtros
    de forma condicional sin romper la referencia del DataFrame original.
    """
    st.sidebar.header("Filtros Ejecutivos 📊")

    df_filtered = df.copy()

    # Si el dataframe está vacío o no ha cargado, salir limpio
    if df_filtered.empty:
        return df_filtered

    # ---- Componentes de Filtrado Convencional ----
    def add_filter(column: str, label: str):
        if column in df.columns:
            unique_vals = [v for v in df[column].unique() if v != ""]
            selected_vals = st.sidebar.multiselect(label, sorted(unique_vals))
            if selected_vals:
                return df_filtered.loc[df_filtered[column].isin(selected_vals)]
        return df_filtered

    df_filtered = add_filter("EntidadNombre", "Filtrar por EntidadNombre")
    df_filtered = add_filter("EntidadID", "Filtrar por EntidadID")
    df_filtered = add_filter("EntidadTipo", "Filtrar por EntidadTipo")
    df_filtered = add_filter("Titulo", "Filtrar por Titulo de Problema")
    df_filtered = add_filter("NivelDeImpacto", "Filtrar por Nivel de Impacto")
    df_filtered = add_filter("NivelDeSeveridad", "Filtrar por Nivel de Severidad")

    # ---- Lógica Especial para KubernetesNamespace ----
    col_k8s = "KubernetesNamespace"
    if col_k8s in df.columns:
        # Extraer únicos "limpios" (exploding por coma, quitando espacios vacios)
        # Esto nos asegura que si un problem tiene "A, B", A y B aparecen independientes
        raw_k8s_series = df[col_k8s].astype(str).str.split(',').explode().str.strip()
        unique_k8s_namespaces = [n for n in raw_k8s_series.unique() if n]

        if unique_k8s_namespaces:
            selected_k8s = st.sidebar.multiselect("Filtrar por Kubernetes Namespace", sorted(unique_k8s_namespaces))
            
            # Lógica inclusiva: Si el valor de la fila "CONTENÍA" alguno de los namespaces filtrados
            if selected_k8s:
                mask_k8s = df_filtered[col_k8s].apply(
                    lambda x: any(sel_k8s in str(x) for sel_k8s in selected_k8s)
                )
                df_filtered = df_filtered[mask_k8s]

    st.sidebar.markdown("---")
    st.sidebar.caption(f"Registros después de filtrado: {len(df_filtered)}")

    return df_filtered


# ==========================================
# 3. Componentes Visuales (Echarts)
# ==========================================
def render_kpis(df: pd.DataFrame):
    """
    Renderiza los KPI Superiores con métricas de salud vital.
    """
    total_problems = len(df)
    
    if total_problems == 0:
        mttr = 0.0
        pct_open = 0.0
        pct_closed = 0.0
        total_criticos = 0
    else:
        mttr = df["DuracionMinutos"].mean()
        
        # Calcular proporciones de estado
        if 'Estado' in df.columns:
            estado_counts = df['Estado'].value_counts()
            qty_open = estado_counts.get("OPEN", 0)
            qty_closed = estado_counts.get("CLOSED", 0)
            pct_open = (qty_open / total_problems) * 100
            pct_closed = (qty_closed / total_problems) * 100
        else:
            pct_open, pct_closed = 0.0, 0.0
            
        # Calcular total severos
        # Asumimos que criticos son 'ERROR', 'AVAILABILITY' o 'CRITICAL' o similares. Adaptable a taxonomía dynatrace.
        if 'NivelDeSeveridad' in df.columns:
             total_criticos = len(df[df['NivelDeSeveridad'].astype(str).str.upper().isin(['ERROR', 'AVAILABILITY', 'CRITICAL'])])
        else:
             total_criticos = 0

    # Desplegar KPIs en 4 columnas
    c1, c2, c3, c4 = st.columns(4)
    with c1:
        st.metric("Total Incidencias", total_problems)
    with c2:
        st.metric("MTTR Promedio", f"{mttr:.2f} min")
    with c3:
        st.metric("% Ratio (OPEN vs CLOSED)", f"O: {pct_open:.1f}% | C: {pct_closed:.1f}%")
    with c4:
        st.metric("Incidentes Críticos", total_criticos)

    st.markdown("<br>", unsafe_allow_html=True)


def render_charts(df: pd.DataFrame):
    """
    Sección interactiva montando ECharts en caliente. Inyección pura de diccionarios `options`.
    """
    if df.empty:
        st.info("Sin datos para mostrar gráficos en la selección actual.")
        return

    c_left, c_right = st.columns([6, 4])

    with c_left:
        # ---- 3A. Top Offenders (Bar Chart Horizontal) ----
        if 'EntidadNombre' in df.columns:
            top_offenders = df['EntidadNombre'].value_counts().nlargest(10).sort_values(ascending=True)
            
            # Limpiamos nombres si son muy largos
            categories = [str(x)[:40] + "..." if len(str(x)) > 40 else str(x) for x in top_offenders.index.tolist()]
            values = top_offenders.tolist()

            options_bar = {
                "title": {"text": "Top 10 Entidades Ofensoras", "textStyle": {"color": "#FFF"}},
                "tooltip": {
                    "trigger": "axis",
                    "axisPointer": {"type": "shadow"}
                },
                "grid": {"left": "3%", "right": "4%", "bottom": "3%", "containLabel": True},
                "xAxis": {
                    "type": "value", 
                    "splitLine": {"lineStyle": {"color": "#333"}}
                },
                "yAxis": {
                    "type": "category",
                    "data": categories,
                    "axisLabel": {"color": "#FFF"}
                },
                "series": [
                    {
                        "name": "Incidencias",
                        "type": "bar",
                        "data": values,
                        "itemStyle": {
                            "color": "#ef476f", # Estética vibrante
                            "borderRadius": [0, 5, 5, 0] # Bordes redondeados
                        },
                        "label": {"show": True, "position": "right", "color": "#FFF"}
                    }
                ],
                "backgroundColor": "transparent"
            }
            st_echarts(options=options_bar, height="400px")

    with c_right:
        # ---- 3B. Distribución Severidad (Donut Chart) ----
        if 'NivelDeSeveridad' in df.columns:
            severity_counts = df['NivelDeSeveridad'].value_counts()
            
            data_donut = []
            colorful_palette = ["#ef476f", "#ffd166", "#06d6a0", "#118ab2", "#073b4c"]
            for idx, (label, val) in enumerate(severity_counts.items()):
                # Color code por default por naming conventions en dynatrace
                lbl_color = colorful_palette[idx % len(colorful_palette)]
                if str(label).upper() == "AVAILABILITY": lbl_color = "#e63946"
                if str(label).upper() == "ERROR": lbl_color = "#f4a261"
                if str(label).upper() == "PERFORMANCE": lbl_color = "#e9c46a"
                if str(label).upper() == "CUSTOM_ALERT": lbl_color = "#2a9d8f"

                data_donut.append({"value": val, "name": label, "itemStyle": {"color": lbl_color}})

            options_donut = {
                "title": {
                    "text": "Impacto por Severidad",
                    "left": "center",
                    "textStyle": {"color": "#FFF"}
                },
                "tooltip": {"trigger": "item"},
                "legend": {
                    "top": "bottom",
                    "textStyle": {"color": "#FFF"}
                },
                "series": [
                    {
                        "name": "Severidad",
                        "type": "pie",
                        "radius": ["40%", "70%"],
                        "avoidLabelOverlap": False,
                        "itemStyle": {
                            "borderRadius": 5,
                            "borderColor": "#1e1e1e",
                            "borderWidth": 2
                        },
                        "label": {"show": False, "position": "center"},
                        "emphasis": {
                            "label": {"show": True, "fontSize": "20", "fontWeight": "bold", "color": "#FFF"}
                        },
                        "labelLine": {"show": False},
                        "data": data_donut
                    }
                ],
                "backgroundColor": "transparent"
            }
            st_echarts(options=options_donut, height="400px")

    # ---- 3C. Evolucion Temporal (Área con gradiente) ----
    st.markdown("### Línea Temporal de Afectación")
    if 'Inicio' in df.columns:
        # Agrupamos por la iteración de tiempo truncada a dia/hora dependiendo de la dispersion
        # Como es genérico, truncaremos a DIA (Date) temporalmente para el gráfico para asegurar legibilidad
        df_timeseries = df.copy()
        
        # Ocultar nulos que afecten al date range
        df_timeseries = df_timeseries.dropna(subset=['Inicio'])
        if not df_timeseries.empty:
            df_timeseries['FechaCorta'] = df_timeseries['Inicio'].dt.date
            ts_grouped = df_timeseries.groupby('FechaCorta').size()

            x_axis_data = [str(x) for x in ts_grouped.index.tolist()]
            y_axis_data = ts_grouped.tolist()

            options_line = {
                "tooltip": {"trigger": "axis"},
                "xAxis": {
                    "type": "category",
                    "boundaryGap": False,
                    "data": x_axis_data,
                    "axisLabel": {"color": "#FFF"}
                },
                "yAxis": {
                    "type": "value",
                    "splitLine": {"lineStyle": {"color": "#333"}},
                    "axisLabel": {"color": "#FFF"}
                },
                "grid": {"left": "3%", "right": "4%", "bottom": "5%", "containLabel": True},
                "series": [
                    {
                        "name": "Apariciones",
                        "type": "line",
                        "smooth": True,
                        "itemStyle": {"color": "#118ab2"},
                        "areaStyle": {
                            "color": {
                                "type": 'linear',
                                "x": 0, "y": 0, "x2": 0, "y2": 1,
                                "colorStops": [
                                    {"offset": 0, "color": "rgba(17,138,178,0.8)"},
                                    {"offset": 1, "color": "rgba(17,138,178,0.1)"}
                                ]
                            }
                        },
                        "data": y_axis_data
                    }
                ],
                "backgroundColor": "transparent"
            }
            st_echarts(options=options_line, height="350px")


def render_raw_data(df: pd.DataFrame):
    """
    Expander nativo de streamlit para la validación cruda de los filtros (Raw Data Audit).
    """
    with st.expander("🔬 Ver Registros en Detalle (Raw Data)"):
        st.dataframe(df, width='stretch')


# ==========================================
# Ejecución Principal
# ==========================================
def main():
    st.title("🛡️ Panel de Control Ejecutivo: Dynatrace Insights")
    st.markdown("Visualización macroscópica de problemas, causativas tecnológicas e impactos de infraestructura.")

    # 1. Cargar la data global
    df_raw = load_data()
    
    # 2. Filtrado Sidebar reactivo
    df_filtered = sidebar_filters(df_raw)

    if not df_filtered.empty:
        # 3. Dibujar KPIs Header
        render_kpis(df_filtered)

        # 4. Dibujar Gráficos Medios e Inferiores (Echarts)
        render_charts(df_filtered)

        # 5. Desplegable de Tabla Plana Data
        render_raw_data(df_filtered)
    else:
        st.warning("El dataset está actualmente vacío o se han sobre-filtrado todos los registros. Cambia las opciones de filtrado en la barra lateral.")

if __name__ == "__main__":
    main()
