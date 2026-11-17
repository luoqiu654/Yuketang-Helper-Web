"""
Entry point for the PyInstaller-packaged Yuketang Helper application.
Starts the FastAPI server and opens the browser automatically.
"""

import os
import sys
import webbrowser
from pathlib import Path

# When frozen, the backend modules are compiled into the executable (the spec
# puts backend/ on PyInstaller's search path); only the SSL certificate path
# has to be set so that outbound HTTPS/WSS connections work.
if getattr(sys, "frozen", False):
    import certifi
    os.environ["SSL_CERT_FILE"] = certifi.where()
    os.environ["REQUESTS_CA_BUNDLE"] = certifi.where()
else:
    sys.path.insert(0, str(Path(__file__).resolve().parent / "backend"))

# V0.2 packaged builds use a separate data directory from the original EXE.
if getattr(sys, "frozen", False):
    from platformdirs import user_data_dir
    os.environ.setdefault("YUKETANG_STORE_DIR", str(Path(user_data_dir("Yuketang Evan V0.2", appauthor=False))))

import uvicorn
from main import app  # noqa: E402

# 127.0.0.1 by default so the single-binary distribution doesn't expose the
# helper to the local network. Set HOST=0.0.0.0 to bind all interfaces.
HOST = os.environ.get("HOST", "127.0.0.1")
PORT = int(os.environ.get("PORT", "8501"))

if __name__ == "__main__":
    if os.environ.get("YUKETANG_NO_BROWSER") != "1":
        webbrowser.open(f"http://localhost:{PORT}")
    uvicorn.run(app, host=HOST, port=PORT, log_level="info", use_colors=False)
