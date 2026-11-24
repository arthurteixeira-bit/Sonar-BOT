"""
Handlers do Telegram — toda a lógica de conversa do bot.
Fluxos: áudio, texto, confirmação, correção, status, follow-up.
"""
import os
import logging
import tempfile

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    CommandHandler,
    MessageHandler,
    CallbackQueryHandler,
    ConversationHandler,
    ContextTypes,
    filters,
)

import config
import gemini_client
import sheets_client

logger = logging.getLogger(__name__)

# --- Estados da conversa ---
CONFIRMING = 0
CHOOSING_FIELD = 1
ENTERING_VALUE = 2

# Labels dos campos (pra mostrar nos botões de correção)
FIELD_LABELS = {
    "empresa": "📌 Empresa",
    "site": "🌐 Site",
    "cargo": "👤 Cargo",
    "status": "🎯 Status",
    "temperatura": "🌡️ Temperatura",
    "proximo_passo": "📅 Próximo passo",
    "data_proxima_acao": "📆 Data",
    "observacoes": "📝 Observações",
}


# =====================================================
# COMANDOS
# =====================================================

async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Comando /start — boas-vindas."""
    user_id = str(update.effective_user.id)
    partner = config.PARTNERS.get(user_id)

    if partner:
        await update.message.reply_text(
            f"Fala {partner['nome']}! 👋\n\n"
            "Sou o bot do Sonar. Me manda um áudio ou texto depois da reunião "
            "e eu atualizo o CRM pra você.\n\n"
            "📌 /status — ver resumo da sua lista\n"
            "❓ /ajuda — como usar"
        )
    else:
        await update.message.reply_text(
            "Não te reconheci. Fala com o Arthur pra te cadastrar. 🤷"
        )


async def cmd_ajuda(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Comando /ajuda ou /help."""
    await update.message.reply_text(
        "📌 Como usar:\n\n"
        "1. Saiu da reunião? Manda um áudio ou texto contando como foi.\n"
        "2. Eu extraio os dados e te mostro pra confirmar.\n"
        "3. Confirmou? Pronto, tá no CRM.\n\n"
        "💡 Dica: menciona empresa, como foi, próximo passo e quando."
    )


