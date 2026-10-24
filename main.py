import os
import requests
from flask import Flask

app = Flask(__name__)

# Recuperamos las credenciales guardadas en las Variables de Entorno de Render
TELEGRAM_TOKEN = os.environ.get("TELEGRAM_TOKEN")
CHAT_ID = os.environ.get("CHAT_ID")

def enviar_telegram(mensaje):
    """Función para enviar alertas a tu Telegram"""
    if not TELEGRAM_TOKEN or not CHAT_ID:
        print("Faltan las credenciales de Telegram en Render.")
        return False
        
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    payload = {
        "chat_id": CHAT_ID,
        "text": mensaje,
        "parse_mode": "HTML"
    }
    
    try:
        response = requests.post(url, json=payload)
        return response.status_code == 200
    except Exception as e:
        print(f"Error enviando mensaje a Telegram: {e}")
        return False

def motor_analisis_v24(partido_info):
    """Motor matemático basado en Metaprompt v24.0"""
    pe = partido_info["promedio_a"] + partido_info["promedio_b"]
    tipo_mercado = partido_info["tipo_mercado"]
    linea_casa = partido_info["linea_casa"]
    cuota = partido_info["cuota"]

    # Cálculo del Margen de Seguridad (MS)
    if tipo_mercado == "Over":
        ms = pe - linea_casa
    else:
        ms = linea_casa - pe

    # Regla estricta: Margen mínimo 2.0 y cuota >= 1.50
    if ms < 2.0 or cuota < 1.50:
        return None

    # Clasificación de Apuesta
    if ms >= 3.0:
        veredicto = "🟢 APUESTA FUERTE (GO)"
        stake = "2.0% de la Banca"
    else:
        veredicto = "🟡 APUESTA MODERADA (GO CON CAUTELA)"
        stake = "1.0% de la Banca"

    alerta = (
        f"🚨 <b>¡OPORTUNIDAD DETECTADA (+EV)!</b> 🚨\n\n"
        f"⚽ <b>Partido:</b> {partido_info['equipo_a']} vs {partido_info['equipo_b']}\n"
        f"📊 <b>Mercado:</b> {tipo_mercado} {linea_casa}\n"
        f"📈 <b>PE Esperado:</b> {pe} | <b>Margen (MS):</b> +{round(ms, 2)}\n"
        f"💰 <b>Cuota:</b> {cuota} | <b>Stake:</b> {stake}\n"
        f"📌 <b>Veredicto:</b> {veredicto}"
    )
    return alerta

@app.route("/")
def home():
    return "Football Analytics Bot (Supremacy CORE ∞) is active and monitoring."

@app.route("/probar-analisis")
def probar_analisis():
    partido_prueba = {
        "equipo_a": "Real Madrid", "equipo_b": "Barcelona",
        "promedio_a": 7.2, "promedio_b": 5.8,
        "tipo_mercado": "Over", "linea_casa": 9.5, "cuota": 1.75
    }
    
    alerta = motor_analisis_v24(partido_prueba)
    
    if alerta:
        exito = enviar_telegram(alerta)
        if exito:
            return "✅ ¡Alerta procesada y enviada a Telegram con éxito!"
        else:
            return "⚠️ El análisis dio GO, pero falló el envío a Telegram. Revisa las variables TOKEN y CHAT_ID en Render."
    else:
        return "El partido analizado dio NO-GO (Filtros estrictos aplicados correctamente)."

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port)
