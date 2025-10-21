import os
import json
import argparse
import csv
import polib
import re
from argparse_color_formatter import ColorHelpFormatter

# Nome del file di cache che verrà generato
CACHE_FILE_NAME = "alumen_cache.json"

# --- Funzioni di supporto copiate da Alumen.py ---

def log_critical_error_and_exit(message):
    """Stampa un errore critico ed esce."""
    print(f"🛑 ERRORE CRITICO: {message}")
    exit(1)

def determine_if_translatable(text_value):
    """Determina se una stringa è testo traducibile (non un ID, numero, ecc.)."""
    if not isinstance(text_value, str): return False
    text_value_stripped = text_value.strip()
    if not text_value_stripped or text_value_stripped.isdigit() or re.match(r'^[\W_]+$', text_value_stripped) or "\\u" in text_value_stripped:
        return False
    if '_' in text_value_stripped and ' ' not in text_value_stripped:
        return False
    return True

def get_cache_key(original_text, args):
    """Genera la chiave di cache ESATTAMENTE come fa Alumen.py."""
    cache_key_tuple = (original_text, args.source_lang, args.target_lang, args.game_name, args.prompt_context)
    return json.dumps(cache_key_tuple, ensure_ascii=False)

# --- Logica di Estrazione per Tipo di File ---

def extract_cache_from_csv(source_path, target_path, cache_map, args):
    """Estrae le coppie di cache dai file CSV."""
    try:
        with open(source_path, 'r', encoding=args.encoding, newline='') as sf:
            source_rows = list(csv.reader(sf, delimiter=args.delimiter))
        with open(target_path, 'r', encoding=args.encoding, newline='') as tf:
            target_rows = list(csv.reader(tf, delimiter=args.delimiter))
    except FileNotFoundError:
        print(f"    ⚠️  Target file mancante: '{target_path}'. Salto.")
        return
    except Exception as e:
        print(f"    ❌ Errore lettura CSV: {e}. Salto.")
        return

    if len(source_rows) != len(target_rows):
        print(f"    ⚠️  Attenzione: Numero di righe non corrispondente ({len(source_rows)} vs {len(target_rows)}). Procedo, ma potrebbero esserci errori.")

    start_index = 0
    if source_rows and target_rows and source_rows[0] != target_rows[0] and not args.no_header:
         print("    ℹ️  Saltata la prima riga (presunto header).")
         start_index = 1

    rows_to_process = min(len(source_rows), len(target_rows))
    entries_added = 0
    entries_skipped = 0 # MODIFICA: Aggiunto contatore per le voci saltate
    
    for i in range(start_index, rows_to_process):
        source_row = source_rows[i]
        target_row = target_rows[i]
        
        if len(source_row) <= args.source_col or len(target_row) <= args.target_col:
            continue

        original_text = source_row[args.source_col]
        translated_text = target_row[args.target_col]

        if determine_if_translatable(original_text) and translated_text.strip():
            key = get_cache_key(original_text, args)
            # MODIFICA: Aggiunto controllo per evitare di aggiungere chiavi esistenti
            if key not in cache_map:
                cache_map[key] = translated_text
                entries_added += 1
            else:
                entries_skipped += 1
            
    # MODIFICA: Aggiornato il messaggio di output
    print(f"    ✅ Aggiunte {entries_added} nuove voci. Saltate {entries_skipped} voci già presenti in cache.")

