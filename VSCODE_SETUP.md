# Run WebDoctor locally in Visual Studio Code

## Requirements

- Python 3.9 or newer (Python 3.11 is a good choice).
- Visual Studio Code with the Microsoft Python extension.
- Internet access for installing Python packages and Playwright Chromium.

Only test websites that you own or have permission to test. Start with `https://example.com` or your own development site.

## Windows (PowerShell)

1. Extract the ZIP file and open the extracted `webdoctor` folder in VS Code.
2. In VS Code, choose **Terminal → New Terminal**.
3. Run these commands:

```powershell
py -3.11 -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
pip install -e .
python -m playwright install chromium
webdoctor check https://example.com --no-ai
```

If PowerShell blocks activation, use Command Prompt in VS Code and run:

```bat
py -3.11 -m venv .venv
.venv\Scripts\activate.bat
python -m pip install --upgrade pip
pip install -e .
python -m playwright install chromium
webdoctor check https://example.com --no-ai
```

## macOS or Linux

Open the extracted folder in VS Code, create a terminal, then run:

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip
pip install -e .
python -m playwright install chromium
webdoctor check https://example.com --no-ai
```

The command writes reports to the `reports/` folder. These reports can contain private URLs or screenshots, so do not publish them without reviewing them.

## Open the dashboard

After installing, activate the virtual environment and run:

```bash
webdoctor serve --host 127.0.0.1 --port 8080
```

Then open `http://127.0.0.1:8080` in your browser. Stop the server with `Ctrl+C` in the terminal.

## AI diagnosis

The Manus built-in AI provider is available only inside Manus-managed environments. For a local VS Code installation, either use `--no-ai` (rule-based explanations; no API key needed) or configure a direct AI provider with your own API key. Never put API keys in source code or commit them to Git.

## Run WebDoctor's own tests

With the virtual environment active, run:

```bash
python tests/test_all.py
```
