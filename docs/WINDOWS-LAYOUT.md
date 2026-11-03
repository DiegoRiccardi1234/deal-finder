# Pacchetto Windows: programma e dati separati

Il pacchetto organizzato mantiene nella root soltanto il lanciatore principale
e `LEGGIMI.txt`, insieme alle cartelle di supporto:

```text
DealFinder/
  DealFinder.exe
  LEGGIMI.txt
  app/
    .dealfinder-layout.json
    DealFinder.exe
    Aggiorna.exe
    DealFinderMCP.exe
    _internal/
    vendor/                 # se sono presenti componenti vendor
  state/
```

La release pubblica contiene due asset costruiti dallo stesso runtime:

| Asset | Destinazione | Dati utente |
| --- | --- | --- |
| `DealFinder-organized-windows.zip` | Nuove installazioni e aggiornamenti del formato organizzato | `state` |
| `DealFinder-windows.zip` | Installazioni storiche, compreso l'updater 4.2.1 | `data` |

Il secondo conserva il nome storico e il layout flat con `Aggiorna.exe` e
`_internal` nella root. Per nuove installazioni il pacchetto consigliato è
quello organizzato.

Aprire **`DealFinder.exe` nella root**. Il piccolo lanciatore senza console
avvia il runtime in `app`, mantenendo l'icona nell'area di notifica. Gli
argomenti `--no-browser` e `--port` vengono inoltrati, anche se il percorso
contiene spazi o caratteri non ASCII. Spostare l'intera cartella, conservando
programma e dati insieme; non spostare i singoli eseguibili interni.
La porta scelta viene conservata nel processo per il riavvio dell'updater
tramite `DEAL_FINDER_PORT`; un nuovo `--port` esplicito ha precedenza.

La prima apertura può creare i dati locali sotto `state`: database, preferiti,
storico, connessioni, chiavi, cache e log. Il percorso è direttamente
`state\deal_finder.db`, senza un ulteriore livello `state\data`. Il pacchetto
pubblico contiene `state` vuota. I file statici del programma, compreso il seed
della knowledge base e gli asset Vinted, restano sotto `app\_internal`.

I CLI Codex e Claude non sono inclusi nel pacchetto: le relative connessioni
usano i runtime installati e autenticati sul computer. Claude è facoltativo.
La mancata disponibilità di un runtime non autorizza un passaggio a un'altra
connessione o a un'API.

## Marker e installazioni precedenti

Il marker `app\.dealfinder-layout.json` identifica il prodotto `dealfinder`
e la versione del layout `1`. Non coincide con la versione dell'applicazione.
Un marker corrotto, di un altro prodotto o di una versione non riconosciuta
blocca l'avvio prima di creare un database su un percorso alternativo. Non
cancellarlo per tentare di risolvere il problema: ripristinare il marker corretto
o l'installazione dal backup.

Un checkout sorgente continua a usare `data`. Anche un'installazione storica
senza marker continua a usare `data` e il runtime nella root. La semplice
esistenza di una cartella chiamata `app` non trasforma un checkout in un bundle.

**L'updater storico non può installare direttamente questo nuovo ZIP.** Il suo
validatore richiede `Aggiorna.exe` e `_internal` nella root e rifiuta il nuovo
formato prima di chiudere l'app. La release mantiene quindi anche
`DealFinder-windows.zip` nel formato flat: l'updater 4.2.1 può installarlo senza
cambiare il percorso dei dati. Per passare fisicamente dal formato storico a
quello organizzato resta necessaria la migrazione locale verificata.

Il nuovo updater seleziona e verifica l'asset in base al layout installato:
legacy verso legacy, organizzato verso organizzato. Un asset mancante o di
formato sbagliato interrompe l'operazione prima di chiudere l'app, senza
ripiegare sull'altro formato. La primitiva di sincronizzazione supporta anche
un archivio organizzato sopra una destinazione storica, mantenendola storica:
questo non è una migrazione fisica né il percorso dell'updater pubblico.
Un runtime storico privo del supporto a
`state` viene invece rifiutato su una destinazione organizzata, prima di
scrivere. Copiarlo sotto `app` gli farebbe aprire un nuovo database vuoto e non
costituisce un downgrade supportato.

