"""
Ponto de entrada — servidor web (Starlette + uvicorn) que:
1. Recebe webhooks do Telegram em POST /webhook
2. Recebe cron de follow-up em GET /followup?secret=xxx
3. Health check em GET /health (Render usa pra saber se tá vivo)
"""
import asyncio
import logging
from contextlib import asynccontextmanager

from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import PlainTextResponse
from starlette.routing import Route

import uvicorn
from telegram import Update
from telegram.ext import Application, CommandHandler

import config
import gemini_client
from bot import (
    cmd_start,
    cmd_ajuda,
    cmd_status,
    build_conversation_handler,
    send_followups,
)

logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO,
)
logger = logging.getLogger(__name__)

# Aplicação Telegram global
tg_app: Application = None


# =====================================================
# ENDPOINTS
# =====================================================

async def webhook(request: Request):
    """Recebe update do Telegram e processa."""
    # Valida secret token (segurança)
    secret = request.headers.get("X-Telegram-Bot-Api-Secret-Token", "")
    if secret != config.WEBHOOK_SECRET:
        return PlainTextResponse("Unauthorized", status_code=401)

    data = await request.json()
    update = Update.de_json(data, tg_app.bot)
    await tg_app.process_update(update)
    return PlainTextResponse("OK")


async def followup(request: Request):
    """
    Endpoint pro cron-job.org bater toda manhã.
    Manda follow-ups pra parceiros com ações pendentes.
    """
    secret = request.query_params.get("secret", "")
    if secret != config.WEBHOOK_SECRET:
        return PlainTextResponse("Unauthorized", status_code=401)

    # Responde rápido e processa em background
    # (evita timeout do cron-job.org)
    asyncio.create_task(_run_followups())
    return PlainTextResponse("OK")


async def _run_followups():
    """Roda follow-ups em background."""
    try:
        count = await send_followups(tg_app.bot)
        logger.info(f"Cron de follow-up executado: {count} mensagens")
    except Exception as e:
        logger.error(f"Erro no cron de follow-up: {e}")


async def health(request: Request):
    """Health check — Render usa pra monitorar o serviço."""
    return PlainTextResponse("OK")


# =====================================================
# LIFECYCLE
# =====================================================

@asynccontextmanager
async def lifespan(app):
    """Inicializa bot na subida, limpa na descida."""
    global tg_app

    # Configura Gemini
    gemini_client.configure(config.GEMINI_API_KEY)
    logger.info("Gemini configurado")

    # Monta aplicação Telegram (sem updater — webhook manual)
    tg_app = (
        Application.builder()
        .token(config.TELEGRAM_TOKEN)
        .updater(None)  # Não usa polling, webhook é manual
        .build()
    )

    # Registra handlers
    tg_app.add_handler(CommandHandler("start", cmd_start))
    tg_app.add_handler(CommandHandler("status", cmd_status))
    tg_app.add_handler(CommandHandler("ajuda", cmd_ajuda))
    tg_app.add_handler(CommandHandler("help", cmd_ajuda))
    tg_app.add_handler(build_conversation_handler())

    # Inicializa
    await tg_app.initialize()
    await tg_app.start()

    # Configura webhook no Telegram
    if config.RENDER_URL:
        webhook_url = f"{config.RENDER_URL}/webhook"
        await tg_app.bot.set_webhook(
            url=webhook_url,
            secret_token=config.WEBHOOK_SECRET,
        )
        logger.info(f"Webhook configurado: {webhook_url}")
    else:
        logger.warning("RENDER_EXTERNAL_URL não definida — webhook não configurado")

    yield  # App rodando

    # Shutdown
    await tg_app.stop()
    await tg_app.shutdown()
    logger.info("Bot encerrado")


# =====================================================
# APP
# =====================================================

routes = [
    Route("/webhook", webhook, methods=["POST"]),
    Route("/followup", followup, methods=["GET"]),
    Route("/health", health, methods=["GET"]),
]

app = Starlette(routes=routes, lifespan=lifespan)


if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=config.PORT)
