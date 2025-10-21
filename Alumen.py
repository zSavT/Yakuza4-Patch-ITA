import time
import google.generativeai as genai
import google.api_core.exceptions
import csv
import os
import re
import argparse
import sys
import threading
import requests
from packaging import version
from threading import Thread, Event, Lock
import textwrap
from datetime import datetime
from argparse_color_formatter import ColorHelpFormatter
import json
import polib
from tenacity import retry, stop_after_attempt, wait_exponential, before_sleep_log
import logging

import telegram_bot

from rich.console import Console
from rich.progress import Progress, SpinnerColumn, BarColumn, TextColumn, TimeElapsedColumn, TimeRemainingColumn
from rich.table import Table
from rich.panel import Panel
from rich.rule import Rule
from rich.text import Text

# Console principale per l'output sul terminale (con colori)
console = Console()


# ----- Costanti Globali -----
MAX_RETRIES_PER_API_CALL = 3
MAX_MAJOR_FAILURES_THRESHOLD = 6
DEFAULT_MODEL_NAME = "gemini-2.5-flash"
LOG_FILE_NAME = "log.txt"
CACHE_FILE_NAME = "alumen_cache.json"
BASE_API_CALL_INTERVAL_SECONDS = 0.2
FILE_CONTEXT_SAMPLE_SIZE = 15
CURRENT_SCRIPT_VERSION = "1.5.0"
GITHUB_REPO = "zSavT/Alumen"

# ----- Variabili Globali -----
available_api_keys = []
current_api_key_index = 0
major_failure_count = 0
model = None
script_args = None
log_file_path = None
translation_cache = {}
BLACKLIST_TERMS = set(["Dummy", "dummy"])
blacklisted_api_key_indices = set()
api_call_counts = {}
cache_hit_count = 0
start_time = 0.0
total_files_translated = 0
total_entries_translated = 0
last_cache_save_time = 0.0
rpm_limit = None
rpm_request_timestamps = []
rpm_lock = Lock()

# ----- Variabili per la Modalità Interattiva -----
user_command_skip_api = False
user_command_skip_file = False
script_is_paused = Event()
command_lock = Lock()
graceful_exit_requested = Event()
current_file_context = None
current_file_total_entries = 0
current_file_processed_entries = 0
last_translation_prompt = None
max_entries_limit = 156

ALUMEN_ASCII_ART = """

 ░▒▓██████▓▒░░▒▓█▓▒░     ░▒▓█▓▒░░▒▓█▓▒░▒▓██████████████▓▒░░▒▓████████▓▒░▒▓███████▓▒░
░▒▓█▓▒░░▒▓█▓▒░▒▓█▓▒░     ░▒▓█▓▒░░▒▓█▓▒░▒▓█▓▒░░▒▓█▓▒░░▒▓█▓▒░▒▓█▓▒░      ░▒▓█▓▒░░▒▓█▓▒░
░▒▓█▓▒░░▒▓█▓▒░▒▓█▓▒░     ░▒▓█▓▒░░▒▓█▓▒░▒▓█▓▒░░▒▓█▓▒░░▒▓█▓▒░▒▓█▓▒░      ░▒▓█▓▒░░▒▓█▓▒░
░▒▓████████▓▒░▒▓█▓▒░     ░▒▓█▓▒░░▒▓█▓▒░▒▓█▓▒░░▒▓█▓▒░░▒▓█▓▒░▒▓██████▓▒░ ░▒▓█▓▒░░▒▓█▓▒░
░▒▓█▓▒░░▒▓█▓▒░▒▓█▓▒░     ░▒▓█▓▒░░▒▓█▓▒░▒▓█▓▒░░▒▓█▓▒░░▒▓█▓▒░▒▓█▓▒░      ░▒▓█▓▒░░▒▓█▓▒░
░▒▓█▓▒░░▒▓█▓▒░▒▓█▓▒░     ░▒▓█▓▒░░▒▓█▓▒░▒▓█▓▒░░▒▓█▓▒░░▒▓█▓▒░▒▓█▓▒░      ░▒▓█▓▒░░▒▓█▓▒░
░▒▓█▓▒░░▒▓█▓▒░▒▓████████▓▒░▒▓██████▓▒░░▒▓█▓▒░░▒▓█▓▒░░▒▓█▓▒░▒▓████████▓▒░▒▓█▓▒░░▒▓█▓▒░
   Traduttore Automatico Multilingua potenziato da Gemini
"""

logging.basicConfig(level=logging.INFO, format='%(message)s', handlers=[])
logging.getLogger().addHandler(logging.StreamHandler(sys.stdout))
logging.getLogger().handlers[0].setFormatter(logging.Formatter('%(message)s'))

TRANSIENT_API_EXCEPTIONS = (
    google.api_core.exceptions.ResourceExhausted,
    google.api_core.exceptions.DeadlineExceeded,
    google.api_core.exceptions.ServiceUnavailable,
    google.api_core.exceptions.InternalServerError,
    google.api_core.exceptions.Unknown
)

def retry_if_flag_is_not_set(retry_state):
    is_transient_error = isinstance(retry_state.outcome.exception(), TRANSIENT_API_EXCEPTIONS)
    should_retry = is_transient_error and not script_args.rotate_on_limit_or_error
    return should_retry

def log_before_retry(retry_state):
    active_key_display = available_api_keys[current_api_key_index][-4:]
    error_message = str(retry_state.outcome.exception())
    console.print(f"    ⚠️  Tentativo {retry_state.attempt_number} (Chiave ...{active_key_display}) fallito. Errore: {error_message}")
    console.print(f"    ⏳ Riprovo tra {retry_state.next_action.sleep:.2f} secondi...")
    write_to_log(f"ERRORE API (Retry {retry_state.attempt_number}): Key ...{active_key_display}. Errore: {error_message}")

def get_script_args_updated():
    global script_args
    parser = argparse.ArgumentParser(
        description="Alumen - Script per tradurre file CSV, JSON o PO utilizzando Google Gemini.",
        formatter_class=ColorHelpFormatter
    )
    api_group = parser.add_argument_group('Configurazione API e Modello')
    file_format_group = parser.add_argument_group('Configurazione File e Formato')
    csv_options_group = parser.add_argument_group('Opzioni Specifiche per CSV')
    json_options_group = parser.add_argument_group('Opzioni Specifiche per JSON')
    translation_group = parser.add_argument_group('Parametri di Traduzione')
    wrapping_group = parser.add_argument_group('Opzioni A Capo Automatico (Word Wrapping)')
    utility_group = parser.add_argument_group('Utilità e Modalità Interattiva')
    api_group.add_argument("--api", type=str, help="Specifica una o più chiavi API Google Gemini, separate da virgola.")
    api_group.add_argument("--model-name", type=str, default=DEFAULT_MODEL_NAME, help=f"Nome del modello Gemini da utilizzare. Default: '{DEFAULT_MODEL_NAME}'")
    file_format_group.add_argument("--input", type=str, default="input", help="Percorso della cartella base contenente i file da tradurre. Default: 'input'")
    file_format_group.add_argument("--file-type", type=str, default="csv", choices=['csv', 'json', 'po'], help="Tipo di file da elaborare: 'csv', 'json' o 'po'. Default: 'csv'")
    file_format_group.add_argument("--encoding", type=str, default="utf-8", help="Codifica caratteri dei file. Default: 'utf-8'")
    csv_options_group.add_argument("--delimiter", type=str, default=",", help="[Solo CSV] Carattere delimitatore. Default: ','")
    csv_options_group.add_argument("--translate-col", type=int, default=3, help="[Solo CSV] Indice (0-based) della colonna da tradurre. Default: 3")
    csv_options_group.add_argument("--output-col", type=int, default=3, help="[Solo CSV] Indice (0-based) della colonna per il testo tradotto. Default: 3")
    csv_options_group.add_argument("--max-cols", type=int, default=None, help="[Solo CSV] Numero massimo di colonne attese per riga. Default: Nessun controllo.")
    json_options_group.add_argument("--json-keys", type=str, default=None, help="[Solo JSON, Obbligatorio] Elenco di chiavi (separate da virgola) da tradurre. Supporta notazione a punto per chiavi annidate (es. 'key1,path.to.key2').")
    json_options_group.add_argument("--match-full-json-path", action="store_true", help="[Solo JSON] Per le chiavi JSON, richiede la corrispondenza del percorso completo della chiave (es. 'parent.child.key'), invece del solo nome della chiave.")
    translation_group.add_argument("--game-name", type=str, default="un videogioco generico", help="Nome del gioco per contestualizzare la traduzione.")
    translation_group.add_argument("--source-lang", type=str, default="inglese", help="Lingua originale del testo.")
    translation_group.add_argument("--target-lang", type=str, default="italiano", help="Lingua di destinazione.")
    translation_group.add_argument("--prompt-context", type=str, default=None, help="Aggiunge un'informazione contestuale extra al prompt.")
    translation_group.add_argument("--custom-prompt", type=str, default=None, help="Usa un prompt personalizzato. OBBLIGATORIO: includere '{text_to_translate}'.")
    translation_group.add_argument("--translation-only-output", action="store_true", help="L'output conterrà solo i testi tradotti, uno per riga.")
    translation_group.add_argument("--rpm", type=int, default=None, help="Numero massimo di richieste API a Gemini per minuto.")
    translation_group.add_argument("--enable-file-context", action="store_true", help="Abilita l'analisi di un campione del file per generare un contesto generale da usare in tutte le traduzioni del file.")
    translation_group.add_argument("--full-context-sample", action="store_true", help="[Necessita --enable-file-context] Utilizza TUTTE le frasi valide nel file per generare il contesto generale.")
    wrapping_group.add_argument("--wrap-at", type=int, default=None, help="Lunghezza massima della riga per a capo automatico.")
    wrapping_group.add_argument("--newline-char", type=str, default='\\n', help="Carattere da usare per l'a capo automatico.")
    utility_group.add_argument("--enable-file-log", action="store_true", help=f"Attiva la scrittura di un log ('{LOG_FILE_NAME}').")
    utility_group.add_argument("--interactive", action="store_true", help="Abilita comandi interattivi.")
    utility_group.add_argument("--resume", action="store_true", help="Tenta di riprendere la traduzione da file parziali.")
    utility_group.add_argument("--rotate-on-limit-or-error", action="store_true", help="Passa alla API key successiva in caso di errore o limite RPM.")
    utility_group.add_argument("--persistent-cache", action="store_true", help=f"Attiva la cache persistente su file ('{CACHE_FILE_NAME}').")
    utility_group.add_argument("--telegram", action="store_true", help="Abilita il logging e i comandi tramite un bot Telegram.")

    parsed_args = parser.parse_args()
    if parsed_args.delimiter == '\\t': parsed_args.delimiter = '\t'
    if parsed_args.newline_char == '\\n': parsed_args.newline_char = '\n'
    elif parsed_args.newline_char == '\\r\\n': parsed_args.newline_char = '\r\n'
    script_args = parsed_args
    return parsed_args

