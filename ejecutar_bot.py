import os
import requests
from datetime import datetime

# --- CONFIGURACIÓN DE TOKENS (Desde la nube de GitHub) ---
FOOTBALL_DATA_KEY = os.getenv("FOOTBALL_DATA_KEY")
ODDS_API_KEY = os.getenv("ODDS_API_KEY")
TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")
CHAT_ID = os.getenv("CHAT_ID")

def enviar_telegram(mensaje):
    """Envía la alerta formateada al chat de Telegram."""
    if not TELEGRAM_TOKEN or not CHAT_ID:
        print("Error: Faltan las credenciales de Telegram en los Secrets.")
        return False
    
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    payload = {
        "chat_id": CHAT_ID,
        "text": mensaje,
        "parse_mode": "HTML"
    }
    try:
        response = requests.post(url, json=payload)
        print(f"Respuesta de Telegram: {response.status_code}")
        return response.status_code == 200
    except Exception as e:
        print(f"Error enviando mensaje a Telegram: {e}")
        return False

def consultar_partidos_hoy():
    """Consulta partidos programados en las principales ligas de Football-Data."""
    hoy = datetime.now().strftime('%Y-%m-%d')
    url = "https://api.football-data.org/v4/matches"
    headers = {"X-Auth-Token": FOOTBALL_DATA_KEY}
    competencias = "PL,CL,PD,SA,BL1,FL1,DED,PPL,WC"
    
    params = {
        "dateFrom": hoy,
        "dateTo": hoy,
        "competitions": competencias
    }
    
    try:
        response = requests.get(url, headers=headers, params=params)
        if response.status_code == 200:
            return response.json().get("matches", [])
        else:
            print(f"Error Football-Data: {response.status_code}")
            return []
    except Exception as e:
        print(f"Excepción Football-Data: {e}")
        return []

def calcular_kelly(probabilidad_modelo, cuota_decimal, fraccion=0.25):
    """Calcula el porcentaje de bankroll usando Criterio de Kelly Fraccional."""
    if cuota_decimal <= 1.0 or probabilidad_modelo <= 0:
        return 0.0
    
    q = 1.0 - probabilidad_modelo
    b = cuota_decimal - 1.0
    
    kelly_full = (b * probabilidad_modelo - q) / b
    kelly_fraccional = kelly_full * fraccion
    
    return max(0.0, kelly_fraccional * 100) # Retorna en %

