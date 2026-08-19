# Start Doc2Audiobook on macOS or Windows

This is the everyday owner guide. Use the **local web app** unless you are
specifically testing a native desktop build. The local web app runs on your
computer and opens in your normal web browser.

The short version is:

1. Open a terminal in the `Doc2Audibook` project folder.
2. Run the setup command once (and again after a `git pull`).
3. Run the start command.
4. Open <http://127.0.0.1:8000>.
5. Leave the terminal open while using the app. Press **Control+C** in that
   terminal when you want to stop it.

## macOS

### First-time setup

1. Open **Terminal** (press Command+Space, type `Terminal`, then press Return).

2. Go to the project folder. An easy way is to type `cd ` (including the
   space), drag the `Doc2Audibook` folder from Finder into Terminal, and press
   Return. For example:

   ```bash
   cd /path/to/Doc2Audibook
   ```

3. Check the required tools:

   ```bash
   python3 --version
   node --version
   ffmpeg -version
   ```

   The project requires Python 3.10 or newer, Node.js 22.13 or newer for the
   main web interface, and FFmpeg. If you use Homebrew, this installs them:

   ```bash
   brew install python node ffmpeg
   ```

   If `brew` is not installed, install Homebrew from <https://brew.sh>, then run
   the command above. Close and reopen Terminal after installing tools.

4. Set up the project. This may take a while the first time because it installs
   dependencies and downloads offline voices:

   ```bash
   python3 bootstrap.py
   ```

   Wait for the command to finish. Warnings about a missing tool should be fixed
   before continuing.

### Start it each time

Open Terminal, go to the project folder as shown above, then run:

```bash
.venv/bin/python -m web_api
```

When Terminal shows that the server is running, open this address in Safari,
Chrome, or Firefox:

<http://127.0.0.1:8000>

Keep Terminal open. To stop Doc2Audiobook, return to Terminal and press
**Control+C**.

After pulling project updates, run `python3 bootstrap.py` once before starting
the app again.

## Windows

### First-time setup

1. Open **PowerShell** (open Start, type `PowerShell`, and press Enter).

2. Go to the project folder:

   ```powershell
   Set-Location 'D:\GitHub\Doc2Audibook'
   ```

   If your clone is somewhere else, replace that path with the actual project
   folder.

3. Check the required tools:

   ```powershell
   python --version
   node --version
   ffmpeg -version
   ```

   The project requires Python 3.10 or newer, Node.js 22.13 or newer for the
   main web interface, and FFmpeg. If Python responds with a Microsoft Store
   message or is not found, try `py --version`. These commands install the
   required tools with Windows Package Manager; it is safe to run only the
   command for a tool that is missing:

   ```powershell
   winget install --exact --id Python.Python.3.13
   winget install --exact --id OpenJS.NodeJS.LTS
   winget install --exact --id Gyan.FFmpeg
   ```

   Close and reopen PowerShell after installing tools, then run the three
   version checks again.

4. Set up the project. This may take a while the first time because it installs
   dependencies and downloads offline voices:

   ```powershell
   python bootstrap.py
   ```

   If `python` is not recognized but `py --version` worked, use this instead:

   ```powershell
   py bootstrap.py
   ```

   Wait for the command to finish. Warnings about a missing tool should be fixed
   before continuing.

### Start it each time

Open PowerShell, go to the project folder as shown above, then run:

```powershell
.\.venv\Scripts\python.exe -m web_api
```

When PowerShell shows that the server is running, open this address in Edge,
Chrome, or Firefox:

<http://127.0.0.1:8000>

Keep PowerShell open. To stop Doc2Audiobook, return to PowerShell and press
**Control+C**.

After pulling project updates, run `python bootstrap.py` once before starting
the app again.

## If port 8000 is already in use

Start the app on port 8010 instead.

On macOS:

```bash
.venv/bin/python -m web_api --port 8010
```

On Windows:

```powershell
.\.venv\Scripts\python.exe -m web_api --port 8010
```

Then open <http://127.0.0.1:8010>.

## If the main web interface does not appear

- If the terminal says the React frontend was not built, install or update
  Node.js, check that `node --version` is at least 22.13, and run the bootstrap
  command again.
- If the app reports that FFmpeg is missing, close the terminal, open a new
  one, check that `ffmpeg -version` works there, and start the app again.
- If the browser says it cannot connect, make sure the terminal running
  `web_api` is still open and has not printed an error.
- Do not activate the virtual environment manually. The commands above call the
  correct project Python directly and avoid PowerShell execution-policy issues.

## Backup interface: Gradio prototype

If Node.js is unavailable or the main interface is not built, the older Gradio
interface can still be used.

On macOS:

```bash
.venv/bin/python app.py
```

On Windows:

```powershell
.\.venv\Scripts\python.exe app.py
```

Open <http://127.0.0.1:7860>. If that port is busy, the terminal will print the
actual address (for example, port 7861).

## About the native desktop app

The native Tauri desktop package is still Phase 4A development work. The current
Windows artifacts are unsigned internal test builds, and macOS packaging has not
completed physical verification. For normal use on either computer, follow the
local web app instructions in this guide. You do **not** need Rust, Tauri, or the
desktop build command just to use Doc2Audiobook.
