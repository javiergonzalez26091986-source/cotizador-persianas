# -*- coding: utf-8 -*-
import streamlit as st
from fpdf import FPDF
import requests
import json
from datetime import datetime
import pandas as pd
import time
import pytz

# --- 1. MOTOR DE ESTABILIDAD Y ZONA HORARIA ---
if 'last_interaction' not in st.session_state:
    st.session_state.last_interaction = time.time()

colombia_tz = pytz.timezone('America/Bogota')
fecha_hoy = datetime.now(colombia_tz).strftime("%d/%m/%Y")

# --- CONFIGURACIÓN DE PÁGINA ---
st.set_page_config(page_title="Persianas Steven", page_icon="🪟", layout="centered")

# --- ESTILOS CSS ---
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

# URL
URL_APPSCRIPT = "https://script.google.com/macros/s/AKfycbxVFX5JzM8LJsi5EdA6eWq33PD-ZlVY0xhbTzDG15c_fPkoswEdw4qrtiaVwJQsgWU-Uw/exec"

# --- FUNCIONES NUBE ---
def obtener_ultimo_folio():
    try:
        response = requests.get(URL_APPSCRIPT, timeout=10)
        if response.status_code == 200:
            return int(response.text) + 1
    except:
        return None

def registrar_en_nube(datos):
    try:
        response = requests.post(URL_APPSCRIPT, data=json.dumps(datos), timeout=15, allow_redirects=True)
        return response.status_code == 200 or "Exito" in response.text
    except: 
        return False

# --- ESTADO DE SESIÓN ---
if 'n_folio' not in st.session_state:
    folio_real = obtener_ultimo_folio()
    st.session_state.n_folio = folio_real if folio_real else 1

if 'carrito' not in st.session_state: st.session_state.carrito = []
if 'item_id' not in st.session_state: st.session_state.item_id = 0
if 'cliente_limpio' not in st.session_state: st.session_state.cliente_limpio = 0
if 'msg_exito' not in st.session_state: st.session_state.msg_exito = False
if 'bloqueo_envio' not in st.session_state: st.session_state.bloqueo_envio = False

# --- FUNCIÓN PDF ---
def generar_pdf_pro(n_folio, nombre_cliente, carrito):
    pdf = FPDF()
    pdf.add_page()
    pdf.set_font("Arial", 'B', 20)
    pdf.cell(200, 15, txt='Persianas Steven', ln=True, align='C')
    pdf.ln(10)
    pdf.set_font("Arial", 'B', 12)
    pdf.cell(100, 10, txt=f"Cotizacion No: {n_folio}")
    pdf.cell(100, 10, txt=f"Fecha: {fecha_hoy}", ln=True, align='R')
    pdf.set_font("Arial", '', 12)
    pdf.cell(200, 10, txt=f"Cliente: {nombre_cliente}", ln=True)
    pdf.ln(5)
    
    pdf.set_fill_color(230, 230, 230)
    pdf.set_font("Arial", 'B', 9)
    pdf.cell(80, 10, u"Descripcion", border=1, fill=True, align='C')
    pdf.cell(15, 10, "U.M", border=1, fill=True, align='C')
    pdf.cell(35, 10, "Precio Unit.", border=1, fill=True, align='C')
    pdf.cell(15, 10, "Cant.", border=1, fill=True, align='C')
    pdf.cell(45, 10, "Subtotal", border=1, fill=True, align='C', ln=True)
    
    pdf.set_font("Arial", size=9)
    subtotal_acumulado = 0
    for item in carrito:
        pdf.cell(80, 10, item['descripcion'], border=1)
        pdf.cell(15, 10, item['unidad'], border=1, align='C')
        pdf.cell(35, 10, f"${item['valor_item']:,.0f}", border=1, align='R')
        pdf.cell(15, 10, str(item['cantidad']), border=1, align='C')
        pdf.cell(45, 10, f"${item['subtotal_item']:,.0f}", border=1, align='R', ln=True)
        subtotal_acumulado += item['subtotal_item']
    
    impuesto = subtotal_acumulado * 0.07
    total_gral = subtotal_acumulado + impuesto
    
    pdf.ln(5)
    pdf.set_font("Arial", 'B', 10)
    pdf.cell(145, 8, "SUBTOTAL:", align='R')
    pdf.cell(45, 8, f"${subtotal_acumulado:,.0f}", border=1, ln=True, align='R')
    pdf.cell(145, 8, "IMPUESTO (7%):", align='R')
    pdf.cell(45, 8, f"${impuesto:,.0f}", border=1, ln=True, align='R')
    pdf.set_fill_color(240, 240, 240)
    pdf.cell(145, 10, "TOTAL COTIZADO:", align='R')
    pdf.cell(45, 10, f"${total_gral:,.0f}", border=1, ln=True, align='R', fill=True)
    
    return pdf.output(dest='S').encode('latin-1'), total_gral

