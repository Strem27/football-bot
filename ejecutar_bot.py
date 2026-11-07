import os
import requests
from rapidfuzz import process, fuzz

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
        return response.status_code == 200
    except Exception as e:
        print(f"Error enviando mensaje a Telegram: {e}")
        return False

def main():
    print("Iniciando análisis diario de partidos en la nube...")
    
    if not FOOTBALL_DATA_KEY or not ODDS_API_KEY:
        print("Advertencia: Faltan las llaves de las APIs de fútbol u odds.")

    # 1. Aquí colocas la llamada a tu lógica de consulta de partidos
    # (por ejemplo, tu función que consulta Football-Data y The Odds API)
    
    # 2. Aquí ejecutas tu evaluación multimercado y cálculo de Kelly (0.25)
    
    # 3. Envias el resultado consolidado
    mensaje_prueba = "🤖 <b>Bot Quant Activo</b>: Análisis diario ejecutado correctamente desde GitHub Actions."
    enviar_telegram(mensaje_prueba)
    
    print("¡Proceso finalizado con éxito y enviado a Telegram!")

if __name__ == "__main__":
    main()
