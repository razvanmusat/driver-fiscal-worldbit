# Agent fiscal Worldbit

Agent local (Python 3.10+, doar biblioteca standard) care leagă o aplicație web de echipamentele de la casă: **casa de marcat**, **POS-ul bancar** și **sertarul de bani**.
Aplicația web trimite agentului un JSON universal de bon; agentul îl transmite echipamentelor prin driverul/modulul ales la instalare și întoarce starea.
Browserul vorbește doar cu agentul, pe `http://127.0.0.1:<port>`. Un agent per calculator, cu echipamentele acelui calculator.

| Echipament | Disponibil acum | Se adaugă |
|---|---|---|
| Casă de marcat | Fisco, FisCool | alte drivere, câte un fișier în `drivers/` |
| POS bancar | Raiffeisen: ECR Hypercom (Newland N910 Pro) | alte bănci/protocoale, câte un fișier în `pos/` |
| Sertar de bani | modul USB-serial pe port COM | |

Ordinea într-un job: verificare casă de marcat → POS (doar dacă bonul are plată cu cardul și există POS configurat) → bon fiscal. Un singur job în zbor pe agent.

## Instalare și pornire

**Windows:** `Setup-DriverFiscalWorldbit.exe` (se compilează cu Inno Setup din `installer\Setup.iss`, rezultă în `installer\Output\`) sau direct `installer\install.ps1 -Driver fisco|fiscool -Origin https://aplicatia-ta.ro [-Pos none|hypercom_ecr] [-Port 4747]`, ca administrator.
Instalarea pune Python 3.12 dacă lipsește, copiază agentul în `%ProgramFiles%\WorldbitFiscalDriver`, scrie configurația, adaugă politica Chrome care aprobă originea aplicației și creează task-ul programat `WorldbitFiscalDriver`
(la pornirea PC-ului, SYSTEM, repornește la cădere). Driverele casei și ale POS-ului (FisCool, Fisco, driverul USB al terminalului) se instalează separat; instalatorul arată linkul oficial (`installer\drivere.json`).
Dezinstalare: `installer\uninstall.ps1 [-Origin …] [-StergeDate]`.

**Manual (orice sistem):**
```
python -m worldbit_fiscal init --driver fiscool --port 4747 --origin https://aplicatia-ta.ro   # o singură dată; cere parola de admin pentru /setari
python -m worldbit_fiscal run
```
Configurația și baza de joburi stau în `%ProgramData%\WorldbitFiscalDriver` (Windows) sau `~/.worldbit-fiscal-driver`, suprascriibil cu `WORLDBIT_FISCAL_HOME`. Casa de marcat se schimbă doar rulând din nou `init`.

Teste: `python -m unittest discover -s tests -t .` (sau `pytest`).

## API pentru aplicația web

CORS doar pentru originile din `allowed_origins`.

| Metodă | Cale | Rol |
|---|---|---|
| GET | `/health` | `{agent_id, version, driver, fiscal:{ready,detail}, pos:{model,configured}, drawer:{configured}}` |
| POST | `/print` | pornește un bon (202; același `external_id` cu același conținut = 200, fără retipărire) |
| POST | `/reports` | `{external_id, type:"X"\|"Z"}` |
| GET | `/jobs/{external_id}` | starea jobului; pe un job `uncertain` declanșează verificarea |
| POST | `/jobs/{external_id}/retry` | reîncercare după `failed`; `{"confirm_not_printed": true}` dacă `error.retry_safe` e false |
| POST | `/jobs/{external_id}/pos-void` | anulează la POS o plată aprobată al cărei bon fiscal nu a putut ieși |
| POST | `/drawer/open` | deschide sertarul |

### Cererea `POST /print`

```json
{
  "external_id": "vanzare-123",
  "lines": [{"name": "Rochie roșie M", "unit_price": "100.00", "quantity": 1, "vat_percent": 21, "unit": "buc"}],
  "payments": [{"method": "cash", "amount": "60.00"}, {"method": "card", "amount": "40.00"}],
  "total": "100.00",
  "payment_key": "plata-6f1c0b6e",
  "buyer": {"cui": "RO123456"}
}
```

Sumele sunt zecimale (max. 2 zecimale, cantitatea max. 3), cu TVA inclus; `total` = suma liniilor = suma plăților, altfel 400 înainte de orice contact cu aparatul. Metode de plată: `cash`, `card`.
`buyer` e opțional (implementat în FisCool). `payment_key` e opțional (1-64 caractere `[A-Za-z0-9._-]`), vezi „Protecția la bon dublu”.

### Răspunsul (`POST /print`, `GET /jobs/...`)

```json
{
  "ok": true, "external_id": "...", "kind": "receipt", "status": "printed", "attempt": 1, "driver": "fisco",
  "fiscal_job_id": null,
  "result": {"slip_number": "6147", "z_report_number": "430", "receipt_in_z": "10", "device_serial": "DB41...", "report_number": null, "message": null, "raw": {}},
  "error": null,
  "pos": null
}
```

`status`: `received` | `running` | `printed` | `failed` (dovadă că nu s-a tipărit) | `uncertain` (nu se știe; fără retrimitere automată).
`error = {code, message, retry_safe, ...}`; pe lângă ele ajunge tot ce a spus driverul (cod, nume, descriere și rezolvare documentate, răspunsul brut), iar `result.raw` conține răspunsul complet al driverului.

## Comportament automat

- **Eșecuri trecătoare documentate de driver** (casă ocupată, conexiune încă neridicată) se reîncearcă singure (implicit 3 × la 3 s, `auto_retry.max` / `auto_retry.delay_s` în `config.json`).
- **Stare incertă** se verifică automat (Fisco: reconciliere pe lista de joburi; FisCool: aceeași cheie de idempotență).
- **Termen total** de 30 s (`max_wait_s`; la rapoarte 120 s, `max_wait_report_s`): un bon neconfirmat ca emis în acest timp se închide ca eșuat (`NOT_CONFIRMED`, retrimitere sigură). O plată cu cardul nelămurită nu se abandonează.
  Nici Fisco, nici FisCool nu au comandă de anulare: un bon acceptat de driver după termen poate ieși totuși.
- **Rezultatul** (numărul bonului, Z, seria) rămâne în baza locală a agentului (`jobs.sqlite3`), deci poate fi citit din nou dacă aplicația n-a apucat să-l preia. Joburile terminate mai vechi de `retention_days` (implicit 7) se șterg; cele în lucru sau incerte nu se șterg niciodată.

## Protecția la bon dublu

- `external_id` identifică un JOB. Același `external_id` cu același conținut = același job; cu alt conținut = 400.
- `payment_key` identifică PLATA și rămâne stabil între joburile aceluiași bon. Un job nou cu un `payment_key` cunoscut nu tipărește: devine o oglindă a jobului care execută plata (`alias_of`) și arată aceeași stare. Același `payment_key` cu alt conținut = 400.
- `agent_id` (generat la prima pornire, în `/health` și în fiecare job) permite aplicației să refuze raportări de la alt agent decât cel care a primit jobul.
- **FisCool:** cheia de idempotență (`Idempotency-Key`) e `external_id` (apoi `-2`, `-3` la reîncercări după eroare, pentru că FisCool memorează și erorile pe cheie). O încercare rămasă fără răspuns se reia cu aceeași cheie. FisCool ține cheile doar în memorie: după repornirea lui aceeași cheie se execută din nou.
- **Fisco** nu are idempotență: protecția e în agent (snapshot înainte de trimitere, reconciliere, nicio retrimitere fără dovadă negativă).

## Drivere casă de marcat

- **FisCool** ([documentație](https://fiscool.ro/documentatie/)): erorile se clasifică pe cod după pagina „Coduri Eroare” (tranzitorii, sigure, restul cu confirmare de la operator); tabelul celor 72 de coduri e în `drivers/catalog_fiscool.py`.
  TVA: se trimite procentul (`21%`), iar FisCool alege grupa aparatului cu acea cotă; câmpul „Înlocuire TVA” din `/setari` mapează o cotă pe o literă de grupă, iar butonul **„Interoghează cotele TVA din casă”** arată grupele citite de pe aparat (`vat_rates`) și completează câmpul la alegere.
  `fiscal_receipt_count` e contorul bonurilor aparatului (nu numărul din raportul Z), deci `receipt_in_z` rămâne gol. Rapoartele X/Z sunt implementate.
- **Fisco** ([manual](https://fisco.ro/manual-fisco/)): TVA se trimite ca procent, iar Fisco alege cota cu acel procent; codurile API din manual sunt în `drivers/catalog_fisco.py`.

## POS bancar: Raiffeisen, ECR Hypercom (Newland N910 Pro)

Se alege în `/setari` → POS (portul serial al terminalului se scrie sau se alege cu „Detectează porturi”). Parametrii seriali și termenele implicite sunt cele din documentul băncii.
Plata cu cardul sau mixtă: POS înaintea bonului fiscal, pe suma de card.

1. Pornire plată → terminalul confirmă → rezultat. Terminal offline = eșec sigur (`POS_OFFLINE`). Fără rezultat după pornire = „plată neîncasată” cu reîncercare (`POS_NO_RESULT`); protocolul nu are interogare de stare.
2. Aprobarea se salvează în baza agentului înainte de a fi confirmată terminalului. Respins = `POS_DECLINED`, nu iese bon.
3. Reîncercarea după un bon fiscal eșuat nu cere POS-ul din nou.
4. **Anulare (`VOI`):** doar pentru un job eșuat cu plata aprobată (`POST /jobs/{id}/pos-void`); niciodată pentru un bon tipărit.
5. **Decontare (`STL`):** trimisă o singură dată după un raport Z tipărit; dacă nu iese, se dă manual de pe terminal.

Pe terminal real sunt netestate: anularea, decontarea, comportamentul la „Cancel” și formatul numărului de chitanță din `VOI`.

## Sertar de bani

Modul USB-serial (de ex. pe COM3): agentul scrie comanda pe port ca `echo 1 > \\.\COM3`. Setări în `/setari` → Sertar: port, comandă (implicit `1`, urmată de CRLF), deschidere automată.
Se deschide singur după un bon tipărit cu cel puțin o plată `cash`, o dată pe job; un sertar defect nu strică niciodată un bon tipărit. La cerere: `POST /drawer/open`.

## Pagina de setări (`/setari`)

Cere parola de admin. Tab-uri: Agent (port, origini permise), Casa de marcat, POS, Sertar, Actualizare, Schimbă parola. „Detectează porturi” listează echipamentele seriale (Linux: cale stabilă `/dev/serial/by-id/...`; Windows: `COMn` cu nume).

## Securitate

Bind pe 127.0.0.1; antetul `Host` și `Origin` verificate; `/setari` cere parola de admin (PBKDF2, cookie HttpOnly + SameSite=Strict, blocare 60 s după 5 parole greșite). Chrome: instalatorul scrie politica `LocalNetworkAccessAllowedForUrls` / `LoopbackNetworkAllowedForUrls`
pentru originea aplicației (`python -m worldbit_fiscal chrome-policy add|remove|status --origin …`, Windows, administrator); se aplică după repornirea completă a Chrome.

## Versiuni și actualizare

Versiunea e în `worldbit_fiscal/version.py` (`MAJOR.MINOR.PATCH`) și apare în `/health` și `/setari`: `x.y.Z` = corecții, `x.Y.0` = funcție, driver sau POS nou, `X.0.0` = schimbare de structură.

Butonul „Actualizează” din `/setari` (doar la apăsare, niciodată automat) descarcă ultima versiune din repo-ul public de pe GitHub (`main`; adresa se schimbă în `config.json`, cheia `update_url`) și o instalează doar dacă are modificări pentru
ce rulează pe acel calculator (miezul agentului, driverul fiscal ales și modelul de POS ales). Ca o versiune să ajungă la calculatoare: crești numărul în `version.py` și faci push pe `main`.
Înainte de instalare pachetul nou se compilează și se importă într-un proces separat; `config.json` și `jobs.sqlite3` se copiază în `backup\`; dacă versiunea nouă nu pornește în 40 s, se revine la cea veche și motivul apare în `/setari`. Instalarea automată merge doar pe Windows (instalarea normală); pe celelalte sisteme butonul doar anunță.
Cine controlează contul GitHub controlează ce se instalează.

Pentru un driver sau POS nou: fișier în `drivers/` sau `pos/`, `files` completat în clasă, înregistrare în `drivers/__init__.py` sau `pos/__init__.py`, versiune nouă.

## Netestat pe echipamente reale

Daisy Expert XS cu FisCool/Fisco, driverul Fisco pe Windows, POS-ul pe terminal real, Chrome cu un site public care apelează agentul local (Local Network Access), instalarea pe un PC fără internet.
