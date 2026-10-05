# HMC8012 Measurement Layer

Strumento a riga di comando per il multimetro digitale Rohde & Schwarz HMC8012, pensato per essere lanciato da un programma host (una macro VBA). A ogni chiamata esegue un solo comando, scrive l'esito in `result.txt` nella cartella dell'eseguibile e termina. Oltre alle letture singole, la cattura (`--time` o `--auto`) registra la corrente assorbita dal dispositivo e restituisce la corrente media del movimento compreso tra i picchi del deltastep, escludendo i picchi, il riposo e le letture a cavallo dell'inizio e della fine del movimento. Con `--auto` la cattura si ferma da sola quando il dispositivo si è fermato. Su richiesta mostra anche il grafico della cattura, in tempo reale in una piccola finestra oppure salvato in un file HTML.

English version: [README.md](README.md).

## Avvio rapido

| Comando | Cosa fa |
|-|-|
| `hmc.exe 192.168.0.2 dci` | Una lettura di corrente DC |
| `hmc.exe 192.168.0.2 dci --delay 1.5` | Aspetta 1.5 s, poi una lettura di corrente DC |
| `hmc.exe 192.168.0.2 range dci 2` | Imposta la corrente DC con fondo scala 2 A, che resta attivo finché non lo si cambia |
| `hmc.exe 192.168.0.2 dci --auto` | Cattura che si ferma da sola 3 s dopo l'arresto del motore (al massimo 30 s): avviala, poi muovi il motore |
| `hmc.exe 192.168.0.2 dci --time 10` | Cattura di 10 s esatti |
| `hmc.exe 192.168.0.2 dci --auto --rate MED` | Come sopra, con 10 conversioni al secondo, per movimenti sotto il secondo |
| `hmc.exe 192.168.0.2 dci --auto --live` | Cattura con il grafico in tempo reale in una piccola finestra |
| `hmc.exe --version` | Versione di questo eseguibile |

L'esito è in `result.txt`, nella cartella di `hmc.exe`: il valore in ampere, `OK` oppure `ERR`.

### Sviluppo

Python 3.11 o successivo:

```bash
python -m venv venv
source venv/bin/activate
pip install -r requirements.txt
python -m pytest -q
```

Su Windows l'ambiente si attiva con `venv\Scripts\activate`.

## Comandi

`<indirizzo>` è un indirizzo IP (`192.168.0.2`, LAN, porta 5025) o una porta COM (`COM3`, porta COM virtuale USB, solo Windows). `hmc.exe` e `python measure.py` accettano gli stessi argomenti.

