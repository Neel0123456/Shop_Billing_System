# Setup

Project-specific setup notes. Each section is independent — do only the ones
relevant to what you are running.

---

## Printing (SumatraPDF, Windows silent print)

### What SumatraPDF is and why this project uses it

[SumatraPDF](https://www.sumatrapdfreader.org/) is a tiny, free, open-source
PDF/ebook reader for Windows. This project does not use it for *reading* PDFs —
it uses it for its command-line **silent printing**.

The app generates invoice PDFs (from the Word bill template, converted by
Word or LibreOffice) and needs to send them to a physical printer. Windows has no reliable way to print a PDF from code without
opening a viewer and popping a print dialog. SumatraPDF solves that: give it a
PDF path plus a printer name and it prints straight to the Windows spooler —
no window, no dialog, no user click.

### Where it plugs in

| Location | Purpose |
| --- | --- |
| `config/settings/base.py` (`SUMATRA_PDF_PATH`) | Reads the exe path from the env |
| `.env` (`SUMATRA_PDF_PATH`) | Where you fill the path in |
| `billing/services/printing/` | The backend that will invoke it |

Only used when `PRINT_BACKEND=windows`. The other backends are:

- `dummy` — default; saves the PDF it *would* have printed to
  `media/print_spool/` and records the job, so the flow is testable with no
  printer attached
- `cups` — Linux/macOS `lp`
- `escpos` — thermal / receipt printers (`python-escpos`)
- `browser` — return the PDF and let the browser print dialog handle it

> **Status:** implemented. `billing/services/printing/` holds `base.py`
> (the backend interface) and `backends.py` (dummy / windows / cups / escpos /
> browser), with `__init__.py` as the registry. The `windows` backend uses
> SumatraPDF when `SUMATRA_PDF_PATH` is set, then falls back to Adobe Reader's
> `/t` switch, then to the Windows shell print verb via `pywin32`.
>
> Check the live configuration at `/billing/settings/print/`, which also has a
> **Send a test print** button. See `README.md` for the rest of the setup.

### Steps

**1. Download the portable exe**

From <https://www.sumatrapdfreader.org/download-free-pdf-viewer>, grab the
**64-bit portable** build. Portable = a single `.exe`, no installer, no
registry writes.

**2. Put it somewhere stable**

A project-local folder keeps the path predictable:

```powershell
mkdir d:\Personal_Fav_projects\Web_App\tools
# drop SumatraPDF-<version>-64.exe in there, rename it to SumatraPDF.exe
```

**3. Create your `.env`**

```powershell
copy .env.example .env
```

**4. Point the settings at it**

In `.env`:

```ini
PRINT_BACKEND=windows
PRINTER_NAME=                      # blank = Windows default printer
PRINT_COPIES=1
SUMATRA_PDF_PATH=d:\Personal_Fav_projects\Web_App\tools\SumatraPDF.exe
```

**5. Verify before wiring any code**

Print any PDF by hand:

```powershell
& "d:\Personal_Fav_projects\Web_App\tools\SumatraPDF.exe" -print-to-default -silent "C:\path\to\some.pdf"
```

If a page comes out with no dialog appearing, you are set.

To target a specific printer instead, get the exact name and use `-print-to`:

```powershell
Get-Printer | Select-Object Name
& "...\SumatraPDF.exe" -print-to "Exact Printer Name" -silent "C:\path\to\some.pdf"
```

That same exact string goes in `PRINTER_NAME`.

### Notes / gotchas

- `-silent` also suppresses **error** dialogs. During first setup, leave it off
  while debugging and add it back once printing works.
- `-exit-when-done` stops the process lingering after the job is spooled — use
  it in the backend code that shells out to the exe.
- The `windows` backend also needs `pywin32` (already in `requirements.txt`,
  installed only on `sys_platform == "win32"`).
- Use the **portable** build, not the installer. The installer version can be
  auto-updated or relocated out from under the hard-coded path in `.env`.
