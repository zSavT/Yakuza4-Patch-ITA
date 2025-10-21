# Alumen

**Alumen** è uno script Python da riga di comando progettato per automatizzare la traduzione di grandi quantità di testi contenuti in file CSV, JSON e PO. Sfrutta i modelli linguistici di Google **Gemini** per fornire traduzioni accurate e contestualizzate, con un focus su flessibilità, robustezza e controllo remoto.

## Indice

- [Alumen](#alumen)
  - [Indice](#indice)
  - [Funzionalità Principali](#funzionalità-principali)
- [Storia\\Creazione Progetto](#storiacreazione-progetto)
  - [Utilizzo](#utilizzo)
    - [Prerequisiti - Installazione e Configurazione](#prerequisiti---installazione-e-configurazione)
      - [Configurazione Telegram (Opzionale)](#configurazione-telegram-opzionale)
    - [Argomenti da Riga di Comando](#argomenti-da-riga-di-comando)
      - [Configurazione API e Modello](#configurazione-api-e-modello)
      - [Configurazione File e Formato](#configurazione-file-e-formato)
      - [Input/Output e Formato CSV](#inputoutput-e-formato-csv)
      - [Input/Output e Formato JSON](#inputoutput-e-formato-json)
      - [Parametri di Traduzione](#parametri-di-traduzione)
      - [A Capo Automatico (Word Wrapping)](#a-capo-automatico-word-wrapping)
      - [Utilità e Modalità Interattiva](#utilità-e-modalità-interattiva)
    - [Modalità Interattiva](#modalità-interattiva)
    - [Esempi di Utilizzo](#esempi-di-utilizzo)
      - [1. Traduzione PO con Contesto, Limiti e Controllo Telegram](#1-traduzione-po-con-contesto-limiti-e-controllo-telegram)
      - [2. Traduzione CSV standard con log e API multipla](#2-traduzione-csv-standard-con-log-e-api-multipla)
      - [3. Traduzione JSON con percorso completo e wrapping](#3-traduzione-json-con-percorso-completo-e-wrapping)
  - [Risultato e Statistiche Finali](#risultato-e-statistiche-finali)
  - [❗ Note Importanti](#-note-importanti)

## Funzionalità Principali

  * **Supporto File Multiplo:** Elabora file `.csv`, `.json`, e `.po` (formato Gettext).
  * **Traduzione Contesto-Consapevole:** Utilizza Gemini per traduzioni che mantengono il contesto del videogioco, preservando tag e placeholder.
  * **Gestione API Avanzata:** Supporta la fornitura di chiavi multiple e la **rotazione automatica** in caso di errori o limiti RPM.
  * **Contesto Intelligente del File:** Analizza il contenuto di ogni file per determinare un contesto generale (es. "Dialoghi di un'ambientazione fantasy") da applicare a tutte le traduzioni di quel file.
  * **Cache Persistente:** Salva le traduzioni per evitare chiamate API ripetute, accelerando le esecuzioni successive.
  * **Modalità Interattiva Potenziata:** Offre un controllo granulare durante l'esecuzione tramite comandi per gestire API, cache, flusso di lavoro e configurazione.
  * **Integrazione con Telegram:** Permette di monitorare i log e inviare comandi allo script in esecuzione da qualsiasi dispositivo tramite un bot Telegram.
  * **Output e Statistiche Avanzate:** Utilizza la libreria `rich` per fornire un'interfaccia chiara, con barre di progresso dettagliate e statistiche finali formattate in tabelle leggibili.
  * **Blacklist di Termini:** Permette di definire un elenco di parole o frasi che non devono mai essere tradotte.

# Storia\\Creazione Progetto

Una primordiale versione dello script è stata realizzata per la patch in italiano per il gioco [Valkyria Chronicles](https://github.com/zSavT/Valkyria-Chronicles-Patch-ITA.git). Successivamente, lo script è mutato per supportare la traduzione dei file del gioco [Yakuza 4](https://github.com/zSavT/Yakuza4-Patch-ITA.git), adottando il più versatile e potente Gemini. Con il tempo, lo script è stato reso generico per adattarsi a qualsiasi progetto, grazie anche alla facilità con cui Gemini ha permesso di potenziarne le funzionalità e l'adattabilità.

## Utilizzo

### Prerequisiti - Installazione e Configurazione

1.  **Python:** Assicurati di avere Python 3.8 o superiore installato.
2.  **Librerie:** Installa tutte le dipendenze necessarie con un unico comando:
    ```bash
    pip install google-generativeai polib argparse-color-formatter rich tenacity "python-telegram-bot[job-queue]"
    ```
3.  **Chiavi API Gemini:** Ottieni una o più chiavi API da Google AI Studio. Puoi fornirle in due modi:
      * Tramite l'argomento `--api` (consigliato per script).
      * Inserendole, una per riga, in un file denominato `api_key.txt` nella stessa directory dello script.

#### Configurazione Telegram (Opzionale)

Per usare il monitoraggio e i comandi da remoto, segui questi passaggi:

1.  **Crea un Bot:** Parla con `@BotFather` su Telegram, usa il comando `/newbot` e segui le istruzioni. Salva il **Token API** che ti viene fornito.
2.  **Ottieni il tuo Chat ID:** Parla con `@userinfobot` su Telegram per ottenere il tuo ID numerico.
3.  **Crea il file `telegram_config.json`**: Nella stessa cartella di `Alumen.py`, crea questo file e inserisci le tue credenziali:
    ```json
    {
      "bot_token": "IL_TUO_TOKEN_API_QUI",
      "chat_id": "IL_TUO_CHAT_ID_NUMERICO_QUI"
    }
    ```
4.  **Avvia lo script** con il flag `--telegram`.

### Argomenti da Riga di Comando

#### Configurazione API e Modello

| Argomento | Descrizione | Default |
| :--- | :--- | :--- |
| **`--api`** | Specifica una o più chiavi API Gemini, separate da virgola. | - |
| **`--model-name`** | Nome del modello Gemini da utilizzare. | `gemini-2.5-flash` |

#### Configurazione File e Formato

| Argomento | Descrizione | Default |
| :--- | :--- | :--- |
| **`--input`** | Percorso della cartella base contenente i file da tradurre. | `input` |
| **`--file-type`** | Tipo di file da elaborare (`csv`, `json` o `po`). | `csv` |
| **`--encoding`** | Codifica caratteri dei file. | `utf-8` |

#### Input/Output e Formato CSV

| Argomento | Descrizione | Default |
| :--- | :--- | :--- |
| **`--delimiter`** | [Solo CSV] Carattere delimitatore. | `,` |
| **`--translate-col`** | [Solo CSV] Indice (0-based) della colonna da tradurre. | `3` |
| **`--output-col`** | [Solo CSV] Indice (0-based) della colonna per il testo tradotto. | `3` |
| **`--max-cols`** | [Solo CSV] Numero massimo di colonne attese per riga (controlli). | Nessun controllo |

#### Input/Output e Formato JSON

| Argomento | Descrizione | Default |
| :--- | :--- | :--- |
| **`--json-keys`** | **[Solo JSON, Obbligatorio]** Elenco di chiavi (separate da virgola) da tradurre. Supporta notazione a punto (es. `key1,path.to.key2`). | - |
| **`--match-full-json-path`** | [Solo JSON] Richiede la corrispondenza del percorso completo della chiave (es. `parent.child.key`). | `False` |

#### Parametri di Traduzione

| Argomento | Descrizione | Default |
| :--- | :--- | :--- |
| **`--game-name`** | Nome del gioco per contestualizzare la traduzione. | `un videogioco generico` |
| **`--source-lang`** | Lingua originale del testo. | `inglese` |
| **`--target-lang`** | Lingua di destinazione. | `italiano` |
| **`--prompt-context`** | Aggiunge un'informazione contestuale extra a ogni prompt. | - |
| **`--custom-prompt`** | Usa un prompt personalizzato. **OBBLIGATORIO:** includere `{text_to_translate}`. | - |
| **`--translation-only-output`** | L'output (per CSV/JSON) conterrà solo i testi tradotti, uno per riga. | `False` |
| **`--rpm`** | Numero massimo di richieste API a Gemini per minuto (Rate Limit). | Nessun limite |
| **`--enable-file-context`** | **Abilita il Contesto Intelligente del File.** Analizza le prime 15 frasi del file per generare un contesto. | `False` |
| **`--full-context-sample`** | **[Necessita `--enable-file-context`]** Utilizza **tutte** le frasi valide nel file per generare il contesto. | `False` |

#### A Capo Automatico (Word Wrapping)

| Argomento | Descrizione | Default |
| :--- | :--- | :--- |
| **`--wrap-at`** | Lunghezza massima della riga per a capo automatico. | - |
| **`--newline-char`** | Carattere da usare per l'a capo automatico. | `\n` |

#### Utilità e Modalità Interattiva

| Argomento | Descrizione | Default |
| :--- | :--- | :--- |
| **`--enable-file-log`** | Attiva la scrittura di un log (`log.txt`). | `False` |
| **`--interactive`** | Abilita comandi interattivi nella console. | `False` |
| **`--telegram`** | Abilita il logging e i comandi tramite un bot Telegram. | `False` |
| **`--resume`** | Tenta di riprendere la traduzione da file parziali (supportato per CSV). Per JSON/PO, riutilizza le traduzioni in cache. | `False` |
| **`--rotate-on-limit-or-error`** | Passa alla API key successiva in caso di errore o limite RPM. | `False` |
| **`--persistent-cache`** | Attiva la cache persistente su file (`alumen_cache.json`). | `False` |

-----

### Modalità Interattiva

Se lo script viene avviato con `--interactive` (o `--telegram`), è possibile inviare comandi per gestire l'esecuzione.

| Comando | Descrizione |
| :--- | :--- |
| **`help`** | Mostra l'elenco di tutti i comandi disponibili. |
| **`pause`** | Mette in pausa l'elaborazione e mostra le statistiche. |
| **`resume`** | Riprende l'elaborazione. |
| **`stop`** | Richiede un'uscita pulita al termine del file corrente. |
| **`skip api`** | Salta l'API key in uso e forza una rotazione. |
| **`skip file`** | Salta il file corrente e passa al successivo. |
| **`stats`** | Mostra le statistiche di esecuzione aggiornate. |
| **`show file_progress`** | Mostra l'avanzamento all'interno del file corrente. |
| **`show rpm`** | Mostra le statistiche RPM correnti (limite, utilizzo, attesa). |
| **`context`** | Visualizza il contesto generato per il file in elaborazione. |
| **`prompt`** | Visualizza l'ultimo prompt di traduzione inviato a Gemini. |
| **`set model <nome>`** | Aggiorna al volo il modello Gemini da utilizzare. |
| **`set rpm <limite>`** | Imposta il limite di Richieste al Minuto (RPM). Usa `0` per disabilitarlo. |
| **`set max_entries <N>`** | Salta automaticamente i file con più di `N` entry da tradurre. Usa `0` per disabilitare. |
| **`add api <chiave>`** | Aggiunge una nuova chiave API alla sessione. |
| **`remove key <indice>`** | Rimuove una chiave API specificando il suo indice. |
| **`list keys`** | Mostra tutte le API key, il loro stato e il numero di chiamate. |
| **`blacklist <indice>`** | Aggiunge una chiave API alla lista nera. |
| **`clear blacklist`** | Rimuove tutte le chiavi dalla lista nera. |
| **`reload cache`** | Ricarica la cache persistente da disco. |
| **`clear cache`** | Svuota la cache di traduzione in memoria. |
| **`save cache`** | Salva immediatamente la cache in memoria su disco. |

-----

### Esempi di Utilizzo

#### 1\. Traduzione PO con Contesto, Limiti e Controllo Telegram

Avvia la traduzione di file PO, limitando le richieste, ruotando le API, abilitando il contesto intelligente e il controllo remoto via Telegram.

```ps1
py .\Alumen.py --file-type "po" --game-name "Yakuza 4 Remastered" --rpm 15 --enable-file-log --interactive --rotate-on-limit-or-error --enable-file-context --persistent-cache --telegram
```

#### 2\. Traduzione CSV standard con log e API multipla

Traduce un CSV specificando la colonna di input e output, utilizzando una delle API Key disponibili e salvando un log.

```ps1
python Alumen.py --file-type csv --translate-col 2 --output-col 4 --enable-file-log --api "key1...,key2..."
```

#### 3\. Traduzione JSON con percorso completo e wrapping

Traduce chiavi specifiche in file JSON, richiedendo la corrispondenza del percorso completo e formattando l'output per non superare gli 80 caratteri.

```ps1
python Alumen.py --file-type json --json-keys "data.title,menu.help_text" --match-full-json-path --wrap-at 80
```

-----

## Risultato e Statistiche Finali

Alla fine dell'esecuzione (o con il comando `stats`), lo script stampa un riepilogo statistico completo, formattato in tabelle chiare e leggibili grazie alla libreria `rich`.

| Statistica | Descrizione |
| :--- | :--- |
| **Tempo Totale di Esecuzione** | Il tempo complessivo impiegato dall'avvio alla chiusura. |
| **File Tradotti** | Il numero totale di file processati con successo. |
| **Frasi/Entry Tradotte** | Il conteggio totale di singole stringhe passate all'API o recuperate dalla cache. |
| **Cache Hit Totali** | Numero di traduzioni trovate nella cache, che hanno evitato una chiamata API. |
| **API Call Totali** | Numero complessivo di richieste effettive inviate a Gemini. |
| **Dettaglio Utilizzo API Key** | Una tabella che mostra quante chiamate sono state eseguite da ciascuna API key. |

-----

## ❗ Note Importanti

  - **Quota API**: Usa `--rpm` per evitare di superare i limiti di richieste di Gemini.
  - **Contesto Completo (`--full-context-sample`)**: Utilizzare questa opzione su file molto grandi può superare il limite massimo di token del prompt, causando errori API (Generalmente 32K token per Gemini).
  - **Errori API Persistenti**: Se tutte le chiavi API disponibili falliscono, lo script entrerà in una routine di attesa prolungata. È possibile interromperlo con `CTRL + C`.
