# Spiegazione Script
Lo script permette di tradurre in automatico tramite la libreria _deep_translator_ i file csv presenti nella cartella "_input_". Output dell'operazione è salvato nella cartella "_tradotto_".

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

## Problema non gestito

- [x]  I caratteri speciali (come _\n_) non vengono riportati nel file tradotto.

### Esempio caricato

L'esempio caricato proviene dal file presente in "_Yakuza 4\data\hact_" scompattando il file "_subtitle.par_".