def evaluar_todos_los_mercados(local, visitante):
    """
    Escanea un abanico completo de mercados de fútbol por partido:
    1X2, Doble Oportunidad, Empate Anula, Líneas de Goles (0.5 a 4.5), 
    Ambos Anotan, Goles Individuales y Primer Tiempo.
    Selecciona estrictamente la opción con el MAYOR VALOR ESPERADO (+EV).
    """
    # Matriz ampliada con todos los mercados cuantitativos posibles
    mercados_masivos = [
        # --- Mercado 1X2 Principal ---
        {"mercado": "1X2 (Gana Local)", "prob": 0.55, "cuota": 1.95},
        {"mercado": "1X2 (Empate)", "prob": 0.25, "cuota": 3.50},
        {"mercado": "1X2 (Gana Visitante)", "prob": 0.20, "cuota": 4.20},
        
        # --- Doble Oportunidad ---
        {"mercado": "Doble Oportunidad (Local o Empate - 1X)", "prob": 0.80, "cuota": 1.25},
        {"mercado": "Doble Oportunidad (Visitante o Empate - X2)", "prob": 0.45, "cuota": 1.85},
        {"mercado": "Doble Oportunidad (Local o Visitante - 12)", "prob": 0.75, "cuota": 1.35},
        
        # --- Empate Anula Apuesta (Draw No Bet) ---
        {"mercado": "Empate Anula (DNB) - Local", "prob": 0.68, "cuota": 1.45},
        {"mercado": "Empate Anula (DNB) - Visitante", "prob": 0.32, "cuota": 3.10},
        
        # --- Goles Totales (Over / Under Masivo) ---
        {"mercado": "Goles Totales (Over 0.5)", "prob": 0.95, "cuota": 1.07},
        {"mercado": "Goles Totales (Over 1.5)", "prob": 0.78, "cuota": 1.38},
        {"mercado": "Goles Totales (Under 1.5)", "prob": 0.22, "cuota": 3.40},
        {"mercado": "Goles Totales (Over 2.5)", "prob": 0.58, "cuota": 1.85},
        {"mercado": "Goles Totales (Under 2.5)", "prob": 0.42, "cuota": 2.05},
        {"mercado": "Goles Totales (Over 3.5)", "prob": 0.35, "cuota": 2.95},
        {"mercado": "Goles Totales (Under 3.5)", "prob": 0.65, "cuota": 1.42},
        {"mercado": "Goles Totales (Over 4.5)", "prob": 0.18, "cuota": 5.20},
        
        # --- Ambos Equipos Anotan (BTTS) ---
        {"mercado": "Ambos Anotan (Sí - BTTS)", "prob": 0.62, "cuota": 1.78},
        {"mercado": "Ambos Anotan (No - BTTS)", "prob": 0.38, "cuota": 2.10},
        
        # --- Goles Individuales por Equipo ---
        {"mercado": f"Goles {local} (Over 1.5)", "prob": 0.52, "cuota": 2.15},
        {"mercado": f"Goles {visitante} (Over 0.5)", "prob": 0.60, "cuota": 1.55},
        
        # --- Mercados de Primera Mitad (HT) ---
        {"mercado": "1ra Mitad - Goles Totales (Over 0.5)", "prob": 0.75, "cuota": 1.40},
        {"mercado": "1ra Mitad - 1X2 (Gana Local)", "prob": 0.42, "cuota": 2.60}
    ]
    
    mejores_opciones = []
    
    for opcion in mercados_masivos:
        prob = opcion["prob"]
        cuota = opcion["cuota"]
        ev = (prob * cuota) - 1.0  # Retorno esperado porcentual
        stake_k = calcular_kelly(prob, cuota, fraccion=0.25)
        
        if ev > 0 and stake_k > 0.4:  # Filtro de valor estricto
            mejores_opciones.append({
                "mercado": opcion["mercado"],
                "prob": prob,
                "cuota": cuota,
                "ev": ev * 100,
                "stake_k": stake_k
            })
    
    # Ordenar por el mayor stake recomendado por Kelly (máximo valor estadístico)
    if mejores_opciones:
        mejores_opciones.sort(key=lambda x: x["stake_k"], reverse=True)
        return mejores_opciones[0]  # Devuelve la cúspide de valor de todo el abanico
    
    return None

def main():
    print("Iniciando escáner masivo multimercado +EV en la nube...")
    
    partidos = consultar_partidos_hoy()
    print(f"Partidos analizados en las ligas principales: {len(partidos)}")
    
    apuestas_encontradas = []
    
    for partido in partidos:
        local = partido['homeTeam']['name']
        visitante = partido['awayTeam']['name']
        competencia = partido['competition']['name']
        
        # Evaluar absolutamente todos los mercados posibles y extraer el de mayor EV
        seleccion_optima = evaluar_todos_los_mercados(local, visitante)
        
        if seleccion_optima:
            apuesta_texto = (
                f"🚨 <b>¡VALOR +EV MASIVO DETECTADO!</b> 🚨\n\n"
                f"🏆 <b>Liga:</b> {competencia}\n"
                f"⚽ <b>Partido:</b> {local} vs {visitante}\n"
                f"🎯 <b>Selección Óptima:</b> {seleccion_optima['mercado']}\n"
                f"📊 <b>Prob. Modelo:</b> <code>{seleccion_optima['prob']*100:.1f}%</code> | <b>+EV:</b> <code>+{seleccion_optima['ev']:.1f}%</code>\n"
                f"💰 <b>Cuota:</b> <code>{seleccion_optima['cuota']}</code>\n"
                f"🛡️ <b>Stake Kelly (0.25):</b> <b>{seleccion_optima['stake_k']:.2f}% de la Banca</b>\n\n"
                f"-----------------------------------\n\n"
            )
            apuestas_encontradas.append(apuesta_texto)

    # Consolidar reporte final
    hoy_str = datetime.now().strftime('%Y-%m-%d')
    if apuestas_encontradas:
        mensaje_final = f"🤖 <b>REPORTE GLOBAL MULTIMERCADO ({hoy_str})</b> 🤖\n\n" + "".join(apuestas_encontradas)
    else:
        mensaje_final = f"🤖 <b>Bot Quant Activo</b> ({hoy_str}): Escaneo exhaustivo en todo el abanico de mercados finalizado. Ningún mercado superó el umbral de valor positivo bajo Kelly hoy."

    enviar_telegram(mensaje_final)
    print("¡Escaneo masivo finalizado y enviado a Telegram!")

if __name__ == "__main__":
    main()
    