# --- INTERFAZ ---
st.markdown('<link href="https://fonts.googleapis.com/icon?family=Material+Icons" rel="stylesheet">', unsafe_allow_html=True)
st.markdown("<h1 style='display: flex; align-items: center;'><i class='material-icons' style='font-size: 45px; margin-right: 15px; color: #4F8BF9;'>window</i>Persianas Steven</h1>", unsafe_allow_html=True)

if st.session_state.msg_exito:
    st.success("✅ ¡Registro Enviado al Drive con éxito!")
    st.session_state.msg_exito = False

input_cliente = st.text_input("Nombre del Cliente", placeholder="Ej: PABLO PEREZ", key=f"cli_{st.session_state.cliente_limpio}")
cliente = input_cliente.upper()
st.write(f"Folio Actual: **#{st.session_state.n_folio}**")
st.divider()

# DATOS ÍTEM
usar_pulgadas = st.toggle("📐 Usar Pulgadas (in)", value=False, key=f"pulg_{st.session_state.item_id}")
unidad_m = "in" if usar_pulgadas else "m"

col1, col2 = st.columns(2)
with col1:
    ancho = st.number_input(f"Ancho ({unidad_m})", min_value=0.0, step=0.01, format="%.2f", value=None, placeholder="0.00", key=f"anc_{st.session_state.item_id}")
    tipo_tela = st.selectbox("Tipo de Tela", ["Blackout", "Screen", "Sheer Elegance"], index=None, placeholder="Seleccione tela...", key=f"tel_{st.session_state.item_id}")
with col2:
    largo = st.number_input(f"Largo ({unidad_m})", min_value=0.0, step=0.01, format="%.2f", value=None, placeholder="0.00", key=f"lar_{st.session_state.item_id}")
    motor = st.radio("Accionamiento", ["Manual", "Motorizada"], key=f"mot_{st.session_state.item_id}")

cantidad = st.number_input("Cantidad", min_value=1, step=1, value=1, key=f"can_{st.session_state.item_id}")

if ancho and largo and tipo_tela and cantidad:
    factor = 0.0254 if usar_pulgadas else 1.0
    area_f = (ancho * factor * largo * factor) * 1.15
    precios = {"Blackout": 48000, "Screen": 58000, "Sheer Elegance": 88000}
    p_unit = (area_f * precios[tipo_tela]) + (165000 if motor == "Motorizada" else 0)
    sub_total_item = p_unit * cantidad
    
    st.info(f"Área facturable (con 15% desp.): {area_f:.2f} m²")
    st.success(f"## Subtotal Ítem: ${sub_total_item:,.0f}")
    
    if st.button("➕ Agregar al carrito"):
        st.session_state.carrito.append({
            "descripcion": f"{tipo_tela} ({ancho}x{largo}{unidad_m}) {motor}",
            "unidad": unidad_m,
            "cantidad": cantidad,
            "valor_item": p_unit,
            "subtotal_item": sub_total_item
        })
        st.session_state.item_id += 1
        st.rerun()

# RESUMEN Y REGISTRO
if st.session_state.carrito:
    st.divider()
    df_resumen = pd.DataFrame(st.session_state.carrito)
    total_c = df_resumen['subtotal_item'].sum() * 1.07
    
    df_mostrar = pd.DataFrame()
    df_mostrar['Folio'] = [st.session_state.n_folio] * len(df_resumen)
    df_mostrar['Fecha'] = fecha_hoy
    df_mostrar['Cliente'] = cliente
    df_mostrar['Descripción'] = df_resumen['descripcion']
    df_mostrar['U.M'] = df_resumen['unidad']
    df_mostrar['Cantidad'] = df_resumen['cantidad']
    df_mostrar['Valor ítem'] = df_resumen['valor_item'].map('${:,.0f}'.format)
    df_mostrar['Total cotización'] = f"${total_c:,.0f}"
    
    st.table(df_mostrar)
    
    pdf_out, total_f = generar_pdf_pro(st.session_state.n_folio, cliente, st.session_state.carrito)
    st.download_button("📩 Descargar PDF", data=pdf_out, file_name=f"Cotización-{st.session_state.n_folio}.pdf", mime="application/pdf", use_container_width=True)

    if not st.session_state.bloqueo_envio:
        if st.button("💾 REGISTRAR Y LIMPIAR TODO", use_container_width=True, type="primary"):
            st.session_state.bloqueo_envio = True
            st.rerun()
    else:
        st.info("⏳ Enviando información al Drive... Por favor espere.")
        datos_nube = {
            "folio": st.session_state.n_folio,
            "fecha": fecha_hoy,
            "cliente": cliente if cliente else "CONSUMIDOR FINAL",
            "items_detalle": st.session_state.carrito,
            "total_general": total_f
        }
        if registrar_en_nube(datos_nube):
            st.session_state.msg_exito = True
            st.session_state.carrito = []
            st.session_state.cliente_limpio += 1
            st.session_state.n_folio += 1 
            st.session_state.bloqueo_envio = False
            st.rerun()
        else:
            st.error("Error al registrar. Intente de nuevo.")
            st.session_state.bloqueo_envio = False
