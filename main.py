import os
import requests
from flask import Flask, request, jsonify

app = Flask(__name__)

TELEGRAM_TOKEN = os.environ.get("TELEGRAM_TOKEN")
CHAT_ID = os.environ.get("CHAT_ID")

def enviar_telegram(mensaje):
    """Envía mensajes a tu Telegram mediante la API oficial"""
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

def motor_analisis_v24(partido):
    """
    Motor analítico con Metaprompt v24.0
    Filtros: MS >= 2.0 | Cuota >= 1.50
    """
    try:
        prom_a = float(partido["promedio_a"])
        prom_b = float(partido["promedio_b"])
        linea_casa = float(partido["linea_casa"])
        cuota = float(partido["cuota"])
        tipo_mercado = partido.get("tipo_mercado", "Over")
        mercado_nombre = partido.get("mercado_nombre", "Córners")
    except (KeyError, ValueError):
        return None

    pe = prom_a + prom_b

    # Cálculo del Margen de Seguridad
    if tipo_mercado == "Over":
        ms = pe - linea_casa
    else:
        ms = linea_casa - pe

    # Regla estricta NO-GO
    if ms < 2.0 or cuota < 1.50:
        return None

    # Clasificación del Stake
    if ms >= 3.0:
        veredicto = "🟢 APUESTA FUERTE (GO)"
        stake = "2.0% de la Banca"
    else:
        veredicto = "🟡 APUESTA MODERADA (GO CON CAUTELA)"
        stake = "1.0% de la Banca"

    alerta = (
        f"🚨 <b>¡OPORTUNIDAD DETECTADA (+EV)!</b> 🚨\n\n"
        f"⚽ <b>Partido:</b> {partido['equipo_a']} vs {partido['equipo_b']}\n"
        f"📌 <b>Mercado:</b> {mercado_nombre} ({tipo_mercado} {linea_casa})\n"
        f"📈 <b>PE Esperado:</b> {round(pe, 2)} | <b>Margen (MS):</b> +{round(ms, 2)}\n"
        f"💰 <b>Cuota:</b> {cuota} | <b>Stake:</b> {stake}\n"
        f"📊 <b>Veredicto:</b> {veredicto}"
    )
    return alerta

@app.route("/")
def home():
    return "Football Analytics Bot (Supremacy CORE ∞) está activo y monitoreando."

@app.route("/analizar-lote", methods=["POST"])
def analizar_lote():
    """Ruta para recibir y evaluar listas completas de partidos reales"""
    datos = request.get_json()
    
    if not datos or "partidos" not in datos:
        return jsonify({"error": "Formato JSON inválido. Se requiere la clave 'partidos'."}), 400

    partidos = datos["partidos"]
    oportunidades_encontradas = 0

    for partido in partidos:
        alerta = motor_analisis_v24(partido)
        if alerta:
            enviar_telegram(alerta)
            oportunidades_encontradas += 1

    return jsonify({
        "status": "exito",
        "partidos_evaluados": len(partidos),
        "oportunidades_notificadas": oportunidades_encontradas
    })

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port)
