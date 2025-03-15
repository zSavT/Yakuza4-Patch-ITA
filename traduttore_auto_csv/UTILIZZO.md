# Spiegazione Script
Lo script permette di tradurre in automatico tramite chiamata api verso Gemini AI (Richiede api) i file csv presenti nella cartella "_input_". Output dell'operazione è salvato nella cartella "_tradotto_".

# Funzionamento script

Lo script utilizza le api di Gemini 2.0 per poter funzionare. Le API al momento sono utilizzabili gratuitamente (per ora). La chiave si può ottenere da [qui](https://aistudio.google.com/apikey).<br>
Bisogna inserire la chiave all'interno del file "_traduttore_auto_csv/api_key.txt_" oppure lanciando lo script python tramite il flag "_--api [CHIAVE_API]_".
Ovviamente bisogna sostituire "_CHIAVE API_" con la propria chiave.

```
python .\main.py --api [CHIAVE_API]
```

## Struttura file

I file csv del gioco hanno il seguente formato:

```
INTEGER INTEGER TEXT
```
Esempio
```
293	326	Answer me.
2058	2177	You seem real tense.\nSomething happen?
```

La codifica dei file csv è "__UTF-16__".

## Problema

I caratteri speciali (come _\n_) alcune volte non vengono inseriti erroneamente.
