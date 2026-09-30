# -*- coding: utf-8 -*-
"""
Cotizador Persianas Steven — versión optimizada.

Cambios respecto a la versión original:
- st.set_page_config va antes de cualquier otro comando de Streamlit.
- URL de Apps Script y parámetros de negocio salen de st.secrets / entorno.
- Dinero calculado con Decimal (sin errores de redondeo de float).
- Manejo de errores específico (sin `except:` pelado) y respuestas JSON.
- PDF cacheado, robusto a fpdf1/fpdf2 y con texto sanitizado a latin-1.
- Un solo criterio para el nombre del cliente (PDF y nube usan el mismo).
- El folio confirmado por el servidor tiene prioridad (evita duplicados).
"""
from __future__ import annotations

import json
import os
import time
from datetime import datetime
from decimal import Decimal, ROUND_HALF_UP

import pandas as pd
import requests
import streamlit as st
from fpdf import FPDF

# Zona horaria: zoneinfo (estándar) con respaldo a pytz
try:
    from zoneinfo import ZoneInfo
    TZ_COLOMBIA = ZoneInfo("America/Bogota")
except Exception:  # pragma: no cover
    import pytz
    TZ_COLOMBIA = pytz.timezone("America/Bogota")

# --- 1. CONFIGURACIÓN DE PÁGINA (debe ser el primer comando st.*) ---
st.set_page_config(page_title="Persianas Steven", page_icon="🪟", layout="centered")

# --- 2. CONFIGURACIÓN DE NEGOCIO ---
def _secret(nombre: str, default: str = "") -> str:
    """Lee un valor de st.secrets o de variables de entorno."""
    try:
        return str(st.secrets.get(nombre, os.getenv(nombre, default)))
    except Exception:
        return os.getenv(nombre, default)

URL_APPSCRIPT = _secret("APPSCRIPT_URL")
TASA_IMPUESTO = Decimal(_secret("TASA_IMPUESTO", "0.07"))
FACTOR_DESPERDICIO = Decimal(_secret("FACTOR_DESPERDICIO", "1.15"))
RECARGO_MOTOR = Decimal(_secret("RECARGO_MOTOR", "165000"))
PRECIOS_M2 = {
    "Blackout": Decimal(_secret("PRECIO_BLACKOUT", "48000")),
    "Screen": Decimal(_secret("PRECIO_SCREEN", "58000")),
    "Sheer Elegance": Decimal(_secret("PRECIO_SHEER", "88000")),
}
PULGADA_A_METRO = Decimal("0.0254")
CLIENTE_POR_DEFECTO = "CONSUMIDOR FINAL"

# --- 3. ESTILOS CSS ---
st.markdown("""
    <style>
    #MainMenu {visibility: hidden;}
    footer {visibility: hidden;}
    header {visibility: hidden;}
    .stAppDeployButton {display:none;}
    div[data-testid="stToolbar"] { visibility: hidden !important; }

    div.stButton > button:first-child[kind="primary"] {
        background-color: #28a745 !important;
        border-color: #28a745 !important;
        color: white !important;
    }
    .stColumn div.stButton > button[kind="primary"] {
        background-color: #dc3545 !important;
        border-color: #dc3545 !important;
        color: white !important;
    }
    </style>
    """, unsafe_allow_html=True)

# --- 4. CAPA DE NUBE (Apps Script) ---
class NubeError(Exception):
    """Error controlado al comunicarse con Google Apps Script."""


def _appscript_json(resp: requests.Response) -> dict:
    """Valida status HTTP y cuerpo JSON con contrato {"ok": bool, ...}."""
    resp.raise_for_status()
    try:
        data = resp.json()
    except ValueError as e:
        raise NubeError(f"Respuesta no JSON de Apps Script: {resp.text[:200]}") from e
    if not data.get("ok"):
        raise NubeError(str(data.get("error", "Apps Script devolvió ok=false")))
    return data