def format_time_delta(seconds):
    h = int(seconds // 3600)
    m = int((seconds % 3600) // 60)
    s = int(seconds % 60)
    return f"{h}h {m}m {s}s"

def setup_log_file():
    global log_file_path, script_args
    if not script_args.enable_file_log: return
    try:
        try: script_dir = os.path.dirname(os.path.abspath(__file__))
        except NameError: script_dir = os.getcwd()
        log_file_path = os.path.join(script_dir, LOG_FILE_NAME)
        with open(log_file_path, 'a', encoding='utf-8') as f:
            f.write(ALUMEN_ASCII_ART + "\n")
            f.write(f"--- Nuova Sessione di Log Avviata: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')} ---\n")
            config_to_log = {k: (v if k != 'api' or not v or len(v) < 15 else f"{v[:5]}...{v[-4:]}(nascosta)") for k, v in vars(script_args).items()}
            f.write(f"Configurazione Applicata: {config_to_log}\n")
            f.write("-" * 70 + "\n")
        console.print(f"ℹ️  Logging su file abilitato. I log verranno salvati in: '{log_file_path}'")
    except Exception as e:
        console.print(f"⚠️  Attenzione: Impossibile inizializzare il file di log '{LOG_FILE_NAME}': {e}")
        log_file_path = None

def write_to_log(message):
    global script_args, log_file_path
    if script_args and script_args.enable_file_log and log_file_path:
        try:
            with open(log_file_path, 'a', encoding='utf-8') as f:
                f.write(f"{datetime.now().strftime('%Y-%m-%d %H:%M:%S')} - {message}\n")
        except Exception: pass

def log_critical_error_and_exit(message):
    console.print(f"🛑 [bold red]ERRORE CRITICO:[/] {message}")
    write_to_log(f"ERRORE CRITICO: {message}")
    if script_args and script_args.telegram:
        telegram_bot.send_telegram_notification(f"🛑 *ERRORE CRITICO:* Lo script si è interrotto.\n\n_Motivo:_ {message}")
    sys.exit(1)

def initialize_api_keys_and_model():
    global available_api_keys, current_api_key_index, model, rpm_limit, api_call_counts
    console.print("\n--- Inizializzazione API e Modello ---", style="bold cyan")
    if script_args.api:
        keys_from_arg = [key.strip() for key in script_args.api.split(',') if key.strip()]
        if keys_from_arg:
            available_api_keys.extend(keys_from_arg)
            console.print(f"✅ Trovate {len(keys_from_arg)} API key dall'argomento --api.")
    api_key_file_path = "api_key.txt"
    if os.path.exists(api_key_file_path):
        with open(api_key_file_path, "r") as f:
            keys_from_file = [line.strip() for line in f if line.strip()]
            if keys_from_file:
                available_api_keys.extend(keys_from_file)
                console.print(f"✅ Caricate {len(keys_from_file)} API key dal file '{api_key_file_path}'.")
    seen = set()
    available_api_keys = [x for x in available_api_keys if not (x in seen or seen.add(x))]
    if not available_api_keys:
        log_critical_error_and_exit("Nessuna API key trovata. Specificare tramite --api o nel file 'api_key.txt'.")
    api_call_counts = {i: 0 for i in range(len(available_api_keys))}
    console.print(f"ℹ️  Totale API keys uniche disponibili: {len(available_api_keys)}.")
    current_api_key_index = 0
    try:
        current_key = available_api_keys[current_api_key_index]
        genai.configure(api_key=current_key)
        model = genai.GenerativeModel(script_args.model_name)
        console.print(f"✅ Modello '[bold]{script_args.model_name}[/]' pronto. API Key attiva: [green]...{current_key[-4:]}[/]")
    except Exception as e:
        log_critical_error_and_exit(f"Errore durante l'inizializzazione del modello '{script_args.model_name}': {e}")
    if script_args.rpm and script_args.rpm > 0:
        rpm_limit = script_args.rpm
        console.print(f"ℹ️  Limite RPM impostato a {rpm_limit} richieste al minuto.")
    console.print("---" * 20, style="cyan")

def add_api_key(new_key, is_telegram: bool = False):
    global available_api_keys, api_call_counts, blacklisted_api_key_indices
    new_key = new_key.strip()
    
    if not new_key:
        msg = "🛑 ERRORE: La chiave API non può essere vuota."
        if is_telegram: return msg
        console.print(f"[red]{msg}[/]")
        return
        
    if new_key in available_api_keys:
        msg = "ℹ️  Questa API key è già presente nella lista."
        if is_telegram: return msg
        console.print(f"[yellow]{msg}[/]")
        return

    available_api_keys.append(new_key)
    new_index = len(available_api_keys) - 1
    api_call_counts[new_index] = 0
    blacklisted_api_key_indices.discard(new_index)
    msg = f"✅ Nuova API Key ...{new_key[-4:]} aggiunta. Totale chiavi: {len(available_api_keys)}."
    write_to_log(f"COMANDO: Aggiunta nuova API Key ...{new_key[-4:]}")
    
    if is_telegram: return msg
    console.print(f"[green]{msg}[/]")

def list_api_keys(is_telegram: bool = False):
    if is_telegram:
        lines = ["*🔑 Elenco Chiavi API*"]
        for i, key in enumerate(available_api_keys):
            key_suffix = f"...{key[-4:]}"
            status = ""
            if i == current_api_key_index: status = " (✅ ATTIVA)"
            if i in blacklisted_api_key_indices: status = " (❌ BLACKLISTED)"
            calls = api_call_counts.get(i, 0)
            lines.append(f"`[{i:2}]` • `{key_suffix:7}` • {calls} chiamate{status}")
        return "\n".join(lines)
    else:
        table = Table(title="🔑 Elenco Chiavi API Disponibili", show_header=True, header_style="bold magenta")
        table.add_column("Index", style="dim", width=6)
        table.add_column("Chiave", style="green")
        table.add_column("Stato", justify="right")
        table.add_column("Chiamate", justify="right")
        
        for i, key in enumerate(available_api_keys):
            key_suffix = f"...{key[-4:]}"
            status = Text("")
            if i == current_api_key_index:
                status = Text("ATTIVA", style="bold green")
            if i in blacklisted_api_key_indices:
                status = Text("BLACKLISTED", style="bold red")
            
            calls = api_call_counts.get(i, 0)
            table.add_row(str(i), key_suffix, status, str(calls))
            
        console.print(table)

def remove_api_key(index_str, is_telegram: bool = False):
    global available_api_keys, current_api_key_index, api_call_counts, blacklisted_api_key_indices
    try:
        index = int(index_str)
        if not (0 <= index < len(available_api_keys)):
            msg = f"🛑 ERRORE: Indice {index} fuori dal range valido."
            if is_telegram: return msg
            console.print(f"[red]{msg}[/]")
            return
            
        key_suffix = available_api_keys[index][-4:]
        del available_api_keys[index]
        if index in blacklisted_api_key_indices: blacklisted_api_key_indices.discard(index)
        
        new_api_call_counts = {}
        new_blacklisted_indices = set()
        for old_index, count in api_call_counts.items():
            if old_index < index: new_api_call_counts[old_index] = count
            elif old_index > index:
                new_index = old_index - 1
                new_api_call_counts[new_index] = count
                if old_index in blacklisted_api_key_indices: new_blacklisted_indices.add(new_index)
        api_call_counts = new_api_call_counts
        blacklisted_api_key_indices = new_blacklisted_indices
        
        if current_api_key_index == index:
            if not is_telegram:
                console.print(f"[yellow]⚠️  La chiave rimossa era quella attiva. Rotazione necessaria.[/]")
            if available_api_keys:
                current_api_key_index = index % len(available_api_keys) if index < len(available_api_keys) else len(available_api_keys) - 1
                rotate_api_key(reason_override="Chiave attiva rimossa")
            else:
                log_critical_error_and_exit("Tutte le API key rimosse. Impossibile proseguire.")
        elif current_api_key_index > index:
            current_api_key_index -= 1

        msg = f"✅ API Key ...{key_suffix} all'indice {index} rimossa."
        write_to_log(f"COMANDO: {msg}")
        if is_telegram: return msg
        console.print(f"[green]{msg}[/]")

    except ValueError:
        msg = "🛑 ERRORE: L'indice deve essere un numero intero."
        if is_telegram: return msg
        console.print(f"[red]{msg}[/]")
    except Exception as e:
        msg = f"🛑 ERRORE: Impossibile rimuovere la chiave API: {e}"
        if is_telegram: return msg
        console.print(f"[red]{msg}[/]")

def blacklist_specific_api_key(index_str, is_telegram: bool = False):
    global current_api_key_index, blacklisted_api_key_indices
    try:
        index = int(index_str)
        if not (0 <= index < len(available_api_keys)):
            msg = f"🛑 ERRORE: Indice {index} fuori dal range valido."
            if is_telegram: return msg
            console.print(f"[red]{msg}[/]")
            return

        if index in blacklisted_api_key_indices:
            msg = f"ℹ️  L'API Key ...{available_api_keys[index][-4:]} è già in blacklist."
            if is_telegram: return msg
            console.print(f"[yellow]{msg}[/]")
            return

        key_suffix = available_api_keys[index][-4:]
        blacklisted_api_key_indices.add(index)
        msg = f"✅ API Key ...{key_suffix} all'indice {index} aggiunta alla blacklist."
        write_to_log(f"COMANDO: API Key ...{key_suffix} blacklisted.")
        if index == current_api_key_index:
            rotate_api_key(triggered_by_user=True, reason_override="Key blacklisted da comando")
        if is_telegram: return msg
        console.print(f"[green]{msg}[/]")

    except ValueError:
        msg = "🛑 ERRORE: L'indice deve essere un numero intero."
        if is_telegram: return msg
        console.print(f"[red]{msg}[/]")
    except Exception as e:
        msg = f"🛑 ERRORE: Impossibile blackistare la chiave API: {e}"
        if is_telegram: return msg
        console.print(f"[red]{msg}[/]")

def clear_blacklisted_keys(is_telegram: bool = False):
    global blacklisted_api_key_indices
    count = len(blacklisted_api_key_indices)
    if count == 0:
        msg = "ℹ️  Nessuna chiave era in blacklist."
        if is_telegram: return msg
        console.print(f"[yellow]{msg}[/]")
        return
    blacklisted_api_key_indices.clear()
    msg = f"✅ {count} chiavi rimosse dalla blacklist."
    write_to_log(f"COMANDO: {msg}")
    if is_telegram: return msg
    console.print(f"[green]{msg}[/]")

def set_rpm_limit_func(rpm_str, is_telegram: bool = False):
    global rpm_limit, rpm_request_timestamps
    try:
        new_rpm = int(rpm_str)
        if new_rpm < 0:
            msg = "🛑 ERRORE: Il limite RPM non può essere negativo."
        elif new_rpm == 0:
            rpm_limit = None
            msg = "✅ Limite RPM disabilitato."
        else:
            rpm_limit = new_rpm
            with rpm_lock:
                rpm_request_timestamps.clear()
            msg = f"✅ Nuovo limite RPM impostato a {new_rpm}."
        
        write_to_log(f"COMANDO: {msg.strip()}")
        if is_telegram: return msg
        console.print(f"[green]{msg}[/]")
    except ValueError:
        msg = "🛑 ERRORE: Il limite RPM deve essere un numero intero."
        if is_telegram: return msg
        console.print(f"[red]{msg}[/]")

def show_rpm_stats(title="Statistiche RPM", is_telegram: bool = False):
    global rpm_limit, rpm_request_timestamps
    current_time = time.time()
    with rpm_lock:
        rpm_request_timestamps[:] = [ts for ts in rpm_request_timestamps if ts > current_time - 60.0]
        current_rpm = len(rpm_request_timestamps)
    limit_display = f"{rpm_limit}/min" if rpm_limit is not None else "Disabilitato"

    if is_telegram:
        lines = [f"*{title}*"]
        lines.append(f"⏱️ *Limite:* `{limit_display}`")
        lines.append(f"📈 *Utilizzo ultimi 60s:* `{current_rpm}` chiamate")
        if rpm_limit is not None and rpm_limit > 0:
            remaining = rpm_limit - current_rpm
            lines.append(f"📉 *Chiamate rimanenti:* `{max(0, remaining)}`")
            if current_rpm >= rpm_limit:
                wait_duration = (rpm_request_timestamps[0] + 60.0) - current_time
                lines.append(f"⏳ *Attesa necessaria:* `{max(0.0, wait_duration):.2f}` secondi")
        return "\n".join(lines)
    else:
        table = Table(title=title, show_header=False)
        table.add_column("Parametro", style="cyan")
        table.add_column("Valore", style="bold")
        table.add_row("Limite impostato", limit_display)
        table.add_row("Utilizzo ultimi 60s", f"{current_rpm} chiamate")
        if rpm_limit is not None and rpm_limit > 0:
            remaining = rpm_limit - current_rpm
            table.add_row("Chiamate rimanenti", str(max(0, remaining)))
            if current_rpm >= rpm_limit:
                wait_duration = (rpm_request_timestamps[0] + 60.0) - current_time
                table.add_row("Attesa necessaria", f"{max(0.0, wait_duration):.2f} secondi")
        console.print(table)

def set_model_name(model_name, is_telegram: bool = False):
    global model, script_args
    if not model_name:
        msg = "🛑 ERRORE: Il nome del modello non può essere vuoto."
        if is_telegram: return msg
        console.print(f"[red]{msg}[/]")
        return
    try:
        temp_model = genai.GenerativeModel(model_name)
        model = temp_model
        script_args.model_name = model_name
        msg = f"✅ Modello aggiornato a '{model_name}'."
        write_to_log(f"COMANDO: {msg}")
        if is_telegram: return msg
        console.print(f"[green]{msg}[/]")
    except Exception as e:
        msg = f"🛑 ERRORE: Impossibile impostare il modello '{model_name}'. Errore: {e}"
        if is_telegram: return msg
        console.print(f"[red]{msg}[/]")

def show_file_progress(is_telegram: bool = False):
    if current_file_total_entries == 0:
        msg = "ℹ️  Nessun file in elaborazione."
        if is_telegram: return msg
        console.print(f"[yellow]{msg}[/]")
        return

    progress = (current_file_processed_entries / current_file_total_entries) * 100
    file_type = script_args.file_type.upper()

    if is_telegram:
        lines = ["*📊 Stato Avanzamento File*"]
        lines.append(f"📁 *Tipo File:* `{file_type}`")
        lines.append(f"🔄 *Entry Tradotte:* `{current_file_processed_entries} / {current_file_total_entries}`")
        lines.append(f"📈 *Percentuale:* `{progress:.2f}%`")
        if current_file_context:
            lines.append(f"📝 *Contesto:* `'{current_file_context[:60].strip()}...'`")
        return "\n".join(lines)
    else:
        table = Table(title="Stato Avanzamento File", show_header=False)
        table.add_column("Parametro", style="cyan")
        table.add_column("Valore", style="bold")
        table.add_row("Tipo File", file_type)
        table.add_row("Entry Tradotte", f"{current_file_processed_entries} / {current_file_total_entries}")
        table.add_row("Percentuale", f"{progress:.2f}%")
        if current_file_context:
            table.add_row("Contesto", f"'{current_file_context[:60].strip()}...'")
        console.print(table)

def reload_persistent_cache(is_telegram: bool = False):
    if not script_args.persistent_cache:
        msg = "⚠️  La cache persistente è disabilitata. Usa --persistent-cache."
        if is_telegram: return msg
        console.print(f"[yellow]{msg}[/]")
        return
    initial_count = len(translation_cache)
    try:
        load_persistent_cache(silent=True)
        final_count = len(translation_cache)
        msg = f"✅ Cache ricaricata. Voci: {initial_count} -> {final_count}."
        write_to_log(f"COMANDO: {msg}")
        if is_telegram: return msg
        console.print(f"[green]{msg}[/]")
    except Exception as e:
        msg = f"🛑 ERRORE: Impossibile ricaricare la cache: {e}"
        if is_telegram: return msg
        console.print(f"[red]{msg}[/]")

def clear_translation_cache_func(is_telegram: bool = False):
    global translation_cache
    count = len(translation_cache)
    if count == 0:
        msg = "ℹ️  La cache in memoria è già vuota."
        if is_telegram: return msg
        console.print(f"[yellow]{msg}[/]")
        return
    translation_cache.clear()
    msg = f"✅ Cache in memoria svuotata. Rimosse {count} voci."
    write_to_log(f"COMANDO: {msg}")
    if script_args.persistent_cache:
        msg += "\nℹ️  Per svuotare anche la cache su disco, usa il comando 'save cache'."
    if is_telegram: return msg
    console.print(f"[green]{msg}[/]")

def display_colored_prompt(is_telegram: bool = False):
    if not last_translation_prompt:
        msg = "ℹ️  Nessun prompt di traduzione è stato ancora inviato."
        if is_telegram: return msg
        console.print(f"[yellow]{msg}[/]")
        return

    if is_telegram:
        return f"*📝 Ultimo Prompt Inviato:*\n```\n{last_translation_prompt}\n```"
    else:
        text = Text(last_translation_prompt)
        text.highlight_regex(r'\{[^{}]*\}', "bold yellow")
        console.print(Panel(text, title="Ultimo Prompt di Traduzione Inviato", border_style="cyan"))

def rotate_api_key(triggered_by_user=False, reason_override=None):
    global current_api_key_index, major_failure_count, model, blacklisted_api_key_indices
    usable_keys_count = len(available_api_keys) - len(blacklisted_api_key_indices)
    if usable_keys_count <= 1 and current_api_key_index not in blacklisted_api_key_indices:
        console.print("⚠️  Solo una API key utilizzabile disponibile. Impossibile ruotare.", style="yellow")
        return False
    if usable_keys_count == 0:
        console.print("🛑 ERRORE CRITICO: Tutte le API key sono state blacklisted. Impossibile proseguire.", style="bold red")
        write_to_log("ERRORE CRITICO: Tutte le API key sono state blacklisted.")
        return False
    previous_key_index = current_api_key_index
    initial_index = current_api_key_index
    while True:
        current_api_key_index = (current_api_key_index + 1) % len(available_api_keys)
        if current_api_key_index not in blacklisted_api_key_indices: break
        if current_api_key_index == initial_index:
            console.print("🛑 ERRORE CRITICO: Impossibile trovare una API key non blacklisted.", style="bold red")
            write_to_log("ERRORE CRITICO: Impossibile trovare una API key non blacklisted.")
            if previous_key_index not in blacklisted_api_key_indices:
                current_api_key_index = previous_key_index
            return False
    new_api_key = available_api_keys[current_api_key_index]
    trigger_reason = reason_override if reason_override else ("Comando utente." if triggered_by_user else f"Soglia fallimenti raggiunta.")
    
    console.print(f"\nℹ️  Rotazione API Key in corso (Motivo: {trigger_reason})...")
    if script_args.telegram:
        telegram_bot.send_telegram_notification(f"🔄 *Rotazione API Key...*\n_Motivo:_ {trigger_reason}")

    try:
        genai.configure(api_key=new_api_key)
        model = genai.GenerativeModel(script_args.model_name)
        
        console.print(f"✅ Rotazione completata. Nuova API Key attiva: [green]...{new_api_key[-4:]}[/]")
        if script_args.telegram:
            telegram_bot.send_telegram_notification(f"✅ *Rotazione completata.*\n*Nuova API Key attiva:* `...{new_api_key[-4:]}`")

        major_failure_count = 0
        return True
    except Exception as e:
        console.print(f"❌ [red]ERRORE: Configurazione nuova API Key fallita: {e}[/]")
        if script_args.telegram:
            telegram_bot.send_telegram_notification(f"❌ *ERRORE:* Configurazione nuova API Key `...{new_api_key[-4:]}` fallita.")
        
        if previous_key_index not in blacklisted_api_key_indices:
            current_api_key_index = previous_key_index
            try:
                genai.configure(api_key=available_api_keys[previous_key_index])
                model = genai.GenerativeModel(script_args.model_name)
                console.print("✅ API Key precedente ripristinata.")
            except Exception as e_revert:
                log_critical_error_and_exit(f"Errore nel ripristino della API Key precedente: {e_revert}.")
        else:
            log_critical_error_and_exit("Fallita rotazione API e la chiave precedente è blacklisted. Nessuna chiave utilizzabile.")
        return False

def blacklist_current_api_key(is_telegram: bool = False):
    global current_api_key_index, blacklisted_api_key_indices
    if current_api_key_index in blacklisted_api_key_indices:
        msg = f"ℹ️  L'API Key ...{available_api_keys[current_api_key_index][-4:]} è già in blacklist."
        if is_telegram: return msg
        console.print(f"[yellow]{msg}[/]")
        return
    blacklisted_api_key_indices.add(current_api_key_index)
    key_suffix = available_api_keys[current_api_key_index][-4:]
    msg = f"✅ API Key ...{key_suffix} aggiunta alla blacklist."
    write_to_log(f"COMANDO: {msg}")
    rotate_api_key(triggered_by_user=True, reason_override="Key blacklisted")
    if is_telegram: return msg
    console.print(f"[green]{msg}[/]")

def show_stats(title="📊 STATISTICHE DI ESECUZIONE", is_telegram: bool = False):
    end_time = time.time()
    total_time = end_time - start_time
    total_api_calls = sum(api_call_counts.values())
    avg_time_per_file = 0.0
    if total_files_translated > 0: avg_time_per_file = total_time / total_files_translated

    if is_telegram:
        lines = [f"*{title}*"]
        lines.append(f"⏳ *Tempo trascorso:* {format_time_delta(total_time)}")
        lines.append(f"✅ *File tradotti:* {total_files_translated}")
        lines.append(f"✅ *Entry tradotte:* {total_entries_translated}")
        if total_files_translated > 0:
            lines.append(f"⏱️ *Tempo medio per file:* {format_time_delta(avg_time_per_file)}")
        lines.append("")
        lines.append(f"💾 *Traduzioni da cache:* {cache_hit_count}")
        lines.append(f"📞 *Chiamate API totali:* {total_api_calls}")
        lines.append("\n" + list_api_keys(is_telegram=True))
        return "\n".join(lines)
    else:
        console.print()
        main_table = Table(title=title, show_header=False, header_style="bold magenta")
        main_table.add_column("Parametro", style="cyan")
        main_table.add_column("Valore", style="bold")
        main_table.add_row("⏳ Tempo trascorso", format_time_delta(total_time))
        main_table.add_row("✅ File tradotti", str(total_files_translated))
        main_table.add_row("✅ Frasi/Entry tradotte", str(total_entries_translated))
        if total_files_translated > 0:
            main_table.add_row("⏱️  Tempo medio per file", format_time_delta(avg_time_per_file))
        main_table.add_section()
        main_table.add_row("💾 Traduzioni da cache", str(cache_hit_count))
        main_table.add_row("📞 Chiamate API totali", str(total_api_calls))
        console.print(main_table)
        list_api_keys()

def process_command(command_line: str, is_telegram: bool = False):
    command_parts = command_line.split(maxsplit=1)
    command = command_parts[0].lower() if command_parts else ""
    output = ""
    
    with command_lock:
        if command == "stop":
            graceful_exit_requested.set()
            output = "➡️  Comando ricevuto: lo script terminerà dopo il file attuale."
        elif command == "log":
            if len(command_parts) > 1 and command_parts[1].strip():
                if script_args.enable_file_log:
                    user_message = command_parts[1].strip()
                    write_to_log(f"MESSAGGIO UTENTE: {user_message}")
                    output = f"✅ Messaggio '{user_message}' aggiunto al log."
                else:
                    output = "⚠️  Impossibile scrivere nel log: il logging su file è disabilitato."
            else:
                output = "⚠️  Comando non valido. Usa 'log <messaggio>'."
        elif command == "context":
            if current_file_context:
                output = f"ℹ️  Contesto attivo per il file corrente:\n`{current_file_context}`"
            else:
                output = "ℹ️  Nessun contesto generato per il file corrente."
        elif command == "skip":
            sub_command = command_parts[1].lower() if len(command_parts) > 1 else ""
            if sub_command == "api": user_command_skip_api = True; output = "➡️  Comando ricevuto: salto dell'API corrente in corso..."
            elif sub_command == "file": user_command_skip_file = True; output = "➡️  Comando ricevuto: salto del file corrente in corso..."
            else: output = "⚠️  Comando non valido. Usa 'skip api' o 'skip file'."
        elif command == "pause":
            script_is_paused.clear()
            output = "⏳ SCRIPT IN PAUSA. Invia 'resume' per continuare."
            if not is_telegram: show_stats("📊 STATISTICHE AL MOMENTO DELLA PAUSA")
        elif command == "resume":
            script_is_paused.set()
            output = "▶️  Script in esecuzione..."
        elif command == "stats":
            return show_stats("📊 STATISTICHE ATTUALI", is_telegram=is_telegram)
        elif command == "add":
            parts = command_parts[1].split(maxsplit=1) if len(command_parts) > 1 else []
            if len(parts) == 2 and parts[0].lower() == 'api': output = add_api_key(parts[1].strip(), is_telegram=is_telegram)
            else: output = "⚠️  Comando non valido. Usa 'add api <chiave>'."
        elif command == "exhausted": output = blacklist_current_api_key(is_telegram=is_telegram)
        elif command == "list" and len(command_parts) > 1 and command_parts[1].lower() == 'keys':
            return list_api_keys(is_telegram=is_telegram)
        elif command == "remove":
            parts = command_parts[1].split(maxsplit=1) if len(command_parts) > 1 else []
            if len(parts) == 2 and parts[0].lower() == 'key': output = remove_api_key(parts[1].strip(), is_telegram=is_telegram)
            else: output = "⚠️  Comando non valido. Usa 'remove key <indice>'."
        elif command == "blacklist":
            if len(command_parts) > 1: output = blacklist_specific_api_key(command_parts[1].strip(), is_telegram=is_telegram)
            else: output = "⚠️  Comando non valido. Usa 'blacklist <indice>'."
        elif command == "clear" and len(command_parts) > 1 and command_parts[1].lower() == 'blacklist':
            output = clear_blacklisted_keys(is_telegram=is_telegram)
        elif command == "set":
            parts = command_parts[1].split(maxsplit=1) if len(command_parts) > 1 else []
            if len(parts) == 2:
                param, value = parts[0].lower(), parts[1].strip()
                if param == 'rpm': output = set_rpm_limit_func(value, is_telegram=is_telegram)
                elif param == 'model': output = set_model_name(value, is_telegram=is_telegram)
                elif param == 'max_entries': output = set_max_entries_limit(value, is_telegram=is_telegram)
                else: output = "⚠️  Comando non valido. Usa 'set <rpm|model|max_entries> <valore>'."
            else: output = "⚠️  Comando non valido. Usa 'set <param> <valore>'."
        elif command == "show":
            sub_command = command_parts[1].lower() if len(command_parts) > 1 else ""
            if sub_command == "rpm": return show_rpm_stats(is_telegram=is_telegram)
            elif sub_command == "file_progress": return show_file_progress(is_telegram=is_telegram)
            else: output = "⚠️  Comando non valido. Usa 'show <rpm|file_progress>'."
        elif command == "reload" and len(command_parts) > 1 and command_parts[1].lower() == 'cache':
            output = reload_persistent_cache(is_telegram=is_telegram)
        elif command == "clear" and len(command_parts) > 1 and command_parts[1].lower() == 'cache':
            output = clear_translation_cache_func(is_telegram=is_telegram)
        elif command == "prompt":
            return display_colored_prompt(is_telegram=is_telegram)
        elif command == "save" or (command == "salva" and len(command_parts) > 1 and command_parts[1].lower() == "cache"):
            if script_args.persistent_cache:
                output = "➡️ Comando ricevuto: salvataggio della cache in corso..."
                save_persistent_cache()
            else:
                output = "⚠️ Attenzione: La cache persistente è disabilitata."
        elif command == "help":
            help_sections = {
                "Controllo Esecuzione": {
                    "stop": "Termina lo script dopo il file attuale.",
                    "pause": "Mette in pausa l'elaborazione.",
                    "resume": "Riprende l'elaborazione."
                },
                "Salto e Rotazione API": {
                    "skip file": "Salta il file corrente.",
                    "skip api": "Forza la rotazione della chiave API.",
                    "exhausted": "Mette in blacklist la chiave corrente e ruota."
                },
                "Gestione Chiavi API": {
                    "list keys": "Mostra l'elenco delle chiavi API.",
                    "add api <chiave>": "Aggiunge una nuova chiave API.",
                    "remove key <indice>": "Rimuove una chiave API per indice.",
                    "blacklist <indice>": "Aggiunge una chiave alla blacklist.",
                    "clear blacklist": "Pulisce la blacklist delle chiavi."
                },
                "Statistiche e Info": {
                    "stats": "Mostra le statistiche complete.",
                    "show rpm": "Visualizza le statistiche RPM.",
                    "show file_progress": "Mostra l'avanzamento del file corrente.",
                    "context": "Mostra il contesto generato per il file.",
                    "prompt": "Mostra l'ultimo prompt inviato a Gemini."
                },
                "Configurazione": {
                    "set rpm <numero>": "Imposta il limite di richieste al minuto (0 per disabilitare).",
                    "set model <nome>": "Cambia il modello Gemini in uso.",
                    "set max_entries <numero>": "Imposta il limite massimo di entry per file."
                },
                "Cache": {
                    "save cache": "Forza il salvataggio della cache su file.",
                    "reload cache": "Ricarica la cache dal file persistente.",
                    "clear cache": "Svuota la cache in memoria."
                }
            }
            
            if is_telegram:
                lines = ["*🆘 AIUTO COMANDI ALUMEN*"]
                for section, commands in help_sections.items():
                    lines.append(f"\n*{section}*")
                    for cmd, desc in commands.items():
                        lines.append(f"`/{cmd}` - {desc}")
                return "\n".join(lines)
            else:
                # Per la console locale, usiamo un formato più leggibile
                output_lines = []
                for section, commands in help_sections.items():
                    output_lines.append(f"\n[bold cyan]--- {section} ---[/bold cyan]")
                    for cmd, desc in commands.items():
                        output_lines.append(f"[yellow]{cmd:<25}[/yellow] {desc}")
                output = "\n".join(output_lines)

    if is_telegram:
        return output
    console.print(output)

def command_input_thread_func():
    console.print(Rule("[bold]Alumen - Console Interattiva[/]"))
    console.print("ℹ️  Digita 'help' per i comandi, 'exit' o 'quit' per chiudere.")
    while True:
        try:
            prompt_indicator = "[yellow](In Pausa)[/]" if not script_is_paused.is_set() else ""
            command_line = console.input(f"[bold magenta]Alumen >[/] {prompt_indicator}").strip()
            
            if command_line.lower() in ["exit", "quit"]:
                process_command(command_line)
                break

            if command_line:
                process_command(command_line, is_telegram=False)
        except (EOFError, KeyboardInterrupt):
            console.print("\nINFO: Chiusura console interattiva.");
            break
        except Exception as e:
            console.print(f"🛑 Errore nel thread input comandi: {e}")
            break

def check_and_wait_if_paused(file_context=""):
    global script_is_paused
    if script_args.interactive and not script_is_paused.is_set():
        console.print(f"\n[yellow]▶️  SCRIPT RIPRESO (Lavorando su: {file_context}).[/]\n")
        script_is_paused.wait()

def wait_for_rpm_limit():
    global rpm_limit, rpm_request_timestamps
    if not rpm_limit or rpm_limit <= 0: return
    while True:
        if script_args.interactive: check_and_wait_if_paused("Attesa RPM")
        with rpm_lock:
            current_time = time.time()
            rpm_request_timestamps[:] = [ts for ts in rpm_request_timestamps if ts > current_time - 60.0]
            if len(rpm_request_timestamps) < rpm_limit:
                rpm_request_timestamps.append(current_time)
                break
            else:
                if script_args.rotate_on_limit_or_error and rotate_api_key(reason_override="Limite RPM raggiunto"): break
                wait_duration = (rpm_request_timestamps[0] + 60.0) - current_time
        if wait_duration > 0:
            console.print(f"    [yellow]⏳ Limite RPM ({rpm_limit}/min) raggiunto. Attesa di {wait_duration:.1f} secondi...[/]")
            time.sleep(wait_duration)

def determine_if_translatable(text_value):
    if not isinstance(text_value, str): return False
    text_value_stripped = text_value.strip()
    if not text_value_stripped or text_value_stripped.isdigit() or re.match(r'^[\W_]+$', text_value_stripped) or "\\u" in text_value_stripped:
        return False
    if '_' in text_value_stripped and ' ' not in text_value_stripped:
        return False
    return True

def load_persistent_cache(silent: bool = False):
    global translation_cache, script_args, last_cache_save_time
    if not script_args.persistent_cache: return
    try:
        if os.path.exists(CACHE_FILE_NAME):
            with open(CACHE_FILE_NAME, 'r', encoding='utf-8') as f:
                translation_cache = json.load(f)
            if not silent:
                console.print(f"✅ Cache persistente caricata da '[green]{CACHE_FILE_NAME}[/]' ({len(translation_cache)} voci).")
            last_cache_save_time = time.time()
        else:
            if not silent:
                console.print(f"ℹ️  File di cache '[yellow]{CACHE_FILE_NAME}[/]' non trovato. Verrà creato un nuovo file.")
            last_cache_save_time = 0.0
    except (json.JSONDecodeError, IOError) as e:
        console.print(f"⚠️  [yellow]Attenzione: Impossibile caricare la cache da '{CACHE_FILE_NAME}': {e}. Verrà ricreata.[/]")
        translation_cache = {}
        last_cache_save_time = 0.0

def save_persistent_cache():
    global translation_cache, script_args, last_cache_save_time
    if not script_args.persistent_cache or not translation_cache:
        if script_args.persistent_cache and not translation_cache:
            console.print("\nℹ️  Salvataggio cache ignorato: la cache è vuota.", style="yellow")
        return
    try:
        with open(CACHE_FILE_NAME, 'w', encoding='utf-8') as f:
            json.dump(translation_cache, f, ensure_ascii=False, indent=4)
        console.print(f"\n✅ Cache ({len(translation_cache)} voci) salvata correttamente in '[green]{CACHE_FILE_NAME}[/]'.")
        last_cache_save_time = time.time()
    except IOError as e:
        console.print(f"\n[red]🛑 ERRORE: Impossibile salvare la cache in '{CACHE_FILE_NAME}': {e}[/]")

def check_and_save_cache():
    global last_cache_save_time, script_args
    if not script_args.persistent_cache: return
    current_time = time.time()
    if last_cache_save_time == 0.0 or current_time - last_cache_save_time >= 600:
        console.print("\nℹ️  Salvataggio periodico della cache in corso...")
        write_to_log("Salvataggio cache periodico (10 minuti) attivato.")
        save_persistent_cache()

@retry(
    stop=stop_after_attempt(MAX_RETRIES_PER_API_CALL),
    wait=wait_exponential(multiplier=1.5, min=2, max=15),
    retry=retry_if_flag_is_not_set,
    before_sleep=log_before_retry
)
def _call_generative_model_for_context(prompt):
    wait_for_rpm_limit()
    time.sleep(BASE_API_CALL_INTERVAL_SECONDS)
    response_obj = model.generate_content(prompt)
    if not response_obj or not hasattr(response_obj, 'text'):
        raise ValueError("Risposta dall'API non valida o vuota.")
    return response_obj.text.strip()

def generate_file_context(sample_text, file_name, args):
    global major_failure_count, model, translation_cache, cache_hit_count, api_call_counts
    context_cache_key = f"CONTEXT_FILE::{file_name}::{args.game_name}::{args.prompt_context}"
    if args.full_context_sample: context_cache_key += "::FULL_SAMPLE"
    if context_cache_key in translation_cache:
        console.print(f"  ✅ Contesto per '[bold]{file_name}[/]' trovato nella cache.")
        cache_hit_count += 1
        return translation_cache[context_cache_key]
    
    console.print(f"  ➡️  Richiesta API per generare il contesto del file '[bold]{file_name}[/]'...")
    prompt = f"Analizza il seguente campione di testo, che proviene da un file di traduzione per il gioco '{args.game_name}'. Il tuo compito è determinare, in non più di due frasi concise, l'argomento principale, il contesto generale o l'ambientazione più probabile di questo file. Questo contesto verrà utilizzato per migliorare la qualità delle traduzioni successive. Rispondi solo con il contesto generato.\nCampione di testo:\n---\n{sample_text}\n---\nContesto generato:"
    
    while True:
        if args.interactive: check_and_wait_if_paused(f"Generazione Contesto per File: {file_name}")
        with command_lock:
            global user_command_skip_file
            if user_command_skip_file: raise KeyboardInterrupt
        try:
            file_context = _call_generative_model_for_context(prompt)
            api_call_counts[current_api_key_index] += 1
            if args.wrap_at and args.wrap_at > 0: file_context = textwrap.fill(file_context, width=args.wrap_at, newline=args.newline_char, replace_whitespace=False)
            translation_cache[context_cache_key] = file_context
            console.print(f"  ✅ Contesto generato per il file: '[italic]{file_context}[/]'")
            write_to_log(f"Contesto generato per {file_name}: {file_context}")
            major_failure_count = 0
            return file_context
        except TRANSIENT_API_EXCEPTIONS as e:
            major_failure_count += 1
            console.print(f"    ❌ Fallimento definitivo generazione contesto con Chiave ...{available_api_keys[current_api_key_index][-4:]}. Fallimenti consecutivi: {major_failure_count}/{MAX_MAJOR_FAILURES_THRESHOLD}", style="red")
            if args.rotate_on_limit_or_error and rotate_api_key(reason_override="Errore API"):
                continue
            elif major_failure_count >= MAX_MAJOR_FAILURES_THRESHOLD and rotate_api_key():
                 continue
            else:
                console.print("    ⚠️  Rotazione API non attiva o fallita. Contesto file non generabile.", style="yellow")
                return None
        except Exception as e:
            console.print(f"    🛑 Errore non gestito durante la generazione del contesto: {e}", style="red")
            return None

@retry(
    stop=stop_after_attempt(MAX_RETRIES_PER_API_CALL),
    wait=wait_exponential(multiplier=1.5, min=2, max=15),
    retry=retry_if_flag_is_not_set,
    before_sleep=log_before_retry
)
def _call_generative_model_for_translation(prompt_text):
    global last_translation_prompt
    wait_for_rpm_limit()
    time.sleep(BASE_API_CALL_INTERVAL_SECONDS)
    last_translation_prompt = prompt_text
    response_obj = model.generate_content(prompt_text)
    if not response_obj or not hasattr(response_obj, 'text'):
        raise ValueError("Risposta dall'API non valida o vuota.")
    return response_obj.text.strip()

def get_translation_from_api(text_to_translate, context_for_log, args, dynamic_context=None):
    global major_failure_count, user_command_skip_api, model, translation_cache, cache_hit_count, api_call_counts, BLACKLIST_TERMS
    if text_to_translate.strip() in BLACKLIST_TERMS:
        return text_to_translate
    if not determine_if_translatable(text_to_translate): return text_to_translate
    
    cache_key_tuple = (text_to_translate, args.source_lang, args.target_lang, args.game_name, args.prompt_context)
    cache_key = json.dumps(cache_key_tuple, ensure_ascii=False)
    
    if cache_key in translation_cache:
        cache_hit_count += 1
        translated_text = translation_cache[cache_key]
        
        content = Text.from_markup(f"    [dim]└─ Orig:[/] {text_to_translate}\n    [dim]└─ Trad:[/] {translated_text}")
        console.print(Panel(content, title=f"[bold green]💾 CACHE[/] | {context_for_log}", border_style="green", title_align="left"))
        return translated_text
        
    if args.custom_prompt:
        if "{text_to_translate}" not in args.custom_prompt:
            console.print(f"    - ❌ ERRORE: Il prompt personalizzato non include '{{text_to_translate}}'. Salto.", style="red")
            return text_to_translate
        prompt_text = args.custom_prompt.format(text_to_translate=text_to_translate)
    else:
        blacklist_str = ", ".join(BLACKLIST_TERMS)
        prompt_base = f"Traduci il seguente testo da {args.source_lang} a {args.target_lang}, tenendo conto del contesto del gioco '{args.game_name}' e utilizzando uno stile che includa eventuali slang o espressioni colloquiali appropriate al contesto e quindi adattando il testo se serve. ISTRUZIONE CRITICA: preserva esattamente tutti gli a capo originali (come `\\n` o `\\r\\n`) presenti nel testo. Inoltre, preserva eventuali tag HTML, placeholder (come [p], {{player_name}}), o codici speciali (come ad esempio stringhe con codici tipo: talk_id_player). Assicurati di mantenere identici i seguenti termini che NON devono essere tradotti, anche se appaiono in frasi più lunghe: {blacklist_str}. In caso di dubbi sul genere (Femminile o Maschile), utilizza il maschile."
        if args.prompt_context: prompt_base += f"\nIstruzione aggiuntiva: {args.prompt_context}."
        if dynamic_context: prompt_base += f"\nContesto aggiuntivo per questa traduzione: '{dynamic_context}'."
        prompt_base += "\nRispondi solo con la traduzione diretta."
        prompt_text = f"{prompt_base}\nTesto originale:\n{text_to_translate}\n\nTraduzione in {args.target_lang}:"
        
    while True:
        if args.interactive: check_and_wait_if_paused(context_for_log)
        with command_lock:
            if user_command_skip_api:
                rotate_api_key(triggered_by_user=True)
                user_command_skip_api = False
        try:
            translated_text = _call_generative_model_for_translation(prompt_text)
            api_call_counts[current_api_key_index] += 1
            if args.wrap_at and args.wrap_at > 0: translated_text = textwrap.fill(translated_text, width=args.wrap_at, newline=args.newline_char, replace_whitespace=False)
            major_failure_count = 0

            content = Text.from_markup(f"    [dim]└─ Orig:[/] {text_to_translate}\n    [dim]└─ Trad:[/] {translated_text}")
            console.print(Panel(content, title=f"{context_for_log}", border_style="blue", title_align="left"))
            
            translation_cache[cache_key] = translated_text
            write_to_log(f"CACHE MISS: Nuova traduzione salvata in cache per il contesto: {context_for_log}")
            return translated_text
        except google.api_core.exceptions.PermissionDenied as e:
            console.print(f"    🛑 Chiave API ...{available_api_keys[current_api_key_index][-4:]} non valida o disabilitata. Verrà messa in blacklist.", style="red")
            write_to_log(f"ERRORE PERMESSO: {context_for_log}, Key ...{available_api_keys[current_api_key_index][-4:]}. Errore: {e}")
            if blacklist_current_api_key():
                continue
            else:
                return text_to_translate
        except TRANSIENT_API_EXCEPTIONS as e:
            major_failure_count += 1
            active_key_short = available_api_keys[current_api_key_index][-4:]
            console.print(f"    ❌ Fallimento definitivo traduzione con Chiave ...{active_key_short}. Errore: {e}. Fallimenti consecutivi: {major_failure_count}/{MAX_MAJOR_FAILURES_THRESHOLD}", style="red")
            if args.rotate_on_limit_or_error and rotate_api_key(reason_override="Errore API"):
                continue
            elif major_failure_count >= MAX_MAJOR_FAILURES_THRESHOLD and rotate_api_key():
                continue
            else:
                return text_to_translate
        except Exception as e:
            console.print(f"    🛑 Errore non gestito durante la traduzione: {e}", style="red")
            return text_to_translate

def _extract_json_sample_texts(obj, keys_to_translate, sample_list, path="", match_full=False, limit=FILE_CONTEXT_SAMPLE_SIZE):
    if limit is not None and len(sample_list) >= limit: return
    if isinstance(obj, dict):
        for key, value in obj.items():
            current_path = f"{path}.{key}" if path else key
            is_match = (current_path in keys_to_translate) if match_full else (key in keys_to_translate)
            if is_match and determine_if_translatable(value):
                sample_list.append(str(value))
                if limit is not None and len(sample_list) >= limit: return
            _extract_json_sample_texts(value, keys_to_translate, sample_list, current_path, match_full, limit)
    elif isinstance(obj, list):
        for item in obj: _extract_json_sample_texts(item, keys_to_translate, sample_list, path, match_full, limit)

def should_translate_msgctxt(context_string):
    if not determine_if_translatable(context_string) or '_' in context_string: return False
    if '\t' in context_string: return False
    if re.search(r'<[a-zA-Z/][^>]*>', context_string): return False
    stripped_context = context_string.strip()
    if ' ' not in stripped_context:
        has_digits = any(char.isdigit() for char in stripped_context)
        is_mixed_case = not stripped_context.islower() and not stripped_context.isupper()
        if has_digits or is_mixed_case: return False
    return False

def traduci_testo_po(input_file, output_file, args):
    global current_file_context, total_entries_translated, user_command_skip_file, current_file_total_entries, current_file_processed_entries
    current_file_context = None
    file_basename = os.path.basename(input_file)
    try: po_file = polib.pofile(input_file, encoding=args.encoding)
    except Exception as e: log_critical_error_and_exit(f"Impossibile leggere o parsare il file PO '{input_file}': {e}")
    
    all_texts_in_file = []
    for entry in po_file:
        if determine_if_translatable(entry.msgid): all_texts_in_file.append(entry.msgid)
        if should_translate_msgctxt(entry.msgctxt): all_texts_in_file.append(entry.msgctxt)

    all_translations_cached = all(
        json.dumps((text, args.source_lang, args.target_lang, args.game_name, args.prompt_context), ensure_ascii=False) in translation_cache
        for text in all_texts_in_file
    ) if all_texts_in_file else False
    
    entries_to_process = [entry for entry in po_file if determine_if_translatable(entry.msgid) or should_translate_msgctxt(entry.msgctxt)]
    current_file_total_entries = len(entries_to_process)
    
    if max_entries_limit is not None and max_entries_limit > 0 and current_file_total_entries > max_entries_limit:
        console.print(f"⏭️  [yellow]SKIP:[/] Il file '{file_basename}' ha {current_file_total_entries} entry, superando il limite di {max_entries_limit}. Verrà saltato.")
        write_to_log(f"SKIP (MAX ENTRIES): File '{file_basename}' ({current_file_total_entries} > {max_entries_limit}) saltato.")
        return

    current_file_processed_entries = 0

    try:
        if args.enable_file_context and not all_translations_cached:
            sample_limit = None if args.full_context_sample else FILE_CONTEXT_SAMPLE_SIZE
            sample_texts = [entry.msgid for entry in po_file if determine_if_translatable(entry.msgid)][:sample_limit]
            if sample_texts:
                console.print(f"  Analisi di {len(sample_texts)} frasi per generare il contesto del file...")
                current_file_context = generate_file_context("\n".join(sample_texts), file_basename, args)
        
        if all_translations_cached and args.enable_file_context:
             console.print(f"  Tutte le traduzioni per '{file_basename}' sono già in cache. Salto la generazione del contesto.")

        with Progress(
            SpinnerColumn(),
            TextColumn("[progress.description]{task.description}"),
            BarColumn(),
            TextColumn("[progress.percentage]{task.percentage:>3.0f}%"),
            TextColumn("• {task.completed}/{task.total} •"),
            TimeElapsedColumn(), "•", TimeRemainingColumn(),
            console=console
        ) as progress:
            task = progress.add_task(f"[cyan]PO '{os.path.basename(input_file)}'[/]", total=current_file_total_entries)
            
            for entry in po_file:
                with command_lock:
                    if user_command_skip_file: raise KeyboardInterrupt
                
                translated_this_entry = False
                original_context = entry.msgctxt
                context_for_prompt, context_is_translatable_prose = None, should_translate_msgctxt(original_context)
                
                context_log = f"PO '{file_basename}' | Riga {entry.linenum} (ctx)"
                msgid_log = f"PO '{file_basename}' | Riga {entry.linenum} (msgid)"
                
                if context_is_translatable_prose:
                    progress.update(task, description=f"[cyan]PO '{os.path.basename(input_file)}'[/] | Riga {entry.linenum} (ctx)")
                    translated_context = get_translation_from_api(original_context, context_log, args)
                    entry.msgctxt = translated_context
                    context_for_prompt = translated_context
                    total_entries_translated += 1
                    translated_this_entry = True

                elif original_context: context_for_prompt = original_context
                
                if entry.msgid and determine_if_translatable(entry.msgid):
                    progress.update(task, description=f"[cyan]PO '{os.path.basename(input_file)}'[/] | Riga {entry.linenum} (msgid)")
                    final_dynamic_context = " - ".join(filter(None, [f"Contesto Generale: {current_file_context}" if current_file_context else None, f"Contesto Entry: {context_for_prompt}" if context_for_prompt else None]))
                    
                    original_msgid = entry.msgid
                    translated_text = get_translation_from_api(original_msgid, msgid_log, args, dynamic_context=final_dynamic_context)
                    
                    entry.msgstr = translated_text
                    total_entries_translated += 1
                    translated_this_entry = True
                    
                elif entry.msgid: entry.msgstr = entry.msgid

                if translated_this_entry:
                    current_file_processed_entries +=1
                    progress.advance(task)

    except KeyboardInterrupt:
        with command_lock: is_skip_command = user_command_skip_file
        if is_skip_command: console.print(f"\n[yellow]➡️  Comando 'skip file' ricevuto. Salvataggio dei progressi per '{file_basename}'...[/]")
        else: console.print(f"\n[red]🛑 Interruzione da tastiera. Salvataggio dei progressi per '{file_basename}'...[/]"); raise
    finally:
        try:
            po_file.save(output_file)
            console.print(f"\n✅ File salvato in: '[green]{output_file}[/]'")
            check_and_save_cache()
        except Exception as e: log_critical_error_and_exit(f"Impossibile scrivere il file di output '{output_file}': {e}")
        with command_lock:
            if user_command_skip_file: user_command_skip_file = False

def traduci_testo_json(input_file, output_file, args):
    global current_file_context, total_entries_translated, user_command_skip_file, current_file_total_entries, current_file_processed_entries
    current_file_context = None
    file_basename = os.path.basename(input_file)
    try:
        with open(input_file, 'r', encoding=args.encoding) as f: data = json.load(f)
    except (json.JSONDecodeError, FileNotFoundError) as e: log_critical_error_and_exit(f"Impossibile leggere o parsare il file JSON '{input_file}': {e}")
    
    keys_to_translate = {k.strip() for k in args.json_keys.split(',')}
    translated_texts_for_only_output = []
    
    all_texts_in_file = []
    _extract_json_sample_texts(data, keys_to_translate, all_texts_in_file, match_full=args.match_full_json_path, limit=None)

    all_translations_cached = all(
        json.dumps((text, args.source_lang, args.target_lang, args.game_name, args.prompt_context), ensure_ascii=False) in translation_cache
        for text in all_texts_in_file
    ) if all_texts_in_file else False

    progress = None
    task = None
    def _translate_recursive(obj, path=""):
        nonlocal progress, task
        global total_entries_translated, current_file_processed_entries
        if isinstance(obj, dict):
            for key, value in list(obj.items()):
                with command_lock:
                    if user_command_skip_file: raise KeyboardInterrupt
                current_path = f"{path}.{key}" if path else key
                is_match = (current_path in keys_to_translate) if args.match_full_json_path else (key in keys_to_translate)
                if is_match and determine_if_translatable(value):
                    progress.update(task, description=f"[cyan]JSON '{file_basename}'[/] | Chiave: {current_path[:30]}...")
                    original_value = value
                    context_log = f"JSON '{file_basename}' | Chiave: '{current_path}'"
                    translated_value = get_translation_from_api(original_value, context_log, args, dynamic_context=current_file_context)
                    
                    obj[key] = translated_value
                    if args.translation_only_output: translated_texts_for_only_output.append(translated_value)
                    progress.advance(task)
                    total_entries_translated += 1
                    current_file_processed_entries += 1
                _translate_recursive(value, current_path)
        elif isinstance(obj, list):
            for i, item in enumerate(obj):
                _translate_recursive(item, f"{path}[{i}]")
    
    try:
        if args.enable_file_context and not all_translations_cached:
            sample_texts = all_texts_in_file[:None if args.full_context_sample else FILE_CONTEXT_SAMPLE_SIZE]
            if sample_texts:
                console.print(f"  Analisi di {len(sample_texts)} voci per generare il contesto del file...")
                current_file_context = generate_file_context("\n".join(sample_texts), file_basename, args)

        if all_translations_cached and args.enable_file_context:
            console.print(f"  Tutte le traduzioni per '{file_basename}' sono già in cache. Salto la generazione del contesto.")

        current_file_total_entries = len([text for text in all_texts_in_file if determine_if_translatable(text)])

        if max_entries_limit is not None and max_entries_limit > 0 and current_file_total_entries > max_entries_limit:
            console.print(f"⏭️  [yellow]SKIP:[/] Il file '{file_basename}' ha {current_file_total_entries} entry, superando il limite di {max_entries_limit}. Verrà saltato.")
            write_to_log(f"SKIP (MAX ENTRIES): File '{file_basename}' ({current_file_total_entries} > {max_entries_limit}) saltato.")
            return

        current_file_processed_entries = 0
        with Progress(
            SpinnerColumn(),
            TextColumn("[progress.description]{task.description}"),
            BarColumn(),
            TextColumn("[progress.percentage]{task.percentage:>3.0f}%"),
            TextColumn("• {task.completed}/{task.total} •"),
            TimeElapsedColumn(), "•", TimeRemainingColumn(),
            console=console
        ) as progress_instance:
            progress = progress_instance
            task = progress.add_task(f"[cyan]JSON '{file_basename}'[/]", total=current_file_total_entries)
            _translate_recursive(data)
    except KeyboardInterrupt:
        with command_lock: is_skip_command = user_command_skip_file
        if is_skip_command: console.print(f"\n[yellow]➡️  Comando 'skip file' ricevuto. Salvataggio dei progressi per '{file_basename}'...[/]")
        else: console.print(f"\n[red]🛑 Interruzione da tastiera. Salvataggio dei progressi per '{file_basename}'...[/]"); raise
    finally:
        try:
            with open(output_file, 'w', encoding=args.encoding) as f:
                if args.translation_only_output: f.write("\n".join(translated_texts_for_only_output) + "\n")
                else: json.dump(data, f, ensure_ascii=False, indent=4)
            console.print(f"\n✅ File salvato in: '[green]{output_file}[/]'")
            check_and_save_cache()
        except Exception as e: log_critical_error_and_exit(f"Impossibile scrivere il file di output '{output_file}': {e}")
        with command_lock:
            if user_command_skip_file: user_command_skip_file = False

def traduci_testo_csv(input_file, output_file, args):
    global current_file_context, total_entries_translated, user_command_skip_file, current_file_total_entries, current_file_processed_entries
    current_file_context = None
    file_basename = os.path.basename(input_file)
    try:
        with open(input_file, 'r', encoding=args.encoding, newline='') as infile: rows = list(csv.reader(infile, delimiter=args.delimiter))
    except Exception as e: log_critical_error_and_exit(f"Impossibile leggere il file CSV '{input_file}': {e}")
    
    header = rows[0] if rows else None
    data_rows = rows[1:] if header else rows
    output_rows = [row[:] for row in rows]
    
    if args.resume and os.path.exists(output_file):
        try:
            with open(output_file, 'r', encoding=args.encoding, newline='') as resumed_file:
                resumed_rows = list(csv.reader(resumed_file, delimiter=args.delimiter))
                if len(resumed_rows) == len(output_rows):
                    output_rows = resumed_rows
                    console.print(f"  Resume mode: Caricate {len(output_rows)} righe da '{output_file}'.")
        except Exception as e: console.print(f"⚠️  [yellow]Attenzione: Impossibile leggere il file di resume '{output_file}': {e}.[/]")
    
    all_texts_in_file = [
        row[args.translate_col] for row in data_rows 
        if len(row) > args.translate_col and determine_if_translatable(row[args.translate_col])
    ]
    all_translations_cached = all(
        json.dumps((text, args.source_lang, args.target_lang, args.game_name, args.prompt_context), ensure_ascii=False) in translation_cache
        for text in all_texts_in_file
    ) if all_texts_in_file else False
    
    translated_texts_for_only_output = []
    
    try:
        if args.enable_file_context and not all_translations_cached:
            sample_limit = None if args.full_context_sample else FILE_CONTEXT_SAMPLE_SIZE
            sample_texts = [row[args.translate_col] for row in data_rows if len(row) > args.translate_col and determine_if_translatable(row[args.translate_col])][:sample_limit]
            if sample_texts:
                console.print(f"  Analisi di {len(sample_texts)} righe per generare il contesto...")
                current_file_context = generate_file_context("\n".join(sample_texts), file_basename, args)
        
        if all_translations_cached and args.enable_file_context:
             console.print(f"  Tutte le traduzioni per '{file_basename}' sono già in cache. Salto generazione contesto.")

        rows_to_translate_indices = []
        output_data_rows = output_rows[1:] if header else output_rows
        for i, row in enumerate(output_data_rows):
            is_already_translated = args.resume and len(row) > args.output_col and row[args.output_col].strip() and (args.output_col != args.translate_col or row[args.output_col] != data_rows[i][args.translate_col])
            needs_translation = len(row) > args.translate_col and determine_if_translatable(row[args.translate_col])
            if needs_translation and not is_already_translated:
                rows_to_translate_indices.append(i)
        
        current_file_total_entries = len(rows_to_translate_indices)
        
        if max_entries_limit is not None and max_entries_limit > 0 and current_file_total_entries > max_entries_limit:
            console.print(f"⏭️  [yellow]SKIP:[/] Il file '{file_basename}' ha {current_file_total_entries} entry, superando il limite di {max_entries_limit}. Verrà saltato.")
            write_to_log(f"SKIP (MAX ENTRIES): File '{file_basename}' ({current_file_total_entries} > {max_entries_limit}) saltato.")
            return
        
        current_file_processed_entries = 0
        
        with Progress(
            SpinnerColumn(),
            TextColumn("[progress.description]{task.description}"),
            BarColumn(),
            TextColumn("[progress.percentage]{task.percentage:>3.0f}%"),
            TextColumn("• {task.completed}/{task.total} •"),
            TimeElapsedColumn(), "•", TimeRemainingColumn(),
            console=console
        ) as progress:
            task = progress.add_task(f"[cyan]CSV '{file_basename}'[/]", total=current_file_total_entries)
            
            for i in rows_to_translate_indices:
                row = output_data_rows[i]
                display_row_num = i + (2 if header else 1)
                with command_lock:
                    if user_command_skip_file: raise KeyboardInterrupt
                original_text = row[args.translate_col]
                progress.update(task, description=f"[cyan]CSV '{file_basename}'[/] | Riga {display_row_num}")
                context_log = f"CSV '{file_basename}' | Riga {display_row_num}"
                translated_text = get_translation_from_api(original_text, context_log, args, dynamic_context=current_file_context)
                
                while len(row) <= args.output_col: row.append('')
                row[args.output_col] = translated_text
                if args.translation_only_output: translated_texts_for_only_output.append(translated_text)
                progress.advance(task)
                total_entries_translated += 1
                current_file_processed_entries += 1

    except KeyboardInterrupt:
        with command_lock: is_skip_command = user_command_skip_file
        if is_skip_command: console.print(f"\n[yellow]➡️  Comando 'skip file' ricevuto. Salvataggio dei progressi per '{file_basename}'...[/]")
        else: console.print(f"\n[red]🛑 Interruzione da tastiera. Salvataggio dei progressi per '{file_basename}'...[/]"); raise
    finally:
        try:
            with open(output_file, 'w', encoding=args.encoding, newline='') as outfile:
                if args.translation_only_output:
                    outfile.write("\n".join(translated_texts_for_only_output) + "\n")
                else:
                    writer = csv.writer(outfile, delimiter=args.delimiter, quoting=csv.QUOTE_MINIMAL)
                    writer.writerows(output_rows)
            console.print(f"\n✅ File salvato in: '[green]{output_file}[/]'")
            check_and_save_cache()
        except Exception as e: log_critical_error_and_exit(f"Impossibile scrivere il file di output '{output_file}': {e}")
        with command_lock:
            if user_command_skip_file: user_command_skip_file = False

def process_files_recursively(args):
    global user_command_skip_file, total_files_translated, current_file_total_entries, current_file_processed_entries
    base_input_dir = os.path.abspath(args.input)
    base_output_dir = f"{base_input_dir}_tradotto" if os.path.basename(base_input_dir) != "input" else os.path.join(os.path.dirname(base_input_dir) or '.', "tradotto")
    console.print(f"\nScansione della cartella '[blue]{base_input_dir}[/]' per i file *.{args.file_type}...")
    console.print(f"Output salvato in: '[blue]{base_output_dir}[/]'")
    os.makedirs(base_output_dir, exist_ok=True)
    file_paths_to_process = [os.path.join(r, f) for r, _, files in os.walk(base_input_dir) for f in files if f.endswith(f'.{args.file_type}')]
    total_files_found = len(file_paths_to_process)
    console.print(f"✅ Trovati {total_files_found} file da elaborare.")
    if total_files_found == 0:
        console.print(f"🛑 [red]Nessun file *.{args.file_type} trovato nella cartella specificata. Uscita.[/]")
        return
    for file_index, input_path in enumerate(file_paths_to_process):
        if graceful_exit_requested.is_set():
            console.print("\n[yellow]🛑 Uscita graduale richiesta. Interruzione del processo.[/]")
            break
        with command_lock:
            if user_command_skip_file:
                console.print(f"[yellow]➡️  Comando 'skip file' rilevato. Saltando: '{os.path.basename(input_path)}'.[/]")
                continue
        
        current_file_total_entries, current_file_processed_entries = 0, 0
        if script_args.interactive: check_and_wait_if_paused(f"Inizio file: {os.path.basename(input_path)}")
        console.print(Rule(f"File [{file_index + 1}/{total_files_found}]", style="bold cyan"))
        
        relative_path_dir = os.path.relpath(os.path.dirname(input_path), base_input_dir)
        current_output_dir = os.path.join(base_output_dir, relative_path_dir) if relative_path_dir != '.' else base_output_dir
        os.makedirs(current_output_dir, exist_ok=True)
        filename = os.path.basename(input_path)
        output_filename = f"{os.path.splitext(filename)[0]}_trads.txt" if args.translation_only_output else filename
        output_path = os.path.join(current_output_dir, output_filename)
        
        if args.resume and os.path.exists(output_path) and args.file_type != 'csv':
             console.print(f"⚠️  [yellow]Attenzione: Resume mode per '{args.file_type}' potrebbe sovrascrivere il file '{output_path}'.[/]")
        try:
            if args.file_type == 'csv': traduci_testo_csv(input_path, output_path, args)
            elif args.file_type == 'json': traduci_testo_json(input_path, output_path, args)
            elif args.file_type == 'po': traduci_testo_po(input_path, output_path, args)
            
            # La funzione `traduci_...` ritorna None se il file è stato saltato
            if os.path.exists(output_path):
                total_files_translated += 1
                if args.telegram:
                    telegram_bot.send_telegram_notification(f"✅ *File Completato!*\n`{filename}` è stato tradotto e salvato.")
        
        except KeyboardInterrupt:
            raise
        except Exception as e:
            error_msg = f"Errore irreversibile durante l'elaborazione del file '{filename}': {e}"
            console.print(f"🛑 [red]{error_msg}[/]")
            write_to_log(f"ERRORE CRITICO FILE: {error_msg}. Il file verrà saltato.")

def check_for_updates():
    try:
        version_url = f"https://raw.githubusercontent.com/{GITHUB_REPO}/main/version.txt"
        console.print("ℹ️  Controllo aggiornamenti in corso...", style="dim")
        response = requests.get(version_url, timeout=5)
        response.raise_for_status()

        latest_version_str = response.text.strip()
        current_version = version.parse(CURRENT_SCRIPT_VERSION)
        latest_version = version.parse(latest_version_str)

        if latest_version > current_version:
            update_message = Text.from_markup(
                f"[bold]🚀 Nuova versione disponibile! ({latest_version_str})[/bold]\n\n"
                f"La tua versione attuale è la [bold red]{CURRENT_SCRIPT_VERSION}[/bold red].\n"
                "È [bold]fortemente consigliato[/bold] aggiornare lo script per ottenere le ultime funzionalità e correzioni.\n\n"
                f"Scarica l'ultima versione da:\n[link=https://github.com/{GITHUB_REPO}]https://github.com/{GITHUB_REPO}[/link]"
            )
            console.print(Panel(update_message, title="[yellow]Aggiornamento Consigliato[/]", border_style="yellow", padding=(1, 2)))

            # Pausa in attesa dell'azione dell'utente
            console.input("\n[bold]Premi Invio per continuare comunque o premi CTRL+C per uscire e aggiornare.[/bold]")
            console.print() # Aggiunge uno spazio per pulizia
        else:
            console.print("✅ La tua versione dello script è aggiornata.", style="dim")

    except requests.exceptions.RequestException:
        console.print("⚠️  Impossibile verificare la presenza di aggiornamenti (errore di rete).", style="yellow dim")
    except KeyboardInterrupt:
        # Gestisce l'uscita tramite CTRL+C in modo pulito
        console.print("\n🛑 Esecuzione annullata dall'utente per procedere con l'aggiornamento. A presto!")
        sys.exit()
    except Exception:
        # Fallisce silenziosamente per altri errori
        console.print("⚠️  Impossibile completare la verifica degli aggiornamenti.", style="yellow dim")
    finally:
        console.print()


if __name__ == "__main__":
    console.print(ALUMEN_ASCII_ART, style="bold cyan")
    console.print("Benvenuto in Alumen, traduttore automatico potenziato da Gemini.\n")
    check_for_updates()
    args_parsed_main = get_script_args_updated()
    if not os.path.isdir(args_parsed_main.input):
        log_critical_error_and_exit(f"La cartella di input specificata '{args_parsed_main.input}' non esiste.")
    if args_parsed_main.file_type == 'json' and not args_parsed_main.json_keys:
        log_critical_error_and_exit("Per --file-type 'json', è obbligatorio specificare --json-keys.")

    script_is_paused.set()
    start_time = time.time()
    if args_parsed_main.enable_file_log: setup_log_file()

    telegram_app = None
    if args_parsed_main.telegram:
        telegram_app = telegram_bot.start_bot()

    initialize_api_keys_and_model()
    load_persistent_cache()

    cmd_thread = None
    if args_parsed_main.interactive:
        cmd_thread = threading.Thread(target=command_input_thread_func, daemon=True)
        cmd_thread.start()
    interrupted = False
    try:
        process_files_recursively(args_parsed_main)
    except KeyboardInterrupt:
        interrupted = True
        console.print("\n\n[bold red]🛑 Interruzione da tastiera (Ctrl+C) rilevata. Chiusura controllata in corso...[/]")
        write_to_log("INTERRUZIONE UTENTE: Rilevato Ctrl+C. Avvio chiusura controllata.")
    finally:
        if telegram_app:
            console.print("\n[telegram] Tentativo di chiusura della connessione Telegram...[/]")
            telegram_bot.stop_bot()
            console.print("[telegram] Connessione Telegram terminata.[/]")
        save_persistent_cache()
        show_stats(title="📊 STATISTICHE FINALI DI ESECUZIONE")
        write_to_log(f"--- FINE Sessione Log: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')} ---\n")
        if interrupted:
            console.print("\n[bold yellow]Script Alumen terminato a causa di un'interruzione da parte dell'utente.[/]")
        else:
            console.print("\n[bold green]Lavoro completato. Script Alumen terminato.[/]")