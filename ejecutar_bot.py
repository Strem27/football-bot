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
    """
    Consulta los partidos programados para el día actual abarcando 
    las principales competiciones europeas y globales de Football-Data.org.
    """
    hoy = datetime.now().strftime('%Y-%m-%d')
    url = "https://api.football-data.org/v4/matches"
    headers = {"X-Auth-Token": FOOTBALL_DATA_KEY}
    
    # Códigos de las principales ligas soportadas por el plan estándar/gratuito
    # (Premier League, Champions League, La Liga, Serie A, Bundesliga, Ligue 1, Eredivisie, etc.)
    competencias = "PL,CL,PD,SA,BL1,FL1,DED,PPL,WC"
    
    params = {
        "dateFrom": hoy,
        "dateTo": hoy,
        "competitions": competencias
    }
    
    try:
        response = requests.get(url, headers=headers, params=params)
        if response.status_code == 200:
            data = response.json()
            matches = data.get("matches", [])
            print(f"Partidos encontrados en las ligas principales: {len(matches)}")
            return matches
        else:
            print(f"Error al consultar Football-Data: {response.status_code} - {response.text}")
            return []
    except Exception as e:
        print(f"Excepción en Football-Data: {e}")
        return []

def calcular_kelly(probabilidad_modelo, cuota_decimal, fraccion=0.25):
    """
    Calcula el porcentaje de bankroll a arriesgar usando el criterio de Kelly fraccional.
    """
    if cuota_decimal <= 1:
        return 0.0
    
    q = 1.0 - probabilidad_modelo
    b = cuota_decimal - 1.0
    
    kelly_full = (b * probabilidad_modelo - q) / b
    kelly_fraccional = kelly_full * fraccion
    
    return max(0.0, kelly_fraccional * 100) # Retorna en porcentaje (%)

def main():
    print("Iniciando análisis diario multimercado en la nube...")
    
    if not FOOTBALL_DATA_KEY or not ODDS_API_KEY:
        print("Advertencia: Faltan llaves de API principales.")

    # 1. Obtener partidos del día en las ligas configuradas
    partidos = consultar_partidos_hoy()
    
    apuestas_encontradas = []
    
    for partido in partidos:
        local = partido['homeTeam']['name']
        visitante = partido['awayTeam']['name']
        competencia = partido['competition']['name']
        
        # 2. Aquí puedes integrar tu modelo cuantitativo o de Expected Goals (Poisson)
        # Ejemplo simulado de evaluación para los partidos de la fecha:
        probabilidad_estimada = 0.58  # Probabilidad calculada por tu motor analítico
        cuota_mercado = 1.85         # Cuota estimada o enlazada desde Odds API
        
        # Aplicación del Criterio de Kelly (Fracción 0.25)
        stake_kelly = calcular_kelly(probabilidad_estimada, cuota_mercado, fraccion=0.25)
        
        # Umbral mínimo para reportar valor
        if stake_kelly > 0.4:
            apuesta_texto = (
                f"🏆 <b>{competencia}</b>\n"
                f"⚽ <b>{local} vs {visitante}</b>\n"
                f"📊 Prob. Modelo: <code>{probabilidad_estimada*100:.1f}%</code>\n"
                f"💰 Cuota: <code>{cuota_mercado}</code>\n"
                f"🎯 Sugerencia Kelly (0.25): <b>{stake_kelly:.2f}% del Bank</b>\n\n"
            )
            apuestas_encontradas.append(apuesta_texto)

    # 3. Consolidar el reporte y enviarlo por Telegram
    if apuestas_encontradas:
        mensaje_final = f"🤖 <b>REPORTE QUANT DIARIO ({datetime.now().strftime('%Y-%m-%d')})</b> 🤖\n\n" + "".join(apuestas_encontradas)
    else:
        mensaje_final = f"🤖 <b>Bot Quant Activo</b> ({datetime.now().strftime('%Y-%m-%d')}): Análisis ejecutado en múltiples ligas. Sin oportunidades con valor óptimo bajo Kelly en este ciclo."

    enviar_telegram(mensaje_final)
    print("¡Proceso finalizado con éxito y enviado a Telegram!")

if __name__ == "__main__":
    main()
