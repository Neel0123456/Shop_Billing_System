# Shop Billing

A Django billing app for a single-counter retail shop, running on one Windows PC.

Enter the customer details and line items, press **Save & Print**, and the bill
is stored in PostgreSQL, archived as a PDF on disk, and sent straight to the
printer — no dialog, no manual steps.

```
Web form  ->  PostgreSQL          (the record)
          ->  Word template       (your bill format, filled with the data)
          ->  .docx  ->  PDF      (converted by Word itself)
          ->  archived on disk    (INVOICE_PDF_DIR)
          ->  Windows spooler     (the paper)
```

---

## Can it really print directly to the printer?

**Yes — because Django runs on the same PC as the printer.** The server process
*is* the shop computer, so Python hands the PDF to the Windows spooler
directly. There is no browser sandbox in the way.

The printing layer is a pluggable backend, chosen by one line in `.env`:

| `PRINT_BACKEND` | What it does | When to use it |
|---|---|---|
| `dummy` | **Current default.** Writes the exact PDF it *would* have printed into `media/print_spool/` and records the job. Nothing reaches hardware. | No printer attached yet |
| `windows` | Silent printing through the Windows spooler | **On the shop PC** |
| `cups` | `lp` on Linux/macOS | Non-Windows |
| `escpos` | Direct ESC/POS to a thermal receipt printer (USB or network) | 80 mm receipt roll |
| `browser` | Falls back to the browser print dialog | App hosted on a *different* machine |

Switching to the real printer is a config change, not a code change:

```ini
PRINT_BACKEND=windows
PRINTER_NAME=EPSON L3250 Series          # exact name from Devices and Printers
PRINT_COPIES=1
SUMATRA_PDF_PATH=C:/Tools/SumatraPDF/SumatraPDF.exe
```

`SUMATRA_PDF_PATH` is optional but **strongly recommended**: SumatraPDF's
`-print-to` is genuinely dialog-free and honours the copy count. Without it the
app falls back to Adobe Reader's `/t` switch, then to the Windows shell print
verb — both work, but neither is guaranteed silent on every machine.

The **Printing** page in the app (`/billing/settings/print/`) shows exactly
which engine, converter and printer are live, lists the printers Windows
reports, and has a **Send a test print** button. Open that page first whenever
a bill does not come out.

---

## Setting up the shop PC

Everything needed on a fresh Windows machine, in order. No demo data — the
shop starts with an empty book.

### 0. Install these first (one time, outside this project)

| Software | Why | Notes |
|---|---|---|
| **Python 3.12+** | runs the app | Tick **“Add python.exe to PATH”** in the installer |
| **PostgreSQL 16+ (with pgAdmin)** | the database | Remember the `postgres` password you set |
| **Microsoft Word** | converts the bill template to PDF | Usually already installed. If not, install **LibreOffice** instead |
| **SumatraPDF** (portable, 64-bit) | silent printing | From <https://www.sumatrapdfreader.org/download-free-pdf-viewer> — see [setup.md](setup.md) |

### 1. Copy the project and create the environment

```powershell
cd D:\ShopBilling                      # wherever the project folder lives

python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
```

### 2. Create the database in pgAdmin

Open **pgAdmin** → *Servers* → *PostgreSQL* → **Databases** → right-click →
**Create → Database…** → name it `shop_billing` → **Save**.

### 3. Create the config file

```powershell
copy .env.example .env
notepad .env
```

Set these five lines (leave the rest as they come):

```ini
SECRET_KEY=<paste the long random string generated in step 4>
DATABASE_URL=postgres://postgres:YOUR_PG_PASSWORD@127.0.0.1:5432/shop_billing
USE_SQLITE=False
INVOICE_PDF_DIR=D:/ShopBilling/Bills
INVOICE_DOCX_DIR=D:/ShopBilling/Bills_Editable
```

### 4. Generate a secret key

```powershell
python -c "import secrets; print(secrets.token_urlsafe(64))"
```

Paste the output into `SECRET_KEY` in `.env`.

### 5. Create the database tables

```powershell
python manage.py migrate
```

### 6. Create the Word bill format

```powershell
python manage.py docx_template --create
```

Open `doc_templates\invoice_template.docx` in Word, restyle it to match the
shop's bill, keep the placeholders intact, then verify:

```powershell
python manage.py docx_template --check
```

### 7. Create the login

```powershell
python manage.py createsuperuser
```

Enter the shop's email address and a password. That is the login for the app
and for `/admin/`.

### 8. Fill in the shop's details

