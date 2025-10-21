# Contenuto completo del file corretto 'telegram_bot.py'

import logging
import json
import io
import sys
import threading
import asyncio
from telegram import Update
from telegram.ext import Application, MessageHandler, filters, ContextTypes, JobQueue

import Alumen

# --- Variabili Globali per il Bot ---
bot_app = None
CHAT_ID = None

# --- Gestore di Log Personalizzato per Telegram ---

class TelegramLogHandler(logging.Handler):
    def __init__(self, application: Application, chat_id: str):
        super().__init__()
        self.application = application
        self.chat_id = chat_id

    def emit(self, record: logging.LogRecord):
        if (
            "httpx" in record.name 
            or "telegram" in record.name 
            or "apscheduler" in record.name
        ):
            return
            
        log_entry = self.format(record)
        
        if self.application.job_queue:
            self.application.job_queue.run_once(
                lambda context: context.bot.send_message(chat_id=self.chat_id, text=log_entry),
                0
            )

# --- Gestore Generico dei Comandi ---

async def generic_command_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    Cattura qualsiasi messaggio di testo, lo passa al processore di comandi
    principale e invia la stringa di testo formattata come risposta.
    """
    user = update.effective_user
    command_line = update.message.text
    
    Alumen.console.log(f"Comando Telegram '{command_line}' ricevuto da {user.username}")

    if command_line.startswith('/'):
        command_line = command_line[1:]

    try:
        # Chiama la funzione centralizzata con il flag per Telegram.
        # Questa funzione ora RESTITUISCE una stringa formattata.
        output_string = Alumen.process_command(command_line, is_telegram=True)
        
        if not output_string or not output_string.strip():
            output_string = "✅ Comando eseguito."
            
    except Exception as e:
        output_string = f"🛑 Errore durante l'esecuzione del comando: {e}"

    # Invia la stringa pulita a Telegram, usando la formattazione Markdown
    await update.message.reply_text(output_string, parse_mode="Markdown")

# --- Funzione di Notifica ---

def send_telegram_notification(message: str):
    """Invia un messaggio di notifica asincrono a Telegram."""
    if bot_app and bot_app.job_queue:
        bot_app.job_queue.run_once(
            lambda context: context.bot.send_message(
                chat_id=CHAT_ID, text=message, parse_mode="Markdown"
            ),
            0
        )

# --- Funzioni Principali di Avvio e Arresto del Bot ---

def start_bot():
    global bot_app, CHAT_ID
    try:
        with open("telegram_config.json", "r") as f:
            config = json.load(f)
            token = config.get("bot_token")
            CHAT_ID = config.get("chat_id")
        if not token or not CHAT_ID or token == "TUO_TOKEN_SEGRETO_QUI":
            Alumen.console.print("[bold red]ERRORE:[/] Il file 'telegram_config.json' non è configurato correttamente.")
            return None
    except FileNotFoundError:
        Alumen.console.print("[bold red]ERRORE:[/] File 'telegram_config.json' non trovato.")
        return None
        
    Alumen.console.print("🤖 Avvio dell'integrazione con Telegram...")

    job_queue = JobQueue()
    builder = Application.builder().token(token)
    builder.job_queue(job_queue)
    bot_app = builder.build()

    telegram_handler = TelegramLogHandler(bot_app, CHAT_ID)
    formatter = logging.Formatter('ℹ️ %(message)s')
    telegram_handler.setFormatter(formatter)
    logging.getLogger().addHandler(telegram_handler)

    # Aggiungi un unico gestore per tutti i messaggi di testo
    bot_app.add_handler(MessageHandler(filters.TEXT, generic_command_handler))

    bot_thread = threading.Thread(target=bot_app.run_polling, daemon=True)
    bot_thread.start()
    
    Alumen.console.print("[bold green]✅ Bot Telegram attivo e in ascolto.[/]")
    
    bot_app.job_queue.run_once(
        lambda context: context.bot.send_message(chat_id=CHAT_ID, text="🚀 Script Alumen avviato! Il logging e i comandi sono attivi."),
        1
    )
    
    return bot_app

def stop_bot():
    """
    Arresta il bot di Telegram in modo controllato, gestendo i timeout di rete
    per evitare che lo script si blocchi durante la chiusura.
    """
    global bot_app
    if not bot_app:
        return

    # Controlla se il bot è effettivamente in esecuzione
    if not bot_app.updater or not bot_app.updater._running: # <--- MODIFICA QUI
        Alumen.console.print("ℹ️  Il bot di Telegram non era in esecuzione.", style="yellow")
        return

    Alumen.console.print("🤖 Arresto del bot Telegram in corso...", style="telegram")

    # Ottieni l'event loop asyncio del bot, che è già in esecuzione
    loop = bot_app.loop

    async def shutdown_with_timeout():
        """ Coroutine che esegue lo shutdown con un timeout di sicurezza. """
        try:
            # Invia il messaggio di chiusura
            if bot_app.job_queue:
                await bot_app.bot.send_message(chat_id=CHAT_ID, text="🛑 Script Alumen terminato.")
            
            # Diamo al processo di shutdown della libreria un massimo di 5 secondi per completarsi.
            await asyncio.wait_for(bot_app.shutdown(), timeout=5.0)
            Alumen.console.print("   - Shutdown pulito del bot completato.", style="telegram")
        except asyncio.TimeoutError:
            Alumen.console.print("⚠️  Timeout durante lo shutdown del bot. La chiusura potrebbe non essere pulita.", style="yellow")
        except Exception as e:
            Alumen.console.print(f"⚠️  Errore non gestito durante lo shutdown del bot: {e}", style="yellow")

    # Poiché stop_bot() è sincrona, scheduliamo la coroutine asincrona
    # sull'event loop del bot in modo sicuro tra i thread.
    if loop and loop.is_running():
        future = asyncio.run_coroutine_threadsafe(shutdown_with_timeout(), loop)
        
        try:
            # Attendi il completamento del future, ma con un timeout per non bloccare tutto.
            future.result(timeout=6.0)
        except TimeoutError:
            Alumen.console.print("⚠️  La procedura di shutdown di Telegram non è terminata in tempo, ma lo script principale continuerà.", style="yellow")
        except Exception as e:
            Alumen.console.print(f"⚠️  Errore nell'attesa del risultato dello shutdown: {e}", style="yellow")
    else:
        Alumen.console.print("⚠️  L'event loop del bot non è in esecuzione. Impossibile eseguire uno shutdown pulito.", style="yellow")