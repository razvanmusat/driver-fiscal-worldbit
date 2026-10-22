# Driver fiscal Worldbit

Agent local (Python 3.10+, doar biblioteca standard) care primește de la o aplicație web un JSON universal de bon și îl
transmite casei de marcat prin driverul ales la instalare: **Fisco** sau **FisCool**. Browserul vorbește doar cu agentul,
pe `http://127.0.0.1:<port>`; nu există tunel SSH. Un agent per calculator, cu casa de marcat a acelui calculator.

POS bancar: **în așteptare** (interfața există, modulele se scriu când sosește protocolul băncii). Cu modelul `none`,
agentul nu apelează POS-ul.

## Pornire

```
python -m worldbit_fiscal init --driver fisco --port 4747 --origin https://aplicatia-ta.ro   # o singură dată (la instalare)
python -m worldbit_fiscal run
```

`init` cere parola de admin pentru `/setari` (sau o ia din variabila `WORLDBIT_ADMIN_PASSWORD`). Configurația și starea
stau în `%ProgramData%\WorldbitFiscalDriver` (Windows) sau `~/.worldbit-fiscal-driver`, suprascriibil cu `WORLDBIT_FISCAL_HOME`.
Driverul se schimbă doar rulând din nou `init`, nu din `/setari`.

Teste: `python -m unittest discover -s tests -t .`

## Ordinea într-un job

health casă de marcat → POS (doar dacă bonul are plată card și există model POS configurat) → bon fiscal.
Un singur job în zbor pe agent. O plată POS aprobată nu se mai cere a doua oară la reîncercare.

## API pentru aplicația web (CORS doar pentru originile din `allowed_origins`)

| Metodă | Cale | Rol |
|---|---|---|
| GET | `/health` | `{agent_id, driver, fiscal:{ready,detail}, pos:{model,configured}}` (bulina de status) |
| POST | `/print` | pornește un bon (202; același `external_id` + același conținut = 200, fără retipărire) |
| POST | `/reports` | `{external_id, type:"X"\|"Z"}` |
| GET | `/jobs/{external_id}` | starea jobului; pe un job `uncertain` declanșează lămurirea (la max. 3 secunde) |
| POST | `/jobs/{external_id}/retry` | reîncercare după `failed`; `{"confirm_not_printed": true}` dacă `error.retry_safe` e false |

### Cererea `POST /print`

```json
{
  "external_id": "dressbit-vanzare-123",
  "lines": [{"name": "Rochie roșie M", "unit_price": "100.00", "quantity": 1, "vat_percent": 21, "unit": "buc"}],
  "payments": [{"method": "cash", "amount": "60.00"}, {"method": "card", "amount": "40.00"}],
  "total": "100.00",
  "payment_key": "dbp-6f1c0b6e-0f0e-4c63-9a53-2a1d7f0e9c11",
  "buyer": {"cui": "RO123456"}
}
```

`payment_key` (opțional, 1-64 caractere din `[A-Za-z0-9._-]`) = identitatea PLĂȚII, stabilă între joburile aceluiași bon. Vezi „Protecția la bon dublu".

Sumele sunt zecimale (max. 2 zecimale, cantitatea max. 3), cu TVA inclus; `total` = suma liniilor = suma plăților
(altfel 400, înainte de orice contact cu aparatul). `buyer` e opțional și e implementat doar în FisCool.
TVA-ul se trimite ca procent; maparea pe indexul aparatului e în `/setari`.

### Răspunsul (`POST /print`, `GET /jobs/...`)

```json
{
  "ok": true, "external_id": "...", "kind": "receipt", "status": "printed", "attempt": 1, "driver": "fisco",
  "fiscal_job_id": "http_...",
  "result": {"slip_number": "6147", "z_report_number": "430", "receipt_in_z": "10", "device_serial": "DB41...",
             "report_number": null, "raw": {}},
  "error": null,
  "pos": null
}
```

`status`: `received` | `running` | `printed` | `failed` (dovadă că nu s-a tipărit) | `uncertain` (nu se știe; fără
retrimitere automată). `error = {code, message, retry_safe}`.

Fisco: `slip_number`=`SlipNumber`, `z_report_number`=`nZrep`, `receipt_in_z`=`nFNum`, `device_serial`=`DeviceSerial`,
`report_number`=`nRep`. FisCool: `slip_number`, `z_report_number`; restul vezi mai jos.