```powershell
python manage.py runserver
```

Open <http://127.0.0.1:8000/>, sign in, and go to **Shop** in the top menu.
Enter the shop name, address, phone, GSTIN and footer note — these print on
every bill.

### 9. Turn on the printer

Get the printer's exact name:

```powershell
Get-Printer | Select-Object Name
```

Then in `.env`:

```ini
PRINT_BACKEND=windows
PRINTER_NAME=<the exact name from above>       # blank = Windows default
PRINT_COPIES=1
SUMATRA_PDF_PATH=D:/ShopBilling/tools/SumatraPDF.exe
```

Restart the server, then go to **Printing → Send a test print**. If a page
comes out with no dialog, the setup is done.

### 10. Run it for real

```powershell
$env:DJANGO_SETTINGS_MODULE = "config.settings.prod"
python manage.py collectstatic --noinput
python -m waitress --listen=127.0.0.1:8000 config.wsgi:application
```

See [Running it in the shop](#running-it-in-the-shop) for starting this
automatically at login and for the backup routine.

---

## Quick start (your own machine, for testing)

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
copy .env.example .env
python manage.py migrate
python manage.py docx_template --create
python manage.py createsuperuser
python manage.py runserver
```

If port 8000 is already in use by another project, pass a different one:
`python manage.py runserver 8001`.

Optionally load 30 realistic bills to click around in — **development only,
never on the shop PC**:

```powershell
python manage.py seed_demo --bills 30      # login: owner@shop.test / billing123
python manage.py seed_demo --clear         # empty the book again (keeps logins)
```

---

## PostgreSQL (via pgAdmin)

1. Open **pgAdmin** → *Databases* → right-click → **Create → Database…**
2. Name it `shop_billing`, owner `postgres`, save.
3. Put the connection string in `.env`:

```ini
USE_SQLITE=False
DATABASE_URL=postgres://postgres:YOUR_PASSWORD@127.0.0.1:5432/shop_billing
```

4. `python manage.py migrate`

Set `USE_SQLITE=True` to fall back to a local file DB if Postgres is ever
unavailable — handy, but the real data lives in Postgres.

---

## The Word bill format

The bill layout is **your `.docx` file**, not hard-coded HTML. Design it in Word
exactly as it should come out of the printer and drop
[docxtpl](https://docxtpl.readthedocs.io/) placeholders where the data goes.

```ini
DOCX_TEMPLATE_PATH=D:/Personal_Fav_projects/Web_App/doc_templates/invoice_template.docx
```

### Getting started with the template

```powershell
python manage.py docx_template --create   # writes a working starter template
python manage.py docx_template --list     # prints every available placeholder
python manage.py docx_template --check    # renders it with sample data
```

Open the generated file in Word, restyle it however you like — fonts, borders,
logo, column widths, margins — and keep the placeholders intact.

### Placeholders

| Group | Placeholders |
|---|---|
| Shop | `shop_name` `shop_tagline` `shop_address` `shop_address_line1` `shop_address_line2` `shop_city` `shop_state` `shop_pincode` `shop_phone` `shop_alt_phone` `shop_email` `shop_gstin` `shop_footer_note` `shop_terms` |
| Bill | `invoice_number` `invoice_date` `invoice_date_long` `created_on` `created_at` `financial_year` |
| Customer | `customer_name` `customer_phone` `customer_email` `customer_city` `customer_address` |
| Totals | `subtotal` `total_discount` `grand_total` `grand_total_words` `item_count` `total_qty` |
| Payment | `payment_status_label` `paid_amount` `pending_amount` `overpaid_amount` `due_date` `notes` |
| Misc | `currency` `printed_on` `printed_by` |

Line items come as a list called `items`, each with
`sr` `description` `code` `qty` `unit` `rate` `gross` `discount`
`discount_label` `amount` `rate_block`.

### Repeating the item row

Put the loop tags in their **own table rows**, above and below the data row:

```
| {%tr for item in items %} |          |     |      |            |          |
| {{ item.sr }}             | {{ item.description }} | {{ item.qty }} | {{ item.rate }} | {{ item.discount_label }} | {{ item.amount }} |
| {%tr endfor %}            |          |     |      |            |          |
```

The two tag rows disappear when the bill is generated; the middle row repeats
once per item.

### Hiding things that are empty

* **Inside one paragraph** use plain Jinja: `{% if shop_email %}Email: {{ shop_email }}{% endif %}`
* **To remove a whole line**, put `{%p if has_pending %}` in its own paragraph,
  the content in the next, and `{%p endif %}` in a third. The two tag paragraphs
  vanish, and so does the content when the condition is false.

Useful flags: `has_discount` `has_pending` `has_overpaid` `has_due_date`
`has_email` `show_signature_line`, and per line `item.has_discount`.

### DOCX → PDF conversion

```ini
DOCX_TO_PDF_CONVERTER=auto      # auto | word | libreoffice
```

* `word` — drives installed Microsoft Word through COM. **Best fidelity**: what
  you see in Word is what prints. This is the recommended setup.
* `libreoffice` — `soffice --headless`, for machines without Word.
* `auto` — tries Word, then LibreOffice.

If neither is available (or the template is missing), the app automatically
falls back to a built-in HTML bill so **billing never stops**. The fallback is
logged loudly and shown on the Printing page.

---

## Where files are saved

Every path is configurable in `.env` — point them at a backed-up or synced
folder if you like:

```ini
INVOICE_PDF_DIR=D:/Shop/Bills                 # archived PDFs
INVOICE_DOCX_DIR=D:/Shop/Bills_Editable       # the filled .docx of each bill
DOCX_KEEP_INTERMEDIATE=True                   # keep the editable .docx
INVOICE_ARCHIVE_SUBFOLDER=date                # date | month | year | none
```

A PDF is archived on **every save**, not only when printing, so the folder is a
complete record. Filenames look like `INV-2026-27-0001_Ramesh_Patel.pdf`.

---

## The bill form

**Customer** — name and phone are required, email is optional, city defaults to
`Himmatnagar` (from `DEFAULT_CITY`) and is overwritten by anything typed. The
bill date defaults to today and cannot be set in the future. Typing a phone
number that has been billed before auto-fills the rest.

**Line items** — add as many rows as needed with **+ Add another item**, remove
any with the **−** button. Each row takes a description (required), quantity,
unit, rate (required, must be > 0) and an optional discount as either **%** or a
flat **Amount**.

A discount of `0` or blank means *no discount*, and nothing is printed for it.
On the bill, a discounted line shows the rate with the reduction beneath it and
the net amount alongside:

| Item | Qty | Rate | Discount | Amount |
|---|---|---|---|---|
| Cotton shirt | 2 pcs | 750.00 | −10% (−150.00) | 1,350.00 |
| Denim trousers | 1 pcs | 1,450.00 | −150.00 | 1,300.00 |
| Cotton socks | 3 pair | 120.00 | | 360.00 |

**Payment** — exactly one of four states, and the amounts must agree with it:

| State | Rule |
|---|---|
| Fully paid | Received is set to the bill total; no due date |
| Overpaid | Received must exceed the total; the excess is shown as *to return* |
| Partially paid | Received must be `> 0` and `< total`; **due date required** |
| Pending | Received is `0`; **due date required** |

Totals, discounts and the pending/overpaid amount update live as you type. The
same rules are enforced again on the server — the browser is only a convenience.

**Save** stores the bill and archives the PDF. **Save & Print** does that and
sends it to the printer.

---

## The listing page

Filters: free-text search, customer name, mobile number, email, city, payment
status (including *pending or underpaid* and *overdue*), bill-date range,
**created-on** date, and a min/max bill total. Quick buttons cover Today,
Yesterday, This month and Created today. Everything combines.

Running totals across the whole filtered set are shown above the table: bills,
billed, received and pending.

From each row you can download the PDF or reprint. The detail page adds an
editable copy, an HTML preview, the print history, and a **Record payment** box
that re-derives the payment state as money comes in.

---

## Tests

```powershell
# fast, in-memory SQLite
python manage.py test --settings=config.settings.test

# the same suite against real PostgreSQL - run this before a release
python manage.py test --settings=config.settings.test_postgres
```

266 tests covering the money maths (half-up rounding, discount caps, no
negative lines), invoice numbering across financial years, all four payment
states, every filter, the login throttle and the full forgot-password flow, the
Word template render, PDF generation, and every print backend.

The test settings force `PRINT_BACKEND=dummy`, so **the suite never touches a
printer or opens Word**.

---

## Running it in the shop

```powershell
$env:DJANGO_SETTINGS_MODULE = "config.settings.prod"
python manage.py collectstatic --noinput
python -m waitress --listen=127.0.0.1:8000 config.wsgi:application
```

`config/settings/prod.py` turns off `DEBUG`, requires a real `SECRET_KEY` and
`ALLOWED_HOSTS`, enables HSTS and secure cookies, and mails errors to `ADMIN_EMAIL`.

For plain HTTP on a single offline PC, keep these off in `.env`:

```ini
SECURE_SSL_REDIRECT=False
SESSION_COOKIE_SECURE=False
CSRF_COOKIE_SECURE=False
```

**Start it automatically at login:** Task Scheduler → Create Task → trigger
*At log on* → action `Start a program`:

* Program: `D:\Personal_Fav_projects\Web_App\.venv\Scripts\python.exe`
* Arguments: `-m waitress --listen=127.0.0.1:8000 config.wsgi:application`
* Start in: `D:\Personal_Fav_projects\Web_App`

Then put a browser shortcut to `http://127.0.0.1:8000/` on the desktop. Add
`--app=http://127.0.0.1:8000/ --kiosk-printing` to a Chrome shortcut for a
kiosk-style till.

**Back up two things:** the PostgreSQL database (pgAdmin → right-click the DB →
Backup…) and the `INVOICE_PDF_DIR` folder.

---

## Forgot password

In development the reset link is printed to the terminal — no SMTP needed. For
real email, set in `.env`:

```ini
EMAIL_BACKEND=django.core.mail.backends.smtp.EmailBackend
EMAIL_HOST=smtp.gmail.com
EMAIL_PORT=587
EMAIL_USE_TLS=True
EMAIL_HOST_USER=yourshop@gmail.com
EMAIL_HOST_PASSWORD=your-16-char-app-password
DEFAULT_FROM_EMAIL=yourshop@gmail.com
```

Gmail needs an **App Password** (Google Account → Security → 2-Step Verification
→ App passwords), not your normal password. Links expire after 2 hours.

The shop owner can also reset any password from the Django admin at `/admin/`.

---

## Layout

```
config/          settings (base / dev / prod / test / test_postgres), urls, wsgi
accounts/        email-based User, login with throttling, password reset, profile
billing/
  models.py      ShopProfile, Customer, Invoice, InvoiceLineItem,
                 InvoiceSequence, PrintJob
  forms.py       every validation rule (customer, line items, payment states)
  views.py       list + filters, create/edit, detail, PDF, print, payments
  utils.py       Indian digit grouping, amount in words
  services/
    documents.py   public API: build_pdf, archive_pdf, print_invoice
    context.py     the one dict handed to the Word template
    docx_engine.py Word template -> filled .docx -> PDF
    docx_convert.py  Word COM and LibreOffice converters
    html_engine.py   xhtml2pdf fallback and on-screen preview
    printing/        dummy, windows, cups, escpos, browser backends
  management/commands/
    docx_template.py  --create / --list / --check
    seed_demo.py      demo data
  tests/           models, forms, views, printing, utils, commands
templates/       base, accounts/*, billing/*, emails/*
static/          css/app.css, js/invoice_form.js   (no CDN - works offline)
doc_templates/   your Word bill format
```

### Design notes

* **Money is always `Decimal`, rounded half-up.** Python's default banker's
  rounding would make `2.675` into `2.67`; a shopkeeper expects `2.68`.
* **Totals are stored, not recomputed on read.** A bill already handed to a
  customer must never change because a formula was tweaked later.
* **Invoices keep a snapshot** of the customer's name, phone, email and city, so
  correcting a customer record never rewrites history on issued bills.
* **Bill numbers** are `INV/2026-27/0001`, allocated with
  `SELECT … FOR UPDATE` inside a transaction and restarting each Indian
  financial year (April–March).
* **Saving is one transaction.** A half-written bill cannot reach the database.
* **A failed print never loses the sale**: the bill is saved and archived first,
  the print attempt is recorded as a `PrintJob` either way.

---

## Troubleshooting

| Symptom | Where to look |
|---|---|
| Nothing prints | `/billing/settings/print/` → is `PRINT_BACKEND` still `dummy`? Then **Send a test print** |
| Bill looks like the plain HTML layout | The Word template is missing or Word failed — the Printing page names which |
| `Word could not convert …` | Close any stuck `WINWORD.EXE` in Task Manager, then retry |
| `Encountered unknown tag 'endif'` | A `{%p if %}` and its `{%p endif %}` are in the *same* paragraph — split them, or use inline `{% if %}` |
| Wrong printer | `PRINTER_NAME` must match Devices and Printers *exactly* |
| Cannot connect to the database | Check the pgAdmin service is running and `DATABASE_URL` matches |
| Locked out of login | Wait 15 minutes, or clear the cache / restart the server |
| Logs | `logs/app.log` |