def obtener_proximo_folio() -> int | None:
    """
    Devuelve el próximo folio disponible.
    Soporta el contrato nuevo {"ok": true, "ultimo_folio": N} y, como
    respaldo, el formato antiguo (cuerpo de texto con un entero).
    """
    if not URL_APPSCRIPT:
        return None
    try:
        resp = requests.get(URL_APPSCRIPT, params={"action": "ultimo_folio"}, timeout=10)
        resp.raise_for_status()
        try:
            data = resp.json()
            if data.get("ok"):
                return int(data["ultimo_folio"]) + 1
        except ValueError:
            pass
        return int(resp.text.strip()) + 1  # formato legado
    except (requests.RequestException, ValueError, KeyError):
        return None


def registrar_en_nube(datos: dict) -> dict:
    """
    Envía la cotización a la nube. Devuelve el dict de respuesta
    (idealmente {"ok": true, "folio": N}). Lanza NubeError si falla.
    """
    if not URL_APPSCRIPT:
        raise NubeError("Falta configurar APPSCRIPT_URL en secrets/entorno.")
    try:
        resp = requests.post(URL_APPSCRIPT, json=datos, timeout=20, allow_redirects=True)
        return _appscript_json(resp)
    except requests.RequestException as e:
        raise NubeError(f"Error de red al registrar: {e}") from e

# --- 5. LÓGICA DE NEGOCIO (pura y testeable) ---
def calcular_item(ancho: float, largo: float, tipo_tela: str,
                  motor: str, cantidad: int, usar_pulgadas: bool) -> tuple[Decimal, Decimal, int]:
    """Devuelve (área facturable m², precio unitario, subtotal entero en pesos)."""
    factor = PULGADA_A_METRO if usar_pulgadas else Decimal("1")
    area = (Decimal(str(ancho)) * factor) * (Decimal(str(largo)) * factor) * FACTOR_DESPERDICIO
    p_unit = area * PRECIOS_M2[tipo_tela]
    if motor == "Motorizada":
        p_unit += RECARGO_MOTOR
    subtotal = int((p_unit * Decimal(cantidad)).quantize(Decimal("1"), rounding=ROUND_HALF_UP))
    return area, p_unit, subtotal


def calcular_totales(carrito: list[dict]) -> tuple[int, int, int]:
    """Devuelve (subtotal, impuesto, total) en pesos enteros."""
    subtotal = sum(int(i["subtotal_item"]) for i in carrito)
    impuesto = int((Decimal(subtotal) * TASA_IMPUESTO).quantize(Decimal("1"), rounding=ROUND_HALF_UP))
    return subtotal, impuesto, subtotal + impuesto


def normalizar_cliente(nombre: str) -> str:
    nombre = (nombre or "").strip().upper()
    return nombre if nombre else CLIENTE_POR_DEFECTO

# --- 6. PDF ---
def _latin(s) -> str:
    """Sanitiza texto para fuentes core latin-1 del PDF."""
    return str(s).encode("latin-1", "replace").decode("latin-1")


