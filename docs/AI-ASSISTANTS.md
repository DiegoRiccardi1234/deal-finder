# AI e assistenti locali

Deal Finder mantiene le API esistenti e può usare il client ufficiale Codex
installato sul PC. Il runtime gira localmente, ma le risposte usano i servizi
e la quota dell'account: non è un modello offline.

## Avvio e connessioni

Apri **AI e assistenti** nella barra laterale. **Usa Codex per tutte le attività**
seleziona l'account per dialogo/piano, analisi e consiglio. Installa e autentica
il client ufficiale separatamente. Non vengono copiati token o gestite finestre
di chat. Una diagnostica non genera inferenze; **Prova sintetica** invia una
richiesta e consuma quota. La quota sconosciuta non significa zero.

Le chiavi API precedenti restano conservate. Ogni attività può avere una
connessione e fallback espliciti nell'ordine salvato. Un errore Codex non
abilita una API pagata. Claude Code è facoltativo, disabilitato inizialmente e
richiede un piano compatibile attuale; un vecchio login non lo dimostra.

Gli endpoint OpenAI-compatible hanno nome, URL, modello e segreto separati.
HTTP è ammesso solo su loopback, HTTPS per gli endpoint remoti. Una chiave
omessa durante modifica viene conservata; la rimozione è esplicita.
Il file delle connessioni corrotto blocca il routing anziché ripiegare su API.

## Una richiesta, più prodotti

Scegli **Assistente autonomo** e descrivi fino a dieci prodotti. Se mancano
budget, l'app pone una domanda aggregata: sono valide anche le risposte
“non so” e “senza tetto”. Minimi e massimi non vengono stimati per categoria.
I budget devono essere associati ai prodotti: gli importi ambigui vengono
lasciati non definiti anziché scambiati fra ricerche.

Un coordinatore valida il piano, raccoglie i prodotti in sequenza sfruttando il
parallelismo degli scraper, analizza fino a cinquanta offerte bilanciate e
produce il consiglio finale. Tutte le offerte raccolte restano consultabili;
il campione analizzato non certifica il miglior prezzo dell'intero mercato.
Non ci sono monitoraggi periodici, acquisti o notifiche esterne automatiche.

I risultati sono progressivi e persistenti; **Interrompi** conserva quelli
raccolti. Le richieste HTTP già avviate terminano ai timeout propri. Un riavvio
marca il lavoro interrotto senza ripetere ricerche o inferenze. Lo storico
permette di riaprire la ricerca. Il timeout nativo include coda ed esecuzione;
un limite suggerito di token non equivale a un tetto hard del protocollo CLI.

Una query di famiglia come “iPhone 15” include le sue varianti. “Solo base” o
un modello preciso restringono il piano. Generazioni, suffissi e capacità non
sono intercambiabili; una capacità mancante non dimostra una differenza.
Prezzi e caratteristiche assenti dai dati restano sconosciuti.

## MCP: l'assistente usa Deal Finder

MCP è la direzione opposta al runtime: Codex, Hermes o un altro client usa
gli strumenti dell'app già aperta. Il server condivide il proprietario dei dati
con la UI; l'adapter STDIO comunica via HTTP autenticato senza aprire SQLite.
Il servizio locale è attivato soltanto da **Genera configurazione MCP** e
riprende agli avvii successivi. **Revoca collegamento MCP** invalida il token
e disattiva il servizio.

Le configurazioni generate includono Codex CLI/desktop/IDE, Hermes YAML,
Claude Desktop e una voce per plugin locale Cowork. Nessun file globale viene
modificato. Mantieni l'app aperta e usa esattamente comando, porta e percorso
token generati. Cowork non deve usare un connettore cloud verso localhost.
La configurazione contiene il percorso del token, mai il token stesso.

I tool espongono contesto, storico, ricerche singole/gruppi, avanzamento,
annullamento, offerte, ticket di analisi e preferiti reversibili. Le ricerche
MCP non invocano alcun modello. L'assistente analizza nella propria conversazione
e restituisce valutazioni complete con ID e ticket; il server valida lo
snapshot e registra una provenienza dichiarata, senza modificare fatti/prezzi.
I contributi esterni sono visibili nella ricerca. Nessun tool approva acquisti,
cambia credenziali o legge file arbitrari.

Hermes può essere un client MCP. La sua API è invece una connessione opzionale
a un agente con strumenti e upstream propri: non diventa gratuita perché gira
localmente. Abilitarla e assegnarla ai task è una scelta esplicita; Deal Finder
non installa né amministra Hermes e non richiede la sua API dai propri tool MCP.

## Stato privato e verifiche

Connessioni e segreti sono separati nei file `ai_connections.json` e
`ai_connection_secrets.json`; token e impostazioni MCP sono sotto `agent/`.
Tutto vive nella directory di stato risolta dall'installazione, mai nel bundle
pubblico. Consulta [il layout Windows](WINDOWS-LAYOUT.md) per backup e migrazione.

I test automatici isolano dati, client nativi simulati e cataloghi sintetici.
Una prova Codex riuscita dimostra soltanto l'accesso di quell'account in quel
momento. Il contratto MCP verificato non certifica client desktop non provati.

Riferimenti: [Codex App Server](https://learn.chatgpt.com/docs/app-server),
[MCP](https://modelcontextprotocol.io/specification/2025-11-25/basic/transports),
[Hermes MCP](https://github.com/NousResearch/hermes-agent/blob/main/website/docs/user-guide/features/mcp.md),
[Hermes API](https://github.com/NousResearch/hermes-agent/blob/main/website/docs/user-guide/features/api-server.md).
