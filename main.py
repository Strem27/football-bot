import os
import requests
from flask import Flask, request, jsonify

app = Flask(__name__)

TELEGRAM_TOKEN = os.environ.get("TELEGRAM_TOKEN")
CHAT_ID = os.environ.get("CHAT_ID")

def enviar_telegram(mensaje):
    if not TELEGRAM_TOKEN or not CHAT_ID:
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
        print(f"Error enviando mensaje: {e}")
        return False

def evaluar_multimercado_normalizado(partido):
    """
    Evalúa múltiples mercados normalizando la desviación porcentual (Eficiencia Relativa)
    para evitar el sesgo del volumen bruto (córners vs goles/tarjetas).
    """
    try:
        equipo_a = partido["equipo_a"]
        equipo_b = partido["equipo_b"]
        candidatos_mercados = partido.get("mercados", [])
    except KeyError:
        return None

    mejor_opcion = None
    mayor_eficiencia = -999.0

    for m in candidatos_mercados:
        try:
            prom_a = float(m["promedio_a"])
            prom_b = float(m["promedio_b"])
            linea_casa = float(m["linea_casa"])
            cuota = float(m["cuota"])
            mercado_nombre = m.get("mercado_nombre", "Mercado")
        except (KeyError, ValueError):
            continue

        pe = prom_a + prom_b

        # Decisión autónoma Over / Under
        if pe >= linea_casa:
            tipo_mercado = "Over"
            ms = pe - linea_casa
        else:
            tipo_mercado = "Under"
            ms = linea_casa - pe

        # Normalización por Eficiencia Relativa (%) para comparar peras con manzanas
        if linea_casa > 0:
            eficiencia_porcentual = (ms / linea_casa) * 100
        else:
            eficiencia_porcentual = 0.0

        # Filtros estrictos de calidad: MS absoluto >= 2.0 y Cuota >= 1.50
        if ms >= 2.0 and cuota >= 1.50:
            # Seleccionamos el mercado que tenga la mayor eficiencia porcentual real
            if eficiencia_porcentual > mayor_eficiencia:
                mayor_eficiencia = eficiencia_porcentual
                mejor_opcion = {
                    "mercado_nombre": mercado_nombre,
                    "tipo_mercado": tipo_mercado,
                    "linea_casa": linea_casa,
                    "pe": round(pe, 2),
                    "ms": round(ms, 2),
                    "eficiencia": round(eficiencia_porcentual, 1),
                    "cuota": cuota
                }

    if not mejor_opcion:
        return None

    if mejor_opcion["ms"] >= 3.0:
        veredicto = "🟢 APUESTA FUERTE (GO)"
        stake = "2.0% de la Banca"
    else:
        veredicto = "🟡 APUESTA MODERADA (GO CON CAUTELA)"
        stake = "1.0% de la Banca"

    alerta = (
        f"🚨 <b>¡VALOR +EV DETECTADO (NORMALIZADO)!</b> 🚨\n\n"
        f"⚽ <b>Partido:</b> {equipo_a} vs {equipo_b}\n"
        f"🎯 <b>Selección Óptima:</b> {mejor_opcion['mercado_nombre']} ({mejor_opcion['tipo_mercado']} {mejor_opcion['linea_casa']})\n"
        f"📈 <b>PE Esperado:</b> {mejor_opcion['pe']} | <b>Margen (MS):</b> +{mejor_opcion['ms']}\n"
        f"⚖️ <b>Eficiencia Relativa:</b> +{mejor_opcion['eficiencia']}%\n"
        f"💰 <b>Cuota:</b> {mejor_opcion['cuota']} | <b>Stake:</b> {stake}\n"
        f"📊 <b>Veredicto:</b> {veredicto}"
    )
    return alerta

@app.route("/")
def home():
    return "Normalized Autonomous Multimarket Football Bot Active."

@app.route("/analizar-lote", methods=["POST"])
def analizar_lote():
    datos = request.get_json()
    if not datos or "partidos" not in datos:
        return jsonify({"error": "JSON inválido"}), 400

    partidos = datos["partidos"]
    alertas_enviadas = 0

    for partido in partidos:
        alerta = evaluar_multimercado_normalizado(partido)
        if alerta:
            enviar_telegram(alerta)
            alertas_enviadas += 1

    return jsonify({
        "status": "exito",
        "partidos_evaluados": len(partidos),
        "alertas_enviadas": alertas_enviadas
    })

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port)