@st.cache_data(show_spinner=False)
def generar_pdf_bytes(n_folio: int, nombre_cliente: str, carrito_json: str, fecha: str) -> bytes:
    """Genera el PDF de la cotización. Cacheado por (folio, cliente, carrito, fecha)."""
    carrito = json.loads(carrito_json)
    subtotal, impuesto, total = calcular_totales(carrito)

    pdf = FPDF()
    pdf.add_page()
    pdf.set_font("Arial", "B", 20)
    pdf.cell(200, 15, txt="Persianas Steven", ln=True, align="C")
    pdf.ln(10)
    pdf.set_font("Arial", "B", 12)
    pdf.cell(100, 10, txt=f"Cotizacion No: {n_folio}")
    pdf.cell(100, 10, txt=f"Fecha: {fecha}", ln=True, align="R")
    pdf.set_font("Arial", "", 12)
    pdf.cell(200, 10, txt=_latin(f"Cliente: {nombre_cliente}"), ln=True)
    pdf.ln(5)

    pdf.set_fill_color(230, 230, 230)
    pdf.set_font("Arial", "B", 9)
    pdf.cell(80, 10, "Descripcion", border=1, fill=True, align="C")
    pdf.cell(15, 10, "U.M", border=1, fill=True, align="C")
    pdf.cell(35, 10, "Precio Unit.", border=1, fill=True, align="C")
    pdf.cell(15, 10, "Cant.", border=1, fill=True, align="C")
    pdf.cell(45, 10, "Subtotal", border=1, fill=True, align="C", ln=True)

    pdf.set_font("Arial", size=9)
    for item in carrito:
        pdf.cell(80, 10, _latin(item["descripcion"])[:45], border=1)
        pdf.cell(15, 10, _latin(item["unidad"]), border=1, align="C")
        pdf.cell(35, 10, f"${int(item['valor_item']):,}", border=1, align="R")
        pdf.cell(15, 10, str(item["cantidad"]), border=1, align="C")
        pdf.cell(45, 10, f"${int(item['subtotal_item']):,}", border=1, align="R", ln=True)

    pdf.ln(5)
    pdf.set_font("Arial", "B", 10)
    pdf.cell(145, 8, "SUBTOTAL:", align="R")
    pdf.cell(45, 8, f"${subtotal:,}", border=1, ln=True, align="R")
    pdf.cell(145, 8, _latin(f"IMPUESTO ({TASA_IMPUESTO:.0%}):"), align="R")
    pdf.cell(45, 8, f"${impuesto:,}", border=1, ln=True, align="R")
    pdf.set_fill_color(240, 240, 240)
    pdf.cell(145, 10, "TOTAL COTIZADO:", align="R")
    pdf.cell(45, 10, f"${total:,}", border=1, ln=True, align="R", fill=True)

    raw = pdf.output(dest="S")
    # fpdf1 devuelve str; fpdf2 devuelve bytearray
    return raw.encode("latin-1") if isinstance(raw, str) else bytes(raw)

# --- 7. ESTADO DE SESIÓN ---
def init_state() -> None:
    defaults = {
        "carrito": [],
        "item_id": 0,
        "cliente_limpio": 0,
        "msg_exito": False,
        "msg_error": "",
        "bloqueo_envio": False,
    }
    for clave, valor in defaults.items():
        if clave not in st.session_state:
            st.session_state[clave] = valor
    if "n_folio" not in st.session_state:
        st.session_state.n_folio = obtener_proximo_folio() or 1

init_state()
fecha_hoy = datetime.now(TZ_COLOMBIA).strftime("%d/%m/%Y")

# --- 8. INTERFAZ ---
st.markdown('<link href="https://fonts.googleapis.com/icon?family=Material+Icons" rel="stylesheet">', unsafe_allow_html=True)
st.markdown("<h1 style='display: flex; align-items: center;'><i class='material-icons' style='font-size: 45px; margin-right: 15px; color: #4F8BF9;'>window</i>Persianas Steven</h1>", unsafe_allow_html=True)

if st.session_state.msg_exito:
    st.success("✅ ¡Registro enviado al Drive con éxito!")
    st.session_state.msg_exito = False
if st.session_state.msg_error:
    st.error(st.session_state.msg_error)
    st.session_state.msg_error = ""

input_cliente = st.text_input("Nombre del Cliente", placeholder="Ej: PABLO PEREZ",
                              key=f"cli_{st.session_state.cliente_limpio}")
cliente = normalizar_cliente(input_cliente)
st.write(f"Folio Actual: **#{st.session_state.n_folio}**")
st.divider()

# DATOS ÍTEM
usar_pulgadas = st.toggle("📐 Usar Pulgadas (in)", value=False, key=f"pulg_{st.session_state.item_id}")
unidad_m = "in" if usar_pulgadas else "m"

col1, col2 = st.columns(2)
with col1:
    ancho = st.number_input(f"Ancho ({unidad_m})", min_value=0.0, step=0.01, format="%.2f",
                            value=None, placeholder="0.00", key=f"anc_{st.session_state.item_id}")
    tipo_tela = st.selectbox("Tipo de Tela", list(PRECIOS_M2.keys()), index=None,
                             placeholder="Seleccione tela...", key=f"tel_{st.session_state.item_id}")