async def cmd_status(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Comando /status — resumo da lista do parceiro."""
    user_id = str(update.effective_user.id)
    partner = config.PARTNERS.get(user_id)

    if not partner:
        await update.message.reply_text(
            "Não te reconheci. Fala com o Arthur pra te cadastrar."
        )
        return

    try:
        summary = sheets_client.get_partner_summary(partner["nome"])

        if summary["total"] == 0:
            await update.message.reply_text(
                "Sua lista tá vazia ainda. Manda o primeiro update! 🚀"
            )
            return

        msg = f"📊 Sua lista, {partner['nome']}:\n\n"
        msg += f"Total: {summary['total']} empresas\n"
        msg += f"🔥 {summary['quente']} quentes\n"
        msg += f"🌡️ {summary['morno']} mornas\n"
        msg += f"❄️ {summary['frio']} frias\n"

        if summary["proxima_acao"]:
            dt, empresa, passo = summary["proxima_acao"]
            msg += f"\n📅 Próxima ação: {empresa} em {dt.strftime('%d/%m')}"
            if passo:
                msg += f" — {passo}"

        await update.message.reply_text(msg)

    except Exception as e:
        logger.error(f"Erro no /status: {e}")
        await update.message.reply_text("Erro ao buscar sua lista. Tenta de novo.")


# =====================================================
# FLUXO DE ATUALIZAÇÃO (áudio e texto)
# =====================================================

async def handle_voice(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Parceiro mandou áudio → transcreve + extrai com Gemini."""
    user_id = str(update.effective_user.id)
    partner = config.PARTNERS.get(user_id)

    if not partner:
        await update.message.reply_text(
            "Não te reconheci. Fala com o Arthur pra te cadastrar."
        )
        return ConversationHandler.END

    await update.message.reply_text("Processando seu áudio... ⏳")

    try:
        # Baixa o arquivo de áudio do Telegram
        voice = update.message.voice or update.message.audio
        tg_file = await context.bot.get_file(voice.file_id)

        with tempfile.NamedTemporaryFile(suffix=".ogg", delete=False) as tmp:
            await tg_file.download_to_drive(tmp.name)
            tmp_path = tmp.name

        # Manda pro Gemini (transcrição + extração num passo)
        data = gemini_client.extract_from_audio(tmp_path)

        # Limpa arquivo temporário
        os.unlink(tmp_path)

        return await _process_extraction(update, context, data, partner)

    except Exception as e:
        logger.error(f"Erro processando áudio: {e}")
        await update.message.reply_text(
            "Erro ao processar o áudio. Tenta mandar de novo? 🙏"
        )
        return ConversationHandler.END


async def handle_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Parceiro mandou texto → extrai dados com Gemini."""
    user_id = str(update.effective_user.id)
    partner = config.PARTNERS.get(user_id)

    if not partner:
        await update.message.reply_text(
            "Não te reconheci. Fala com o Arthur pra te cadastrar."
        )
        return ConversationHandler.END

    text = update.message.text

    # Detecta se é pergunta de status (Fluxo 5)
    status_keywords = [
        "minha lista", "meu status", "quantas empresas",
        "como tá", "como ta", "meu resumo", "minhas empresas",
    ]
    if any(kw in text.lower() for kw in status_keywords):
        await cmd_status(update, context)
        return ConversationHandler.END

    await update.message.reply_text("Processando... ⏳")

    try:
        data = gemini_client.extract_from_text(text)
        return await _process_extraction(update, context, data, partner)

    except Exception as e:
        logger.error(f"Erro processando texto: {e}")
        await update.message.reply_text(
            "Erro ao processar. Tenta mandar de novo? 🙏"
        )
        return ConversationHandler.END


async def _process_extraction(update, context, data, partner):
    """Processa resultado da extração (comum pra áudio e texto)."""
    # Erro: empresa não mencionada
    if data.get("erro") == "empresa_nao_mencionada":
        await update.message.reply_text(
            "Qual empresa foi essa reunião? Preciso pelo menos do nome da empresa."
        )
        return ConversationHandler.END

    # Erro: JSON inválido
    if data.get("erro") == "json_invalido":
        await update.message.reply_text(
            "Não consegui entender direito. Tenta explicar de novo? 🙏"
        )
        return ConversationHandler.END

    # Adiciona parceiro responsável
    data["parceiro"] = partner["nome"]

    # Guarda pra confirmação
    context.user_data["pending_data"] = data

    # Mostra pro parceiro confirmar
    await _show_confirmation(update, data)
    return CONFIRMING


# =====================================================
# CONFIRMAÇÃO E CORREÇÃO
# =====================================================

async def _show_confirmation(update, data):
    """Monta e envia mensagem de confirmação com botões."""
    msg = "Entendi, vou registrar:\n\n"
    msg += f"📌 Empresa: {data.get('empresa', '—')}\n"
    if data.get("site"):
        msg += f"🌐 Site: {data['site']}\n"
    if data.get("cargo"):
        msg += f"👤 Cargo: {data['cargo']}\n"
    msg += f"🎯 Status: {data.get('status', '—')}\n"
    msg += f"🌡️ Temperatura: {data.get('temperatura', '—')}\n"
    if data.get("proximo_passo"):
        msg += f"📅 Próximo passo: {data['proximo_passo']}"
        if data.get("data_proxima_acao"):
            msg += f" em {data['data_proxima_acao']}"
        msg += "\n"
    if data.get("observacoes"):
        msg += f"📝 Obs: {data['observacoes']}\n"
    msg += "\nTá certo?"

    keyboard = InlineKeyboardMarkup([
        [
            InlineKeyboardButton("✅ Confirmar", callback_data="confirm"),
            InlineKeyboardButton("✏️ Corrigir", callback_data="correct"),
        ]
    ])

    # Funciona tanto pra mensagem nova quanto pra edição (após correção)
    if hasattr(update, "callback_query") and update.callback_query:
        await update.callback_query.edit_message_text(msg, reply_markup=keyboard)
    else:
        await update.message.reply_text(msg, reply_markup=keyboard)


async def handle_confirmation(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Callback dos botões Confirmar/Corrigir."""
    query = update.callback_query
    await query.answer()

    if query.data == "confirm":
        data = context.user_data.get("pending_data")
        if not data:
            await query.edit_message_text("Dados perdidos. Manda o update de novo.")
            return ConversationHandler.END

        user_id = str(update.effective_user.id)
        partner = config.PARTNERS[user_id]

        try:
            action, _row = sheets_client.add_or_update_row(partner["nome"], data)
            emoji = "🔄" if action == "updated" else "✅"
            verb = "Atualizado" if action == "updated" else "Registrado"
            await query.edit_message_text(
                f"{emoji} {verb}! {data['empresa']} no CRM."
            )
        except Exception as e:
            logger.error(f"Erro salvando no Sheets: {e}")
            await query.edit_message_text(
                "Erro ao salvar no CRM. Tenta mandar o update de novo. 🙏"
            )

        context.user_data.pop("pending_data", None)
        return ConversationHandler.END

    elif query.data == "correct":
        # Mostra botões com cada campo pra escolher qual corrigir
        buttons = []
        for key, label in FIELD_LABELS.items():
            buttons.append(
                [InlineKeyboardButton(label, callback_data=f"field_{key}")]
            )
        buttons.append(
            [InlineKeyboardButton("🔙 Voltar", callback_data="back")]
        )

        await query.edit_message_text(
            "Qual campo quer corrigir?",
            reply_markup=InlineKeyboardMarkup(buttons),
        )
        return CHOOSING_FIELD


async def handle_field_choice(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Callback da escolha de campo pra corrigir."""
    query = update.callback_query
    await query.answer()

    if query.data == "back":
        data = context.user_data.get("pending_data")
        await _show_confirmation(update, data)
        return CONFIRMING

    field = query.data.replace("field_", "")
    context.user_data["correcting_field"] = field
    label = FIELD_LABELS.get(field, field)

    await query.edit_message_text(f"Qual o valor correto pra {label}?")
    return ENTERING_VALUE


async def handle_correction(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Recebe o valor corrigido e mostra confirmação de novo."""
    field = context.user_data.get("correcting_field")
    data = context.user_data.get("pending_data")

    if not field or not data:
        await update.message.reply_text("Algo deu errado. Manda o update de novo.")
        return ConversationHandler.END

    # Atualiza o campo com o valor novo
    data[field] = update.message.text
    context.user_data["pending_data"] = data

    await _show_confirmation(update, data)
    return CONFIRMING


async def cmd_cancel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Comando /cancelar — cancela fluxo em andamento."""
    context.user_data.pop("pending_data", None)
    context.user_data.pop("correcting_field", None)
    await update.message.reply_text("Cancelado. Manda quando quiser atualizar. 👍")
    return ConversationHandler.END


# =====================================================
# FOLLOW-UP (chamado pelo cron)
# =====================================================

async def send_followups(bot):
    """
    Varre o Sheets, acha ações pendentes/atrasadas,
    manda lembrete soft pro parceiro no Telegram.
    """
    followups = sheets_client.get_pending_followups()
    sent = 0

    for f in followups:
        try:
            if f["atrasado"]:
                msg = (
                    f"Ei {f['nome']}, o próximo passo com {f['empresa']} "
                    f"era pra {f['data']}. Conseguiu avançar? "
                    f"Me conta como tá 🤙"
                )
            else:
                passo = f["proximo_passo"] or "ação"
                msg = (
                    f"Oi {f['nome']}! Hoje era dia de {passo} "
                    f"com a {f['empresa']}. "
                    f"Rolou? Manda um áudio ou texto com update quando puder 🤙"
                )

            await bot.send_message(chat_id=int(f["telegram_id"]), text=msg)
            sent += 1

        except Exception as e:
            logger.error(f"Erro enviando follow-up pra {f['nome']}: {e}")

    logger.info(f"Follow-ups enviados: {sent}/{len(followups)}")
    return sent


def build_conversation_handler() -> ConversationHandler:
    """Monta o ConversationHandler com todos os fluxos."""
    return ConversationHandler(
        entry_points=[
            MessageHandler(filters.VOICE | filters.AUDIO, handle_voice),
            MessageHandler(filters.TEXT & ~filters.COMMAND, handle_text),
        ],
        states={
            CONFIRMING: [CallbackQueryHandler(handle_confirmation)],
            CHOOSING_FIELD: [CallbackQueryHandler(handle_field_choice)],
            ENTERING_VALUE: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, handle_correction),
            ],
        },
        fallbacks=[
            CommandHandler("cancelar", cmd_cancel),
            CommandHandler("cancel", cmd_cancel),
        ],
    )
