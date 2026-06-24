# agentos/shared/telegram_channel.py
# Replaces WhatsApp Business API across VaakShastra, FieldPulse,
# GhostCFO, SupplyBrain, EchoLaw, etc.
# python-telegram-bot: pip install python-telegram-bot

import os
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    Application, CommandHandler, MessageHandler,
    CallbackQueryHandler, filters, ContextTypes
)
import httpx

TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
AGENTOS_GATEWAY_URL = os.getenv("AGENTOS_GATEWAY_URL", "http://gateway:8030")

async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = str(update.effective_user.id)
    text = update.message.text or ""
    
    # Voice message — transcribe via Groq Whisper
    if update.message.voice:
        voice_file = await update.message.voice.get_file()
        audio_bytes = await voice_file.download_as_bytearray()
        text = await transcribe_audio(audio_bytes)
        await update.message.reply_text(f"🎤 Heard: {text}")
    
    # Route to AgentOS gateway
    async with httpx.AsyncClient(timeout=30) as client:
        r = await client.post(
            f"{AGENTOS_GATEWAY_URL}/api/v1/dashboard/chat",
            json={"user_id": user_id, "request_text": text},
            headers={"Authorization": f"Bearer {user_id}"}
        )
        result = r.json()
    
    # Format response with inline keyboard for agent actions
    keyboard = [
        [InlineKeyboardButton(f"📊 View {agent}", callback_data=f"agent:{agent}")]
        for agent in result.get("dispatched_agents", [])[:3]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard) if keyboard else None
    
    await update.message.reply_text(
        result.get("summary", "Processing..."),
        reply_markup=reply_markup,
        parse_mode="Markdown"
    )

async def transcribe_audio(audio_bytes: bytes) -> str:
    """Transcribe voice via Groq Whisper (free)"""
    import tempfile, os
    with tempfile.NamedTemporaryFile(suffix=".ogg", delete=False) as f:
        f.write(audio_bytes)
        tmp_path = f.name
    try:
        from groq import Groq
        client = Groq(api_key=os.getenv("GROQ_API_KEY"))
        with open(tmp_path, "rb") as audio_file:
            transcription = client.audio.transcriptions.create(
                file=audio_file,
                model="whisper-large-v3",
                language="hi"  # Hindi default; auto-detects others
            )
        return transcription.text
    finally:
        os.unlink(tmp_path)

def build_telegram_app():
    app = Application.builder().token(TELEGRAM_BOT_TOKEN).build()
    app.add_handler(MessageHandler(filters.TEXT | filters.VOICE, handle_message))
    return app
