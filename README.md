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

## NEVERIFICAT / ÎN AȘTEPTARE

- **FisCool**: dacă memorează și ERORILE pe cheia de idempotență (dacă da, o reîncercare pe aceeași cheie după o eroare tranzitorie
  întoarce aceeași eroare; atunci operatorul reîncearcă cu confirmare, pe cheie nouă); dacă pornește singur după boot și se conectează
  la casă fără click (manualul spune doar că poate rula în fundal și pornește automat din Setări). Se confirmă pe un aparat real (14 zile gratuite).
- **POS**: nu există niciun modul în agent. FisCool poate face singur POS-ul: dacă la dispozitiv e activ `auto_send_card_to_pos`, suma cardului
  se trimite către POS-ul asociat înaintea bonului (răspunsul bonului NU conține RRN/cod autorizare); există și operația `sale` (cu
  `transaction_id`, `authorization_code`) și `void`. Un modul de POS din agent peste `sale` ar trebui să refuze să ruleze când
  `auto_send_card_to_pos` e activ (risc de dublă debitare). Neimplementat: așteaptă decizia și testul pe aparat. Anularea unei plăți POS
  aprobate dacă bonul se abandonează nu e implementată.
- **Chrome**: promptul/politica Local Network Access și valoarea `targetAddressSpace` se testează pe un Chrome real, cu pagină HTTPS
  publică; frontend-ul nu folosește acum `targetAddressSpace`.

## Instalare pe Windows (`installer/`)

`installer\install.ps1 -Driver fisco|fiscool -Origin https://test.worldbit.ro [-Port 4747]` (ca administrator; se re-lansează singur): instalează Python 3.12 pentru toți utilizatorii dacă lipsește, copiază agentul în `%ProgramFiles%\WorldbitFiscalDriver`, rulează `init`, scrie politica Chrome, creează task-ul programat `WorldbitFiscalDriver` (la pornirea PC-ului, SYSTEM, ascuns, repornește la cădere) și așteaptă `/health`. Datele stau în `%ProgramData%\WorldbitFiscalDriver` (acces doar SYSTEM + administratori). `installer\uninstall.ps1 [-Origin …] [-StergeDate]` îl scoate.

**Chrome fără prompt:** `python -m worldbit_fiscal chrome-policy add|remove|status --origin …` scrie originea în `HKLM\SOFTWARE\Policies\Google\Chrome\LocalNetworkAccessAllowedForUrls` (Chrome 142–145) și `…\LoopbackNetworkAllowedForUrls` (146+), fără să atingă intrările altora. Efecte: Chrome arată „Managed by your organization"; se aplică după repornirea completă a Chrome (verificare: `chrome://policy`).

**VERIFICAT (Windows 11 în VM, cu FisCool și casă simulată):** instalarea curată (inclusiv Python 3.12, instalat cu `/passive`: cu `/quiet` programul de instalare iese cu cod 0 fără să instaleze), task-ul `WorldbitFiscalDriver` (SYSTEM, la pornire, pornește singur după repornirea PC-ului), `/setari` (login, „Detectează dispozitive”, salvare), bon mixt tipărit, job nou cu același `payment_key` fără retipărire, raport X, scrierea politicii Chrome în registru, dezinstalarea (task, fișiere, date, politică). Scripturile folosesc `python -E -s`, nu `-I`: modul izolat ignoră directorul curent și `-m worldbit_fiscal` nu ar găsi pachetul.

**NEVERIFICAT:** efectul politicii Chrome asupra promptului de acces la rețeaua locală (pe Chrome real, cu pagină HTTPS publică), driverul Fisco pe Windows, instalarea pe un PC fără internet (Python se descarcă de pe python.org).