## Fără decizii pentru casier

Casierul apasă doar „Încasare". Ce rezolvă sistemul singur:

- **Eșecuri trecătoare documentate de drivere → reîncercare automată în agent** (implicit 3 × la 3 s, setări `auto_retry.max` / `auto_retry.delay_s` în `config.json`;
  `max: 0` o oprește). Fiecare încercare are cheie nouă la driver (`<id>-2`, `<id>-3`); jobul rămâne `running`, cu eroarea trecătoare și `auto_retry: {count, max}`
  vizibile ca informație. Sunt trecătoare:
  - Fisco: conexiune refuzată / Fisco nu răspunde, lista de joburi indisponibilă, motivul documentat „Printer status is not known yet". NU sunt trecătoare
    depășirea celor 24h fără raport Z și „Fiscal Driver processing is off" (cer o acțiune).
  - FisCool: 1004 (conexiune), 505 (casă ocupată), conexiune refuzată, dispozitiv în starea `Connecting`.
- **Stare incertă → verificare automată** (Fisco: reconciliere pe snapshot; FisCool: aceeași cheie de idempotență), fără nicio acțiune a casierului.
- **Fără conexiune la server → rezultatul rămâne în agent** și fereastra îl raportează singură când revine conexiunea.
- **Bon emis, vânzare neînregistrată → înregistrarea se reia singură** de câteva ori.
- **La final**: succes, sau un eșec definitiv cu mesajul driverului și un singur buton, „Reîncearcă plata" (încercare nouă, fără confirmări). Erorile care cer o
  acțiune (hârtie, raport Z, card refuzat) nu se reîncearcă automat: se afișează imediat, cu rezolvarea documentată.