with col2:
    largo = st.number_input(f"Largo ({unidad_m})", min_value=0.0, step=0.01, format="%.2f",
                            value=None, placeholder="0.00", key=f"lar_{st.session_state.item_id}")
    motor = st.radio("Accionamiento", ["Manual", "Motorizada"], key=f"mot_{st.session_state.item_id}")

cantidad = st.number_input("Cantidad", min_value=1, step=1, value=1, key=f"can_{st.session_state.item_id}")

if ancho and largo and tipo_tela and cantidad:
    area_f, p_unit, sub_total_item = calcular_item(ancho, largo, tipo_tela, motor, cantidad, usar_pulgadas)

    st.info(f"Área facturable (con desperdicio): {area_f:.2f} m²")
    st.success(f"## Subtotal Ítem: ${sub_total_item:,}")

    if st.button("➕ Agregar al carrito"):
        st.session_state.carrito.append({
            "descripcion": f"{tipo_tela} ({ancho}x{largo}{unidad_m}) {motor}",
            "unidad": unidad_m,
            "cantidad": int(cantidad),
            "valor_item": int(p_unit.quantize(Decimal('1'), rounding=ROUND_HALF_UP)),
            "subtotal_item": sub_total_item,
        })
        st.session_state.item_id += 1
        st.rerun()

# RESUMEN Y REGISTRO
if st.session_state.carrito:
    st.divider()
    carrito = st.session_state.carrito
    subtotal_c, impuesto_c, total_c = calcular_totales(carrito)

    df_resumen = pd.DataFrame(carrito)
    df_mostrar = pd.DataFrame({
        "Folio": [st.session_state.n_folio] * len(df_resumen),
        "Fecha": fecha_hoy,
        "Cliente": cliente,
        "Descripción": df_resumen["descripcion"],
        "U.M": df_resumen["unidad"],
        "Cantidad": df_resumen["cantidad"],
        "Valor ítem": df_resumen["valor_item"].map("${:,}".format),
        "Subtotal": df_resumen["subtotal_item"].map("${:,}".format),
    })
    st.table(df_mostrar)
    st.write(f"**Subtotal:** ${subtotal_c:,}  |  **Impuesto ({TASA_IMPUESTO:.0%}):** ${impuesto_c:,}  |  **Total:** ${total_c:,}")

    carrito_json = json.dumps(carrito, ensure_ascii=False, sort_keys=True)
    try:
        pdf_out = generar_pdf_bytes(st.session_state.n_folio, cliente, carrito_json, fecha_hoy)
        st.download_button("📩 Descargar PDF", data=pdf_out,
                           file_name=f"Cotización-{st.session_state.n_folio}.pdf",
                           mime="application/pdf", use_container_width=True)
    except Exception as e:
        st.error(f"No se pudo generar el PDF: {e}")

    if not st.session_state.bloqueo_envio:
        if st.button("💾 REGISTRAR Y LIMPIAR TODO", use_container_width=True, type="primary"):
            st.session_state.bloqueo_envio = True
            st.rerun()
    else:
        st.info("⏳ Enviando información al Drive... Por favor espere.")
        datos_nube = {
            "folio": st.session_state.n_folio,
            "fecha": fecha_hoy,
            "cliente": cliente,
            "items_detalle": carrito,
            "subtotal": subtotal_c,
            "impuesto": impuesto_c,
            "total_general": total_c,
        }
        try:
            respuesta = registrar_en_nube(datos_nube)
            # Si el servidor confirma un folio, ese manda (evita duplicados)
            folio_confirmado = int(respuesta.get("folio", st.session_state.n_folio))
            st.session_state.n_folio = folio_confirmado + 1
            st.session_state.carrito = []
            st.session_state.cliente_limpio += 1
            st.session_state.msg_exito = True
        except NubeError as e:
            st.session_state.msg_error = f"❌ Error al registrar: {e}. Intente de nuevo."
        finally:
            st.session_state.bloqueo_envio = False
        st.rerun()