## Migrazione locale reversibile

Lo script di manutenzione richiede Python e il checkout sorgente; non è
un'operazione da eseguire facendo doppio click sugli eseguibili interni.
Prima provarlo su una **copia** dell'installazione. Chiudere l'app e il suo
updater: lo script controlla i processi della destinazione e non li termina.

Esempio PowerShell dalla root del checkout, con percorsi da adattare
all'installazione scelta e a una cartella di backup privata esterna:

```powershell
$taskCandidate = (Resolve-Path '.\dist\DealFinder').Path
$taskTarget = 'D:\Applicazioni\DealFinder'
$taskBackupRoot = 'D:\Backup privati\DealFinder'

.\.venv\Scripts\python.exe scripts\migrate_installation.py `
  --candidate $taskCandidate `
  --target $taskTarget `
  --backup-root $taskBackupRoot
```

Il candidato deve essere un pacchetto organizzato completo con `state` vuota.
Le tre cartelle devono essere separate, senza contenimento reciproco.
Link simbolici e junction vengono rifiutati. Collisioni tra `data` e `state`,
marker non validi o lock di aggiornamento richiedono prima la risoluzione del
problema; lo script non fonde dati alla cieca.

Prima delle sostituzioni lo script prepara e verifica una copia integrale
dell'installazione, con manifest degli hash SHA-256. Salva inoltre uno snapshot
SQLite consistente, includendo gli eventuali dati nel WAL. Per questa lettura
usa una copia privata, evitando di creare sidecar nel database originale.

Lo staging riceve i contenuti del vecchio `data` direttamente in `state`;
conserva le configurazioni private e la vendor esistente, senza sovrascriverla
con quella del candidato. Eventuali file personali estranei nella vecchia root
restano disponibili sotto `state\legacy-root`. Il vecchio programma completo
rimane nel backup. Un fallimento durante lo scambio o la finalizzazione tenta
di ripristinare l'installazione originale; conservare sempre i percorsi di
backup e recupero indicati dall'errore.

Il risultato JSON contiene il percorso `backup`. Per ripristinare esattamente
il layout e i dati precedenti, a programma chiuso:

```powershell
$taskBackup = 'D:\Backup privati\DealFinder\<cartella-indicata-nel-risultato>'
.\.venv\Scripts\python.exe scripts\migrate_installation.py `
  --target $taskTarget --restore $taskBackup
```

Il ripristino verifica gli hash e conserva separatamente anche l'installazione
che sta sostituendo, inclusi i dati creati dopo la migrazione. Non unisce
automaticamente questi dati con lo snapshot precedente. Non avviare programmi
direttamente dalle cartelle di backup.

## Costruzione e verifica del pacchetto

Chiudere gli eseguibili provenienti dalla cartella di output prima del build.
Usare un output separato per le prove:

```powershell
.\.venv\Scripts\python.exe scripts\build_exe.py --output-dir 'D:\Build locali\DealFinder'
Get-FileHash -LiteralPath 'D:\Build locali\DealFinder\dist\DealFinder-organized-windows.zip' -Algorithm SHA256
Get-FileHash -LiteralPath 'D:\Build locali\DealFinder\dist\DealFinder-windows.zip' -Algorithm SHA256
```

Il costruttore richiede il compilatore C# di .NET Framework su Windows; il
lanciatore prodotto non richiede Python installato. Il build verifica file
obbligatori, struttura e CRC dello ZIP. La verifica finale deve controllare
anche root esatta, `state` vuota, asset Vinted, trasporto MCP e assenza di dati
personali, credenziali, note interne e cartelle di sviluppo. Associare ogni
prova al digest del pacchetto effettivamente provato.

Pubblicare entrambi gli archivi insieme. Le prove di compatibilità includono
il vero updater 4.2.1 su una copia con dati sintetici e l'aggiornamento di
un'installazione organizzata con stato preservato.

Un health HTTP positivo prova l'avvio del server, non la ricerca, la UI o
l'integrazione AI completa. Questi flussi richiedono prove separate su una
copia isolata. Il pacchetto locale e la sua verifica non costituiscono una
pubblicazione: tag, push e release sono operazioni distinte.
