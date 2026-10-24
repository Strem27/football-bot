import os
from flask import Flask
from datetime import datetime

app = Flask(__name__)

@app.route("/")
def home():
    return "Football Analytics Bot (Supremacy CORE ∞) is active and monitoring."

def motor_analisis_v24(partido_info):
    """
    Motor matemático basado en el Metaprompt v24.0
    """
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
        return None  # Descartado (NO-GO)

    # Clasificación de Apuesta
    if ms >= 3.0:
        veredicto = "🟢 APUESTA FUERTE (GO)"
        stake = "2.0% de la Banca"
    else:
        veredicto = "🟡 APUESTA MODERADA (GO CON CAUTELA)"
        stake = "1.0% de la Banca"

    alerta = (
        f"🚨 ¡OPORTUNIDAD DETECTADA (+EV)! 🚨\n\n"
        f"⚽ Partido: {partido_info['equipo_a']} vs {partido_info['equipo_b']}\n"
        f"📊 Mercado: {tipo_mercado} {linea_casa}\n"
        f"📈 PE Esperado: {pe} | Margen (MS): +{round(ms, 2)}\n"
        f"💰 Cuota: {cuota} | Stake: {stake}\n"
        f"📌 Veredicto: {veredicto}\n"
    )
    return alerta

@app.route("/probar-analisis")
def probar_analisis():
    # Ruta de prueba temporal para ver el motor en acción desde el navegador
    partido_prueba = {
        "equipo_a": "Real Madrid", "equipo_b": "Barcelona",
        "promedio_a": 7.2, "promedio_b": 5.8,
        "tipo_mercado": "Over", "linea_casa": 9.5, "cuota": 1.75
    }
    resultado = motor_analisis_v24(partido_prueba)
    if resultado:
        return f"<pre>{resultado}</pre>"
    else:
        return "El partido analizado dio NO-GO (Filtros estrictos aplicados correctamente)."

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port)