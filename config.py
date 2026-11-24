"""
Configuração do bot — todas as variáveis vêm de env vars no Render.
Nunca commite tokens/secrets no código.
"""
import os
import json

# Telegram
TELEGRAM_TOKEN = os.environ["TELEGRAM_TOKEN"]

# Gemini (Google AI Studio — grátis)
GEMINI_API_KEY = os.environ["GEMINI_API_KEY"]

# Google Sheets
SPREADSHEET_ID = os.environ["SPREADSHEET_ID"]
# JSON das credenciais do service account (string inteira no env var)
GOOGLE_CREDENTIALS_JSON = os.environ["GOOGLE_CREDENTIALS_JSON"]

# Render
RENDER_URL = os.environ.get("RENDER_EXTERNAL_URL", "")
PORT = int(os.environ.get("PORT", 10000))

# Segurança do webhook
WEBHOOK_SECRET = os.environ.get("WEBHOOK_SECRET", "sonar-bot-2024")

# Mapeamento parceiro: telegram_user_id → {nome: "nome do cara"}
# Exemplo: {"123456789": {"nome": "Eduardo"}, "987654321": {"nome": "Erick"}}
PARTNERS = json.loads(os.environ.get("PARTNERS", "{}"))