def extract_cache_from_json(source_path, target_path, cache_map, args):
    """Estrae le coppie di cache dai file JSON."""
    try:
        with open(source_path, 'r', encoding=args.encoding) as sf:
            source_data = json.load(sf)
        with open(target_path, 'r', encoding=args.encoding) as tf:
            target_data = json.load(tf)
    except FileNotFoundError:
        print(f"    ⚠️  Target file mancante: '{target_path}'. Salto.")
        return
    except json.JSONDecodeError:
        print(f"    ❌ Errore di parsing JSON nei file. Salto.")
        return
    except Exception as e:
        print(f"    ❌ Errore lettura JSON: {e}. Salto.")
        return

    keys_to_translate = {k.strip() for k in args.json_keys.split(',')}
    entries_added = 0
    entries_skipped = 0 # MODIFICA: Aggiunto contatore per le voci saltate

    def _traverse_and_extract(source_obj, target_obj, path=""):
        nonlocal entries_added, entries_skipped
        if isinstance(source_obj, dict) and isinstance(target_obj, dict):
            for key, source_value in source_obj.items():
                current_path = f"{path}.{key}" if path else key
                target_value = target_obj.get(key)
                
                is_match = (current_path in keys_to_translate) if args.match_full_json_path else (key in keys_to_translate)
                
                if is_match and determine_if_translatable(source_value) and isinstance(target_value, str) and target_value.strip():
                    key_cache = get_cache_key(source_value, args)
                    # MODIFICA: Aggiunto controllo per evitare di aggiungere chiavi esistenti
                    if key_cache not in cache_map:
                        cache_map[key_cache] = target_value
                        entries_added += 1
                    else:
                        entries_skipped += 1
                
                if key in target_obj:
                    _traverse_and_extract(source_value, target_value, current_path)
                    
        elif isinstance(source_obj, list) and isinstance(target_obj, list):
            for i in range(min(len(source_obj), len(target_obj))):
                 _traverse_and_extract(source_obj[i], target_obj[i], path)

    _traverse_and_extract(source_data, target_data)
    # MODIFICA: Aggiornato il messaggio di output
    print(f"    ✅ Aggiunte {entries_added} nuove voci. Saltate {entries_skipped} voci già presenti in cache.")


def extract_cache_from_po(target_path, cache_map, args):
    """Estrae le coppie di cache dai file PO (msgid -> msgstr)."""
    try:
        po_file = polib.pofile(target_path, encoding=args.encoding)
    except FileNotFoundError:
        print(f"    ⚠️  Target file mancante: '{target_path}'. Salto.")
        return
    except Exception as e:
        print(f"    ❌ Errore lettura PO: {e}. Salto.")
        return

    entries_added = 0
    entries_skipped = 0 # MODIFICA: Aggiunto contatore per le voci saltate
    for entry in po_file:
        if entry.msgid and entry.msgstr and determine_if_translatable(entry.msgid):
            original_text = entry.msgid
            translated_text = entry.msgstr
            key = get_cache_key(original_text, args)
            # MODIFICA: Aggiunto controllo per evitare di aggiungere chiavi esistenti
            if key not in cache_map:
                cache_map[key] = translated_text
                entries_added += 1
            else:
                entries_skipped += 1
            
    # MODIFICA: Aggiornato il messaggio di output
    print(f"    ✅ Aggiunte {entries_added} nuove voci. Saltate {entries_skipped} voci già presenti in cache.")

# --- Processo Principale ---

def process_files_recursively(args, cache_map):
    """Scansiona le cartelle, trova i file e avvia il processo di estrazione."""
    source_dir = os.path.abspath(args.source_dir)
    target_dir = os.path.abspath(args.target_dir)

    print(f"\nInizio scansione per file *.{args.file_type} dalla sorgente: '{source_dir}'")
    
    file_count = 0
    for root_dir, dirs_list, files_list in os.walk(source_dir):
        relative_path = os.path.relpath(root_dir, source_dir)
        current_target_dir = os.path.join(target_dir, relative_path)
        
        files_to_process = [f for f in files_list if f.endswith(f'.{args.file_type}')]
        
        for filename in files_to_process:
            file_count += 1
            source_path = os.path.join(root_dir, filename)
            target_path = os.path.join(current_target_dir, filename)
            
            print(f"\n[{file_count}] Elaborazione: {os.path.join(relative_path, filename)}")
            
            if args.file_type == 'csv':
                # FIX: Rimosso controllo 'required' che falliva, ora è gestito qui
                if args.source_col is None or args.target_col is None:
                    log_critical_error_and_exit("Per i file CSV, è obbligatorio specificare --source-col e --target-col.")
                extract_cache_from_csv(source_path, target_path, cache_map, args)
            elif args.file_type == 'json':
                if not args.json_keys:
                    log_critical_error_and_exit("Per i file JSON, è obbligatorio specificare --json-keys.")
                extract_cache_from_json(source_path, target_path, cache_map, args)
            elif args.file_type == 'po':
                extract_cache_from_po(target_path, cache_map, args)