- **Termen total: 30 de secunde** (`max_wait_s`; la rapoarte X/Z `max_wait_report_s`, 120 s). Un bon care nu e confirmat ca emis în acest timp nu mai e „în așteptare":
  agentul îl închide ca eșuat (`NOT_CONFIRMED`, „nu este considerat emis", retrimitere sigură), Dressbit își eliberează bonul, iar casierul reia plata normal.
  Dacă agentul dispare după ce a primit comanda, frontend-ul anulează singur plata la Dressbit după acest termen. Timeout-urile driverelor (Fisco 25 s, FisCool 25 s)
  sunt sub termen, ca agentul să apuce să lămurească jobul. **Înregistrarea tehnică rămâne în agent** (nu se șterge): `fiscal_job_id` al unui job abandonat
  trebuie să rămână atribuit lui, ca reconcilierea unui job următor să nu-l adopte din greșeală. O plată cu cardul nelămurită (POS) nu se abandonează.
  - **Risc rămas, inevitabil:** nici Fisco, nici FisCool nu au comandă de anulare. Dacă driverul emite un job acceptat DUPĂ termen, bonul iese, iar plata reluată
    de casier produce un al doilea. Termenul e configurabil; un bon cu multe linii poate dura mai mult de 30 s pe unele aparate.
- **Singurul caz fără reluare:** bon CONFIRMAT emis, dar vânzare neînregistrată în aplicație după reîncercări (fereastra spune „NU relua plata"; bonul chiar a ieșit).

## Sertarul de bani

Modul USB-serial (ex. Prolific PL2303 pe COM3): agentul scrie comanda pe port ca într-un fișier, exact ca `echo 1 > \\.\COM3` (setările portului sunt cele din Windows). Doar agentul poate: portul e al calculatorului de casă.

- **Setări** (`/setari` -> „Sertar de bani”; config `drawer`): `port` (ex. `COM3`, gol = fără sertar), `command` (implicit `1`; după el se adaugă CRLF, ca la `echo`), `auto_open` (implicit da). Butonul „Deschide sertarul (test)” din pagină îl încearcă.
- **Automat:** după ce un bon se tipărește (inclusiv când starea incertă se lămurește după) și are cel puțin o plată `cash` (și la plata mixtă), sertarul se deschide o singură dată pe job. Un bon doar cu cardul, un bon eșuat sau o oglindă (`payment_key` cunoscut) nu deschid. Starea apare în job: `drawer: {auto, opened, error}`.
- **La cerere:** `POST /drawer/open` (aceeași verificare de origine ca restul API-ului): 200 `{"ok": true}`, 409 `DRAWER_NOT_CONFIGURED`, 502 `DRAWER_FAILED` / `DRAWER_TIMEOUT` (portul nu răspunde în 3 s: agentul nu se blochează). `GET /health` are `drawer: {configured}`.
- Deschiderea e „best effort”: un sertar defect sau un port blocat nu strică niciodată un bon deja tipărit.

## Protecția la bon dublu: `external_id` și `payment_key`

- Cheia trimisă la FisCool (`Idempotency-Key`): `external_id` la prima încercare. Reluarea după un răspuns de EROARE al FisCool primește cheie nouă (`<id>-2`, ...), pentru că FisCool memorează eroarea pe cheie. Reluarea unei încercări rămase FĂRĂ răspuns (răspuns pierdut, termen de 30 s depășit, agent repornit) păstrează aceeași cheie: dacă bonul a ieșit totuși, FisCool nu îl tipărește a doua oară. Doar la FisCool; Fisco rămâne cu cheie nouă la fiecare reluare.

- `external_id` identifică un JOB. Același `external_id` cu același conținut = același job (200, fără retipărire); cu alt conținut sau alt `payment_key` = 400.
- `payment_key` identifică PLATA. Aplicația îl păstrează între joburile aceluiași bon cât timp nu e dovedit că bonul NU a ieșit (anulare automată
  când agentul părea pierdut, fereastră închisă). Un job nou (`external_id` nou) cu un `payment_key` pe care agentul îl cunoaște **nu mai tipărește**:
  devine o oglindă a jobului care execută plata. `GET /jobs/{external_id}` pe oglindă arată starea aceluiași job (cu `alias_of` = jobul real):
  `printed` cu același număr de bon, `running`/`uncertain` cât se lămurește, `failed` dacă a eșuat. `POST /jobs/{id}/retry` pe o oglindă reîncearcă jobul real
  (încercare nouă, cheie nouă la driver). Conținut diferit cu același `payment_key` = 400 (nu se poate compara cu bonul posibil ieșit).
- La un job real, cheia de idempotență a FisCool rămâne `external_id` al lui (apoi `-2`, `-3` la reîncercări); la Fisco, care nu are idempotență, protecția e doar aici.
- Oglinzile și `payment_key` stau în baza locală, deci supraviețuiesc repornirii agentului. Fără `payment_key` în cerere comportamentul e cel de dinainte.
- Rămâne neprotejat: același bon refăcut cu ALT conținut după o anulare fără dovadă (aplicația îl refuză cu 409 până revine la același conținut), și un `payment_key` NOU
  generat de aplicație după o „dovadă" falsă (confirmare de operator greșită).

## Identitatea agentului și rezultatul păstrat

- `agent_id` (generat o singură dată, la prima pornire) apare în `/health` și în fiecare job. Backend-ul leagă jobul de agentul care l-a primit și
  refuză raportările de la alt agent: un bon pornit pe un calculator nu se retrimite, la reluare, pe altul (s-ar putea tipări de două ori).
- Rezultatul unui job (inclusiv numărul bonului, Z, seria) rămâne în baza locală a agentului (`jobs.sqlite3`) cât timp nu e șters manual: dacă
  nu e conexiune la server în momentul tipăririi, browserul îl citește din nou și îl raportează când revine conexiunea (fără retipărire).

## Securitate (fără certificat)

Bind pe 127.0.0.1; antetul `Host` verificat; `Origin` verificat; `/setari` cere parola de admin (PBKDF2, cookie
HttpOnly + SameSite=Strict, blocare 60 s după 5 parole greșite).

## Ce garantează fiecare driver (verificat în documentația oficială)

- **Fisco** ([manual](https://fisco.ro/manual-fisco/)): NU documentează nicio idempotență pe `external_id` (apare doar în exemplul de
  cerere) și recomandă „evitați retrimiterea automată a aceluiași bon fără verificarea statusului". De aceea protecția la bon dublu
  (snapshot înainte de POST, reconciliere pe lista de joburi, nicio retrimitere fără dovadă negativă) e în agent, în driverul Fisco.
  TVA: se trimite procentul (`21%`), iar Fisco alege cota cu acel procent din casă (eroare dacă nu există); `vat_map` e doar o înlocuire opțională.
- **FisCool** ([documentație](https://fiscool.ro/documentatie/)): `Idempotency-Key` documentat — aceeași cheie = o singură execuție, deci
  un răspuns pierdut se lămurește retrimițând aceeași cerere cu aceeași cheie. Erorile se clasifică pe cod, după pagina „Coduri Eroare":
  tranzitorii (408, 605, 606, 1005) → incert, se retrimite cu aceeași cheie; dinainte de tipărire / stare / configurare (1004, 505, 530,
  531, 532, 544, 545, 400, 404, 540, 1009, POS 600-604) → eșec cu retrimitere sigură; orice altceva → eșec cu confirmare de la operator.
  `fiscal_receipt_count` e contorul bonurilor fiscale ale DISPOZITIVULUI (nu numărul din raportul Z): `receipt_in_z` rămâne null.
  Seria aparatului vine din `GET /devices/fiscal` (`connected_device_id`). Rapoartele X/Z (`print_x`, `print_z`) sunt implementate după documentație.
  Health = starea dispozitivului din `/devices/fiscal` (`Connected`). Cererile fără header `Origin` (agentul) nu trec prin dialogul de confirmare al FisCool.

## Mesajele driverelor (transportate integral)

Orice a spus driverul ajunge neschimbat în `error` / `result` și de acolo în Dressbit:

- **Succes:** `result.message` (FisCool `msg`, ex. „Receipt printed") și `result.raw` = răspunsul COMPLET al driverului (Fisco: tot JSON-ul de status, cu
  `job_id` și `result`; FisCool: tot corpul, cu `fiscal_receipt_count`, `bon_id`, totalurile raportului etc.).
- **Eroare:** `message` (textul driverului), plus `driver_code`, `driver_name`, `driver_description`, `resolution`, `receipt`, `driver_response`.
  - FisCool: tabelul complet al celor 72 de coduri din „Coduri Eroare" (`drivers/catalog_fiscool.py`, verificat automat față de pagină): nume, descriere
    și rezolvare/cauze exact ca în documentație; `receipt` = chitanța de refuz POS; `driver_response` = corpul erorii + `http_status`.
  - Fisco: cele 11 coduri API din manual (`drivers/catalog_fisco.py`); codurile imprimantei (`#ErrorCode (-112201)`) vin în `message` și nu sunt tabelate
    în manual; rezolvarea documentată există doar pentru depășirea celor 24h fără raport Z.
  - Un cod care nu e în documentație trece oricum cu tot ce a spus driverul (fără nume/descriere/rezolvare).
- **Stare casă (health):** aceleași câmpuri, ca să se vadă și motivul pentru care nu e pregătită.

## POS bancar: Newland N910 Pro, protocol ECR Hypercom (T7/FIP11 v1.06)

Se alege în `/setari`, secțiunea „POS bancar” (grupul „Raiffeisen”, model „ECR Hypercom (Newland N910 Pro)”), și se scrie de mână portul serial al terminalului
(Windows: `COM4`, numărul din Manager dispozitive după ce driverul USB îl vede și interfața e activată pe terminal; Linux: `/dev/ttyACM0` sau `/dev/ttyUSB0`). Implicit, din document: 9600 baud, 7 biți,
paritate pară, 1 stop; 30 s pentru pornirea plății, 120 s pentru card și PIN, 1,5 s înainte de confirmarea rezultatului. Butonul „Verifică portul POS”
deschide și închide portul, fără să trimită nimic. Fără POS configurat (`none`) agentul nu apelează terminalul.

Plata cu cardul sau mixtă: POS ÎNAINTEA bonului fiscal, pe suma de card.
1. `MOL10.<suma pe 12 cifre>` -> ACK -> `MOL11.` (terminalul a pornit) -> ACK -> `MOL12.` (rezultatul) -> ACK -> (pauză) `MOL13.` -> ACK.
2. Fără ACK la 10 (după 3 retrimiteri) sau fără 11 în 30 s: terminalul e offline, nimic nu a pornit => eșec sigur (`POS_OFFLINE`), plata se poate relua.
3. 11 primit, dar fără 12 în timpul de plată (sau conexiunea cade după ce comanda a plecat): protocolul nu are interogare de stare, deci plata se consideră
   **neîncasată**: eșec cu „Reîncearcă plata” (`POS_NO_RESULT`, `retry_safe`), fără nicio decizie pentru casier. Mesajul de eroare îi spune doar să nu reia
   plata dacă clientul a fost debitat. Plata nu se retrimite niciodată singură; reluarea o face casierul. Rămâne riscul „încasat, dar fără rezultat” (POS-ul
   a încasat și cablul a căzut chiar atunci), același ca la o cădere de curent.
4. 12 cu cod `00`: aprobat. Aprobarea (număr factură, cod aprobare, card mascat) se salvează în baza agentului ÎNAINTE de ACK-ul către POS; apoi bonul
   fiscal. 13 e „best effort”: plata rămâne aprobată și dacă 13 nu e confirmat.
5. 12 cu alt cod: respins (`POS_DECLINED`, mesaj în română pentru codurile uzuale), nu iese bon, plata se poate relua.
6. Mesajele care nu sunt ale noastre (FIP-11 trimite tot traficul și către PIN pad) se ignoră, fără ACK/NAK; cele ale noastre cu LRC greșit primesc NAK.
7. Reluarea după un bon fiscal eșuat nu cere POS-ul din nou (`pos.status == approved`); după o cădere a agentului cu aprobarea deja salvată,
   lămurirea trece direct la „reîncearcă tipărirea”, fără POS.
**Decontarea:** după un raport Z tipărit cu succes, agentul trimite `STL` la POS (o singură dată pe job), exact ca „Decontare” apăsată pe terminal:
`STL10.` -> ACK -> `STL11.` -> ACK (fără mesajul 12). Rezultatul e pe slip-ul terminalului. Fără setări și fără mesaje în Dressbit: dacă nu iese,
se dă manual de pe terminal; nu blochează niciodată raportul Z. Nu se trimite după raport X, după bon sau când POS-ul nu e configurat.
**Anularea plății la POS (`VOI`), doar când bonul fiscal nu poate ieși:** `POST /jobs/<id>/pos-void`. Merge doar pentru un job `failed` cu plata `approved`
(niciodată pentru un bon tipărit, o plată respinsă, una deja anulată sau cât rulează altă operațiune). Conversația e cea a plății, cu `VOI` în loc de `MOL` și
numărul chitanței POS (cel de la plată, `transaction_id`) pe 12 cifre în câmpul sumei. Cu succes: `pos.status = voided` (salvat înainte de ACK), jobul rămâne `failed`;
reluarea ulterioară cere din nou POS-ul (plata nu mai e aprobată). Altfel: 502 cu motivul și „Anulează chitanța nr. X direct de pe POS”; plata rămâne `approved`.
În Dressbit, în fereastra de eșec cu cardul încasat, „Închide” e înlocuit de „Anulează chitanța POS”; la succes plata se închide, iar dacă anularea nu merge apare mesajul
cu numărul chitanței și revine „Închide” (ca fereastra să nu blocheze magazinul). Formatul câmpului numărului de chitanță NU e verificat pe terminal.
PAN-ul complet și data de expirare nu se păstrează nicăieri; RRN nu există în acest protocol (`transaction_id` = numărul facturii).

## Detectarea porturilor și pagina de setări

În `/setari`, lângă portul POS-ului și al sertarului, **„Detectează porturi”** arată echipamentele seriale conectate (nume, producător, USB id) și salvează la click calea cea mai stabilă
(`worldbit_fiscal/ports.py`):
- **Linux:** `/dev/serial/by-id/usb-<producător>_<model>_<serie>-if00`, creată de sistem din numărul de serie al echipamentului, deci rămâne aceeași indiferent în ce port USB îl muți
  (`/dev/ttyACM0` / `/dev/ttyUSB0` se numerotează în ordinea conectării și se pot schimba). Dacă echipamentul nu are nicio cale stabilă, apare avertisment și o regulă udev sugerată
  (se pune ca root în `/etc/udev/rules.d/`, creează `/dev/pos`). Un echipament fără număr de serie are tot cale fixă, dar două la fel nu se pot deosebi.
- **Windows:** `COMn` cu numele dispozitivului. Windows ține numărul COM legat de echipament doar dacă acesta are număr de serie USB; fără el, numărul urmărește portul USB (avertisment afișat).
Pagina de setări e HTML simplu, fără resurse externe: card-uri, bară de stare sus (casa de marcat, POS), bară de salvare fixă, mod întunecat, potrivită și pe telefon.

## Curățarea bazei de joburi

Joburile TERMINATE (tipărite, eșuate, oglinzi) mai vechi de `retention_days` (implicit 7, în `config.json`) se șterg din baza agentului la pornire și apoi la fiecare 6 ore.
Nu se șterge niciodată un job în lucru sau incert, nici unul de care depinde o oglindă mai nouă. Dressbit își păstrează singur istoricul; agentul are nevoie doar de
ce îl ajută să recunoască o plată reluată.

## Versiuni și actualizare

- **Versiunea** e în `worldbit_fiscal/version.py` (`MAJOR.MINOR.PATCH`) și apare în `/health` și în `/setari`. `1.0.x` = corecții în agent, `1.x.0` = driver fiscal sau
  POS nou, `x.0.0` = schimbare de structură. Începem cu `1.0.0`.
- **Butonul „Actualizează”** din `/setari` (doar la apăsare, niciodată automat) descarcă ultima versiune din repo-ul public de pe GitHub
  (`main`; adresa se poate schimba în `config.json`, cheia `update_url`) și o instalează **doar dacă are modificări pentru ce rulează pe acest calculator**:
  miezul agentului + fișierele driverului fiscal ales + fișierele modelului de POS ales (`files` din clasele lor). Nu contează versiunea, listele de
  înregistrare (`drivers/__init__.py`, `pos/__init__.py`) și driverele/modelele neinstalate aici: dacă doar ele s-au schimbat, mesajul e „Nimic de actualizat”.
- **Ca o versiune să ajungă la calculatoare:** crești numărul în `version.py` și faci push pe `main`. Fără creșterea numărului, butonul spune că ai deja ultima versiune.
- **Verificarea înainte de instalare:** pachetul nou se compilează și se importă într-un proces separat, cu același Python ca agentul, ÎNAINTE de orice oprire. O eroare de sintaxă
  sau un modul lipsă se refuză pe loc, cu fișierul, linia și motivul, iar agentul care rulează nu e atins (fără pauză, fără revenire).
- **Copie de siguranță:** înainte de schimbare, `config.json` și `jobs.sqlite3` se copiază în `backup\<versiune>-<data>\` (ultimele 3 rămân); la revenire se pun înapoi.
- **Dacă versiunea nouă trece verificarea, dar pornește greșit:** scriptul o pornește în prim-plan 8 secunde (agentul fiind oprit), citește ce scrie (excepția completă), apoi revine și
  scrie motivul în `update\result.json` și `last.txt`; fereastra din `/setari` îl afișează (`GET /setari/update-status`). Așteptarea e de 40 s reale.
- **Instalarea** (doar pe instalarea normală de pe Windows): agentul pregătește pachetul nou, apoi un al doilea task programat oprește agentul, schimbă dosarul
  `worldbit_fiscal` (cel vechi rămâne în `worldbit_fiscal.bak`), repornește agentul și așteaptă să răspundă în `/health` cu versiunea nouă; dacă nu răspunde în
  40 s, pune înapoi versiunea veche. Rezultatul ultimei actualizări apare în `/setari`. În pagină, butonul deschide o **fereastră de progres** (verificare și descărcare, instalare și repornire, gata), care urmărește `/health` până agentul revine cu versiunea nouă și apoi reîncarcă pagina singură; dacă versiunea nouă nu pornește, spune asta (agentul a revenit la cea veche). Sesiunea de admin se păstrează peste repornire (`sessions.json`, în dosarul de date). Nu pornește dacă sunt operațiuni fiscale în curs. Configurația și baza de
  joburi nu se ating. Pe Linux/macOS butonul doar anunță dacă există ceva de actualizat; instalarea se face reinstalând.
- **Când adaugi un driver fiscal sau un model de POS:** fișier nou în `drivers/` sau `pos/`, `files` completat în clasă, înregistrarea în `drivers/__init__.py` sau
  `pos/__init__.py`, versiune `1.x.0`. Calculatoarele care nu folosesc acel driver/model nu sunt afectate (butonul le spune „nimic de actualizat”).
- Lista POS din `/setari` e grupată pe bancă (aplicația băncii din terminal stabilește protocolul, nu marca terminalului); în paranteză sunt terminalele pe care
  protocolul a fost verificat.
- Integritatea: descărcarea e prin HTTPS de pe GitHub; cine controlează contul GitHub controlează ce se instalează la apăsarea butonului. Autentificare în două
  trepte pe cont. Semnarea release-urilor (cheia publică în agent) e o îmbunătățire posibilă.

## Verificat cap-coadă (Windows 11 în VM, Chrome 156)

Pagina HTTPS (adresă privată, certificat de test) cu **clientul real din Dressbit** (`agent.ts`) -> agent instalat ca serviciu -> port COM virtual (com0com, COM50 <-> COM51)
-> POS fals (acesta pe COM51) și FisCool cu casa simulată: numerar, plată mixtă, card respins, POS fără rezultat, POS oprit, încasat dar casa fără hârtie (anulare reușită,
anulare refuzată de terminal, a doua anulare refuzată), reîncercare după hârtie fără a cere din nou POS-ul, decontare la Z. Pe Linux, aceleași scenarii prin backend-ul
Dressbit real (`FiscalAgentEndToEndTest`, port virtual pty). Chrome acceptă apelul către `127.0.0.1:4747` cu politica instalată; răspunsul la verificarea „Private Network
Access” (`Access-Control-Allow-Private-Network`) e adăugat în agent pentru originile permise. NU s-a putut reproduce cazul „site public pe internet” (fără un tunel public).

## NEVERIFICAT / ÎN AȘTEPTARE

- **FisCool**: dacă memorează și ERORILE pe cheia de idempotență (dacă da, o reîncercare pe aceeași cheie după o eroare tranzitorie
  întoarce aceeași eroare; atunci operatorul reîncearcă cu confirmare, pe cheie nouă); dacă pornește singur după boot și se conectează
  la casă fără click (manualul spune doar că poate rula în fundal și pornește automat din Setări). Se confirmă pe un aparat real (14 zile gratuite).
- **POS (decontare, anulare, mesaje nedocumentate)**: documentul ECR nu definește cum răspunde terminalul la „Cancel”, nici tabelul codurilor de
  răspuns (trimite la ISO-8583). Parametrii seriali prin USB (documentul descrie RS-232 la 9600, 7E1) și comportamentul la „Cancel” se
  stabilesc pe terminalul real. Anularea plății (`VOI`) există DOAR pentru cazul „POS a încasat, bonul fiscal nu iese” (vezi mai jos). Decontarea (`STL`) la Z e scrisă după document, netestată pe terminal.

- **Chrome**: promptul/politica Local Network Access și valoarea `targetAddressSpace` se testează pe un Chrome real, cu pagină HTTPS
  publică; frontend-ul nu folosește acum `targetAddressSpace`.

## Instalare pe Windows (`installer/`)

`installer\install.ps1 -Driver fisco|fiscool -Origin https://test.worldbit.ro [-Port 4747]` (ca administrator; se re-lansează singur): instalează Python 3.12 pentru toți utilizatorii dacă lipsește, copiază agentul în `%ProgramFiles%\WorldbitFiscalDriver`, rulează `init`, scrie politica Chrome, creează task-ul programat `WorldbitFiscalDriver` (la pornirea PC-ului, SYSTEM, ascuns, repornește la cădere) și așteaptă `/health`. Datele stau în `%ProgramData%\WorldbitFiscalDriver` (acces doar SYSTEM + administratori). `installer\uninstall.ps1 [-Origin …] [-StergeDate]` îl scoate.

**Chrome fără prompt:** `python -m worldbit_fiscal chrome-policy add|remove|status --origin …` scrie originea în `HKLM\SOFTWARE\Policies\Google\Chrome\LocalNetworkAccessAllowedForUrls` (Chrome 142–145) și `…\LoopbackNetworkAllowedForUrls` (146+), fără să atingă intrările altora. Efecte: Chrome arată „Managed by your organization"; se aplică după repornirea completă a Chrome (verificare: `chrome://policy`).

**VERIFICAT (Windows 11 în VM, cu FisCool și casă simulată):** instalarea curată (inclusiv Python 3.12, instalat cu `/passive`: cu `/quiet` programul de instalare iese cu cod 0 fără să instaleze), task-ul `WorldbitFiscalDriver` (SYSTEM, la pornire, pornește singur după repornirea PC-ului), `/setari` (login, „Detectează dispozitive”, salvare), bon mixt tipărit, job nou cu același `payment_key` fără retipărire, raport X, scrierea politicii Chrome în registru, dezinstalarea (task, fișiere, date, politică). Scripturile folosesc `python -E -s`, nu `-I`: modul izolat ignoră directorul curent și `-m worldbit_fiscal` nu ar găsi pachetul.

**NEVERIFICAT:** efectul politicii Chrome asupra promptului de acces la rețeaua locală (pe Chrome real, cu pagină HTTPS publică), driverul Fisco pe Windows, instalarea pe un PC fără internet (Python se descarcă de pe python.org).