| Comando | Cosa fa | `result.txt` |
|-|-|-|
| `<indirizzo> <funzione> [--delay S]` | Una lettura con le impostazioni correnti, dopo un'attesa opzionale di S secondi | valore o `ERR` |
| `<indirizzo> <funzione> --time S [opzioni]` | Cattura di S secondi; restituisce il valore medio del movimento. Vedi [Cattura](#cattura) | valore o `ERR` |
| `<indirizzo> <funzione> --auto [opzioni]` | Cattura che si ferma da sola quando il motore torna a riposo (al massimo 30 s). Vedi [Cattura](#cattura) | valore o `ERR` |
| `<indirizzo> range <funzione> <valore>` | Seleziona la funzione e il fondo scala; restano fino al prossimo `range` o `reset` | `OK` o `ERR` |
| `<indirizzo> adc` | Legge l'ADC rate della funzione attiva | `SLOW`, `MED`, `FAST` o `ERR` |
| `<indirizzo> adc <SLOW\|MED\|FAST>` | Imposta l'ADC rate della funzione attiva (l'ultima selezionata) | `OK` o `ERR` |
| `<indirizzo> reset` | Ripristina le impostazioni di fabbrica (ADC rate SLOW, autorange attivo) | `OK` o `ERR` |
| `--version` | Stampa la versione sulla console | invariato |

### Cattura

- La cattura funziona con `dci`, `dcv`, `aci` e `acv`; l'analisi è pensata per la corrente DC assorbita dal dispositivo (`dci`), vedi [Come si calcola il valore della cattura](#come-si-calcola-il-valore-della-cattura).
- Con `--auto` la cattura si ferma 3 s dopo che il valore è tornato a riposo, purché prima il dispositivo sia stato in funzione per almeno 0.5 s. Il riposo è la lettura più bassa vista fino a quel momento, quindi la cattura può partire anche con il dispositivo già in movimento. Le pause più brevi di 3 s non la fermano. Se il dispositivo non si ferma mai, la cattura termina a 30 s.
- `--timeout S` imposta la scadenza della cattura; di default vale la durata della cattura più 10 s (40 s con `--auto`).
- La cattura legge con ADC rate SLOW, oppure con quello indicato da `--rate SLOW|MED|FAST`. Se lo strumento è a un altro rate, lo cambia e alla fine ripristina quello precedente, anche se la cattura fallisce, così le misure successive mantengono le loro impostazioni. MED (10 conversioni al secondo) serve per i movimenti che durano poche conversioni SLOW.
- Impostare prima il fondo scala della funzione con `range`: la cattura rifiuta l'autorange.

Opzioni di diagnostica della cattura, disattivate di default, combinabili tra loro e in qualsiasi ordine:

| Opzione | Cosa aggiunge |
|-|-|
| `--live` | Mostra in tempo reale il grafico delle letture durante la cattura, in una piccola finestra (circa 1000x640). Alla fine evidenzia il movimento e le conversioni usate per la media, e mostra il valore o l'errore. La finestra resta aperta anche dopo la chiusura di `hmc.exe`, finché non la chiudi tu. |
| `--save-plot` | Salva lo stesso grafico in `capture_plot_<data UTC>.html` nella cartella dell'eseguibile: un file unico che si apre con un doppio clic nel browser, anche senza rete. Per ingrandire, trascina sul grafico. |
| `--save-samples` | Salva le letture grezze in `capture_samples_<data UTC>.csv` nella cartella dell'eseguibile. |

- Nessuna opzione cambia `result.txt`. Un file che non si riesce a scrivere o una finestra che non si apre vengono segnalati solo sulla console.
- Con `--live`, dopo aver scritto `result.txt`, `hmc.exe` aspetta al massimo 3 s perché la finestra riceva l'esito.
- La finestra live è una finestra nativa (pywebview sul runtime WebView2, presente in Windows 11 e nei Windows 10 aggiornati) gestita da un secondo processo `hmc.exe`. Compare appena quel processo si è avviato e mostra subito tutte le letture fatte fino a quel momento. Senza WebView2 la pagina si apre in una scheda del browser.
- La pagina è accessibile solo da `127.0.0.1`, mai dalla rete.

### Funzioni e fondi scala

| Nome | Misura | Comando SCPI | Valori di fondo scala (unità SI) |
|-|-|-|-|
| `dcv` | Tensione DC | `CONF:VOLT:DC` | 0.4, 4, 40, 400, 1000 V |
| `acv` | Tensione AC | `CONF:VOLT:AC` | 0.4, 4, 40, 400, 750 V |
| `dci` | Corrente DC | `CONF:CURR:DC` | 0.02, 0.2, 2, 10 A |
| `aci` | Corrente AC | `CONF:CURR:AC` | 0.02, 0.2, 2, 10 A |
| `res` | Resistenza a 2 fili | `CONF:RES` | 400, 4e3, 40e3, 400e3, 4e6, 40e6, 2.5e8 Ohm |
| `fres` | Resistenza a 4 fili | `CONF:FRES` | 400, 4e3, 40e3, 400e3, 4e6 Ohm |
| `cap` | Capacità | `CONF:CAP` | 5e-9, 50e-9, 500e-9, 5e-6, 50e-6, 500e-6 F |
| `temp` | Temperatura (PT100) | `CONF:TEMP` | nessuno |
| `freq` | Frequenza | `CONF:FREQ` | nessuno |
| `cont` | Continuità | `CONF:CONT` | nessuno |
| `diod` | Test diodo | `CONF:DIOD` | nessuno |

`range <funzione> AUTO` attiva l'autorange.

## Contratto con il programma host

**File.** `result.txt` viene scritto nella cartella di `hmc.exe`, che quindi deve avere i permessi di scrittura. Il collegamento LAN non richiede altro; quello COM (USB) richiede il driver VCP dell'HMC8012.

**`result.txt`:**

- Viene cancellato all'avvio di ogni comando (tranne `--version`) e scritto in un colpo solo alla fine (file temporaneo poi rinominato): mentre un comando è in corso il file non esiste, e se il file esiste è completo.
- La riga 1 è il valore (punto decimale), `OK`, l'ADC rate oppure `ERR`.
- Dopo `ERR`, la riga 2 è `[APP] <comando> failed (<livello>).` e la riga 3 è `[EXC] <tipo>: <messaggio>`.
- L'exit code è 0 in caso di successo e 1 in caso di errore.

| Livello | Significato |
|-|-|
| `VISA/network` | Strumento non raggiunto: errore di connessione o trasporto |
| `instrument SCPI` / `instrument` | Strumento raggiunto, ma ha segnalato un errore, un overflow, oppure la cattura si è fermata per letture fallite |
| `instrument config` | Cattura: funzione sbagliata o autorange ancora attivo |
| `insufficient samples` | Cattura: troppo poche letture valide |
| `analysis` | Cattura: letture registrate, ma nessun valore affidabile (vedi la tabella degli errori sotto) |
| `input sanitization` | Argomento non valido |
| `unexpected` | Qualsiasi altro errore; i dettagli sono in `[EXC]` |

**Tempi.** A ogni comando si aggiunge prima il tempo di avvio di `hmc.exe`: l'eseguibile è un file unico che all'avvio si scompatta, e ci mette di solito qualche secondo (da misurare sul PC del laboratorio). Una cattura dura poi i secondi indicati con `--time` (con `--auto`, fino a 3 s dopo l'arresto del motore), più l'analisi, che richiede molto meno di 0.1 s.

Per una lettura singola, aspettare che il processo termini e poi leggere il file:

```vba
Dim sh As Object, rc As Long
Set sh = CreateObject("WScript.Shell")
rc = sh.Run("""C:\hmc\hmc.exe"" 192.168.0.2 dci", 0, True)
```

Con `True`, `Run` aspetta che `hmc.exe` termini; a quel punto la riga 1 di `C:\hmc\result.txt` contiene il valore oppure `ERR`.

In una cattura il motore deve muoversi mentre `hmc.exe` è in esecuzione, quindi lo si lancia senza aspettarne la fine e si attende invece la comparsa di `result.txt`:

```vba
Const RESULT_FILE As String = "C:\hmc\result.txt"
Dim sh As Object, deadline As Date
If Dir(RESULT_FILE) <> "" Then Kill RESULT_FILE
Set sh = CreateObject("WScript.Shell")
sh.Run """C:\hmc\hmc.exe"" 192.168.0.2 dci --auto", 0, False
' Qui si avvia il motore.
deadline = Now + TimeSerial(0, 0, 45)
Do While Dir(RESULT_FILE) = "" And Now < deadline
    DoEvents
Loop
```

- Cancellare prima il vecchio `result.txt`. Lo cancella anche `hmc.exe`, ma solo una volta avviato: fino a quel momento il file conterrebbe ancora il valore del comando precedente.
- Con `False`, `Run` ritorna subito, così il motore può partire mentre la cattura è in corso.
- La scadenza di 45 s copre la cattura più lunga (30 s), l'avvio e un margine; con `dci --time 10` bastano 30 s. Se dopo la scadenza il file non c'è, la cattura non è terminata.
- `result.txt` viene scritto in un solo passaggio, quindi quando esiste è completo: la riga 1 è il valore oppure `ERR`.
- Leggere i numeri con `Val()`, che usa sempre il punto decimale. `CDbl` segue le impostazioni di Windows e in italiano si aspetta la virgola.

**Una cattura per ogni movimento.** La cattura può partire durante i picchi del deltastep, ma deve finire con il dispositivo a riposo: con `--time` il dispositivo deve fermarsi prima della fine della cattura; con `--auto` è la cattura ad aspettarlo.

## Come si calcola il valore della cattura

`result.txt` contiene la **corrente media del movimento**: il livello che il dispositivo assorbe mentre si muove, tra due gruppi di picchi del deltastep, in una cattura che segue la sequenza (riposo), picchi del deltastep, movimento, picchi del deltastep, riposo. Il codice è in `analyzer.py` (`analyze_waveform`):

1. **Validazione.** I timestamp devono essere crescenti. Le letture NaN/inf e i valori di overflow (+/-9.9E37) sono considerati non validi; se superano il 20% delle letture, la cattura viene scartata.
2. **Conversioni.** L'HMC8012 risponde a `READ?` con l'ultima conversione, quindi più letture uguali consecutive sono una sola conversione (circa 5 al secondo in SLOW).
3. **Riposo.** La cattura deve finire con almeno 0.25 s di corrente stabile, al livello più basso della cattura.
4. **Picchi.** Le conversioni sopra la metà tra il riposo e la conversione più alta sono picchi del deltastep; le letture non valide contano come picchi.
5. **Movimento.** I tratti di conversioni consecutive che superano il riposo di più di due tolleranze e restano sotto i picchi. A ciascun estremo di un tratto, le conversioni che non corrispondono al suo livello (entro la tolleranza, max(2 mA, 2%)) sono a cavallo dell'inizio o della fine del movimento e vengono escluse. Quello che resta deve durare almeno 0.3 s, cioè più di una conversione SLOW, e un solo tratto può soddisfare questa condizione.
6. **Risultato.** La media delle conversioni del movimento, se due errori standard stanno entro la tolleranza.

**Un errore, mai un numero sbagliato.** Se non c'è un valore affidabile, in `result.txt` viene scritto `ERR`:

| Errore | Significato | Cosa cambiare |
|-|-|-|
| `InvalidCaptureError` | Dati malformati, troppe letture non valide, oppure la cattura non finisce con un riposo stabile al suo livello più basso | Lasciare andare la cattura finché il dispositivo è a riposo (`--auto` lo fa da sé); alzare il fondo scala se i picchi vanno in overflow |
| `SignalNotSettledError` | Nessun movimento di almeno 0.3 s tra il riposo e i picchi | Per un movimento di sole 2-3 conversioni SLOW usare `--rate MED` |
| `AmbiguousRunError` | Più di un movimento nella cattura | Un movimento per cattura |
| `ImpreciseValueError` | Movimento trovato, ma le sue conversioni sono troppo disperse per la media (poche conversioni, estremi mescolati ai picchi) | `--rate MED` per i movimenti brevi |

**Limiti noti.**

- Un carico periodico con periodo sottomultiplo dei 200 ms di conversione in SLOW può dare aliasing se l'apertura dell'ADC è più corta del periodo di conversione (non indicato nel manuale). Il ripple veloce (passi dello stepper, PWM del driver) si media dentro ogni conversione.
- In SLOW un movimento di 0.6 s ha 3 conversioni, e quelle agli estremi si mescolano ai picchi: delle quattro catture di laboratorio del motore 2, una dà un valore e tre danno `ERR`. `--rate MED` raddoppia le conversioni.
- I picchi del deltastep devono essere nella cattura, perché fissano il limite superiore del movimento. Senza picchi il movimento stesso viene preso per un picco e la cattura dà `SignalNotSettledError`.
- Un movimento che supera il riposo di meno di due tolleranze (circa 7 mA a 167 mA) non si distingue dal riposo.
- Una pausa dentro il movimento (la corrente torna a riposo) lo divide in due: se entrambe le parti durano almeno 0.3 s, la cattura dà `AmbiguousRunError`. Con `--auto`, una pausa di 3 s o più chiude anche la cattura.
- Le catture di `dcv`, `aci` e `acv` usano la stessa analisi della corrente DC: funzionano solo se il valore sale mentre il motore gira, la tolleranza minima resta 0.002 nell'unità della funzione e i messaggi di errore parlano di corrente. Le risposte `FUNC?` delle funzioni AC (`CURR:AC`, `VOLT:AC`) non sono verificate sullo strumento; se lo strumento risponde in un altro modo, la cattura si ferma con `instrument config`.

## Guida per sviluppatori

### Architettura

```text
measure.py              CLI, result.txt
 |-- hmc8012.py         driver SCPI
 |-- capture.py         ciclo di lettura, tramite hmc8012.py
 |-- stop_detector.py   stop automatico (--auto)
 |-- analyzer.py        media del movimento
 |-- capture_plot.py    pagina del grafico (uPlot)
 |-- live_plot.py       server della pagina live, usa capture_plot.py
 `-- live_window.py     processo della finestra nativa

tests/                  catture di laboratorio (tests/data/lab) e simulatore
                        (simulation.py, scenarios.py) per verificare analyzer.py
```

Cosa fa una cattura (`--time` o `--auto`):

1. `measure.py` apre lo strumento (`hmc8012.py`), seleziona la funzione e imposta l'ADC rate della cattura se serve.
2. `capture.py` interroga `READ?` fino alla fine di `--time` oppure, con `--auto`, finché `stop_detector.py` non vede il dispositivo di nuovo a riposo. Le letture fallite restano NaN; cinque di fila fermano la cattura.
3. Il rate precedente viene ripristinato.
4. `analyzer.py` calcola il valore e `measure.py` scrive `result.txt`.

Con `--live` ogni lettura va anche a `live_plot.py`, che la invia alla pagina come server-sent event; la pagina si aggiorna alla frequenza di refresh dello schermo. Alla fine l'esito (valore o errore) viene inviato alla pagina live e scritto nei file richiesti.

| Modulo | Responsabilità | Punti di ingresso pubblici |
|-|-|-|
| `measure.py` | Dispatch CLI, `result.txt`, livelli di errore | `main`, `cmd_*`, `write_result`, `clear_result` |
| `hmc8012.py` | SCPI via PyVISA (socket LAN o COM); ogni setter controlla `SYST:ERR?` | `HMC8012`, `ScpiError`, `RangeOverflowError` |
| `capture.py` | Ciclo di lettura temporizzato, conteggio delle letture fallite | `ContinuousCapture`, `CaptureResult` |
| `stop_detector.py` | Stop automatico: chiude una cattura `--auto` quando il motore ha girato ed è tornato a riposo | `StopDetector` |
| `analyzer.py` | Riposo, picchi, movimento, precisione; nel dubbio solleva un errore invece di restituire un valore | `analyze_waveform`, `AnalysisConfig`, `AnalysisResult`, classi di errore |
| `capture_plot.py` | Pagina del grafico di una cattura (salvata o live), da `plot_assets/` | `render_capture_plot`, `write_capture_plot`, `render_live_page` |
| `live_plot.py` | Serve la pagina live su `127.0.0.1` e invia le letture | `LivePlot` |
| `live_window.py` | Finestra live nativa in un processo figlio (`hmc.exe --live-window <url>`, interno) | `open_live_window`, `run_live_window` |
| `simulation.py` | Simulatore: corrente reale del dispositivo e modello di campionamento dell'HMC8012 | `Phase`, `InstrumentModel`, `simulate_capture` |
| `scenarios.py` | Simulatore: scenari del dispositivo ricavati dalle catture di laboratorio | `SCENARIOS`, `ScenarioParams` |
| `version.py` | Unico punto in cui è definita la versione | `__version__` |
| `plot_assets/` | Template della pagina e uPlot 1.6.32 (MIT), inclusi in `hmc.exe` | |

Le docstring di ogni modulo sono il riferimento per argomenti, valori restituiti ed errori sollevati.

### Dove modificare

| Per cambiare | Modificare |
|-|-|
| Tolleranza dell'analisi | valori di default di `AnalysisConfig` in `analyzer.py` |
| Riconoscimento del movimento (soglia dei picchi, movimento minimo, riposo finale) | `PEAK_THRESHOLD_FRACTION`, `MIN_MOVEMENT_S`, `MIN_IDLE_S` in `analyzer.py` |
| ADC rate di default delle catture | `CAPTURE_ADC_RATE` in `measure.py` |
| Funzioni ammesse nella cattura | `CAPTURE_FUNCTION_REPLIES` in `capture.py` |
| Letture fallite che fermano una cattura | `DEFAULT_MAX_CONSECUTIVE_FAILURES` in `capture.py` |
| Tempo a riposo che chiude una cattura `--auto` | `STOP_HOLD_S` in `stop_detector.py` |
| Durata massima di una cattura `--auto` | `AUTO_STOP_MAX_DURATION_S` in `measure.py` |
| Aspetto del grafico (colori, etichette, disposizione) | `plot_assets/capture_plot.html` |
| Dimensione e titolo della finestra live | `WINDOW_SIZE`, `WINDOW_TITLE` in `live_window.py` |
| Un nuovo comportamento simulato per i test | un builder e una voce in `SCENARIOS` (`scenarios.py`) |
| Un nuovo comando | una funzione `cmd_*` e un ramo in `main()` (`measure.py`) |
| La versione | `version.py` (vedi Rilasci) |

### Test

`python -m pytest -q` esegue tutti i test, compresi quelli che attendono il timeout di connessione su un indirizzo irraggiungibile.

- L'analyzer è verificato sulle catture di laboratorio (`tests/data/lab`: il dispositivo vero in SLOW), sulla simulazione fisica (`simulation.py`, `scenarios.py`: la stessa sequenza a ogni ADC rate) e su casi limite costruiti a mano. La regola verificata dai test: una cattura restituisce il valore giusto oppure un errore esplicito, mai un valore sbagliato.
- I test del grafico coprono il contenuto della pagina e l'invio live su una vera connessione locale.
- La finestra live si può verificare solo su un PC Windows: avviare una cattura con `--live` e guardarla.

### Scelte di progetto

- **Solo il movimento.** I picchi del deltastep sono un altro carico e il riposo non è il movimento: conta solo il livello che sta in mezzo. Le conversioni agli estremi del movimento sono escluse perché ciascuna fa la media tra il movimento e quello che c'era prima o dopo.
- **Un errore piuttosto che un numero sbagliato.** Ogni controllo (riposo alla fine, un solo movimento, almeno 0.3 s, precisione) scarta la cattura indicando il motivo, invece di restituire un valore potenzialmente sbagliato.
- **SLOW di default, senza effetti collaterali.** SLOW è l'unico rate con accuratezza specificata e media il ripple dello stepper dentro ogni conversione; `--rate MED` rinuncia a questo in cambio del doppio delle conversioni sui movimenti brevi. La cattura ripristina il rate precedente, così le letture singole successive mantengono le loro impostazioni.
- **Una conversione, un valore.** L'HMC8012 risponde a `READ?` con l'ultima conversione, quindi se lo si interroga più velocemente dell'ADC i valori si ripetono; l'analisi li unisce, così il risultato non dipende dalla frequenza di interrogazione.
- **Le letture fallite restano nei dati come NaN e contano come picchi.** Una lettura mancante accanto al movimento non viene mai presa per una sua parte.
- **La diagnostica non cambia mai l'esito.** Grafico, finestra live e file delle letture ricevono letture ed esito anche quando la cattura fallisce, ma `result.txt` non li aspetta e non dipende da loro.

### Note sullo strumento

Dai manuali utente e SCPI dell'HMC8012:

- La corrente DC dà 5 / 10 / 200 letture al secondo in SLOW / MED / FAST, con 5¾ / 4¾ / 4¾ cifre.
- L'accuratezza è specificata solo in SLOW, e `*RST` imposta SLOW.
- `ADCRate` "selects the ADC rate for the activated measurement function".

Non indicati nei manuali, da verificare sullo strumento:

- se `CONF:CURR:DC` senza fondo scala riattiva l'autorange (in quel caso la cattura si fermerebbe con `instrument config`);
- la durata dell'apertura dell'ADC.

## Rilasci e compilazione

La versione è definita solo in `version.py`: `hmc.exe --version` la stampa e la build la scrive nelle proprietà del file eseguibile. Ogni modifica rilasciata la incrementa (versionamento semantico: major quando cambia il contratto con l'host, minor per nuovi comandi, patch per le correzioni).

`hmc.exe` si compila su Windows con Python 3.12 (il MinGW-w64 incluso in Nuitka non supporta la 3.13+):

```bat
pip install -r requirements.txt nuitka
python -m pytest -q
python -m nuitka --onefile --assume-yes-for-downloads --output-filename=hmc.exe ^
  --include-package=pyvisa --include-package=pyvisa_py --include-package=serial ^
  --include-data-dir=plot_assets=plot_assets --enable-plugin=pywebview ^
  --nofollow-import-to=pyvisa.testsuite --nofollow-import-to=pyvisa_py.testsuite ^
  --noinclude-pytest-mode=nofollow ^
  --product-name=hmc8012-measure --file-description="HMC8012 measurement CLI" ^
  --file-version=4.1.0 --product-version=4.1.0 ^
  measure.py
```

Usare in `--file-version` e `--product-version` la versione di `version.py`.

## Dipendenze

- Python 3.11 o successivo (3.12 per compilare l'eseguibile)
- `pyvisa`, `pyvisa-py`: comunicazione con lo strumento senza NI-VISA
- `pyserial`: connessioni via porta COM
- `numpy`: analisi delle catture
- `pytest`: test
- `pywebview`: la finestra live (su Windows tramite `pythonnet` e il runtime WebView2)
- uPlot 1.6.32 (MIT): incluso in `plot_assets/`, niente da installare

```bash
pip install -r requirements.txt
```