def main():
    parser = argparse.ArgumentParser(
        description="Cache Extractor - Script per costruire la cache di traduzione da file sorgente e target esistenti.",
        formatter_class=ColorHelpFormatter
    )

    file_group = parser.add_argument_group('\033[96mConfigurazione File\033[0m')
    file_group.add_argument("--source-dir", type=str, required=True, help="\033[97mPercorso della cartella contenente i file ORIGINALI (sorgente).\033[0m")
    file_group.add_argument("--target-dir", type=str, required=True, help="\033[97mPercorso della cartella contenente i file TRADOTTI (target).\033[0m")
    file_group.add_argument("--file-type", type=str, default="csv", choices=['csv', 'json', 'po'], help="\033[97mTipo di file da elaborare: 'csv', 'json' o 'po'. Default: 'csv'\033[0m")
    file_group.add_argument("--encoding", type=str, default="utf-8", help="\033[97mCodifica caratteri dei file. Default: 'utf-8'\033[0m")

    csv_options_group = parser.add_argument_group('\033[96mOpzioni Specifiche per CSV\033[0m')
    csv_options_group.add_argument("--delimiter", type=str, default=",", help="\033[97m[Solo CSV] Carattere delimitatore. Default: ','\033[0m")
    csv_options_group.add_argument("--source-col", type=int, help="\033[97m[Solo CSV] Indice (0-based) della colonna con il testo ORIGINALE nel file sorgente.\033[0m")
    csv_options_group.add_argument("--target-col", type=int, help="\033[97m[Solo CSV] Indice (0-based) della colonna con il testo TRADOTTO nel file target.\033[0m")
    csv_options_group.add_argument("--no-header", action="store_true", help="\033[97m[Solo CSV] Non saltare la prima riga.\033[0m")
    
    json_options_group = parser.add_argument_group('\033[96mOpzioni Specifiche per JSON\033[0m')
    json_options_group.add_argument("--json-keys", type=str, default=None, help="\033[97m[Solo JSON, Obbligatorio] Elenco di chiavi (separate da virgola) che sono state tradotte (es. 'key1,path.to.key2').\033[0m")
    json_options_group.add_argument("--match-full-json-path", action="store_true", help="\033[97m[Solo JSON] Richiede la corrispondenza del percorso completo della chiave (es. 'parent.child.key'), non solo del nome.\033[0m")


    translation_group = parser.add_argument_group('\033[96mParametri di Cache (DEVONO CORRISPONDERE A QUELLI USATI PER LA TRADUZIONE)\033[0m')
    translation_group.add_argument("--game-name", type=str, default="un videogioco generico", help="\033[97mNome del gioco usato per la contestualizzazione.\033[0m")
    translation_group.add_argument("--source-lang", type=str, default="inglese", help="\033[97mLingua originale del testo.\033[0m")
    translation_group.add_argument("--target-lang", type=str, default="italiano", help="\033[97mLingua di destinazione.\033[0m")
    translation_group.add_argument("--prompt-context", type=str, default=None, help="\033[97MInformazione contestuale extra usata nel prompt.\033[0m")
    translation_group.add_argument("--output-cache-file", type=str, default=CACHE_FILE_NAME, help=f"\033[97mNome del file di cache da creare. Default: '{CACHE_FILE_NAME}'\033[0m")
    translation_group.add_argument("--append", action="store_true", help="\033[97mAggiunge le nuove voci a una cache esistente invece di sovrascriverla.\033[0m")

    args = parser.parse_args()

    if args.delimiter == '\\t':
        args.delimiter = '\t'
        
    cache_map = {}
    
    if args.append and os.path.exists(args.output_cache_file):
        try:
            with open(args.output_cache_file, 'r', encoding='utf-8') as f:
                cache_map = json.load(f)
            print(f"✅ Cache esistente caricata da '{args.output_cache_file}' con {len(cache_map)} voci.")
        except json.JSONDecodeError:
            print("⚠️  Attenzione: Impossibile decodificare la cache esistente. Inizio con una cache vuota.")

    try:
        process_files_recursively(args, cache_map)
    except KeyboardInterrupt:
        print("\n🛑 Interruzione da tastiera (Ctrl+C). Salvataggio cache parziale...")
    
    print("\n--- Salvataggio Cache ---")
    
    if not cache_map:
        print("ℹ️  Nessuna voce di cache estratta. File di cache non creato.")
        return

    try:
        with open(args.output_cache_file, 'w', encoding='utf-8') as f:
            json.dump(cache_map, f, ensure_ascii=False, indent=4)
        
        print(f"🎉 Processo completato. {len(cache_map)} voci di cache salvate in '{args.output_cache_file}'.")
        print("Ora puoi usare questo file con il tuo script Alumen.py (opzione --persistent-cache).")
    except Exception as e:
        log_critical_error_and_exit(f"Impossibile scrivere il file di cache: {e}")

if __name__ == "__main__":
    main()