"""CCO Swap launcher -- what `CCO Swap.cmd` actually runs.

This is Python rather than more `.cmd` because two of the three steps below
are things batch does badly: quoting a one-liner, and knowing whether a
server actually came up.

    1. APPDATA is redirected by the .cmd before we start, so every setting
       this copy reads and writes lives in `config/` beside this file. An
       unzipped copy therefore does NOT inherit an existing install's
       settings, which is the whole point of shipping it as a package.

    2. The name profile is applied if it is not already. Those tables are
       otherwise the product of a search measured at roughly half an hour.

    3. WE CLAIM THE PORT BEFORE OPENING THE BROWSER. An earlier version
       opened a fixed URL and then started the server. When another program
       already held that port the bind failed and the browser opened THE
       OTHER PROGRAM -- indistinguishable, from the user's side, from this
       one ignoring its own settings. It cost a real debugging session.
       Binding first and handing the socket's own port to the server means
       the URL we print is one we own.
"""
import os
import socket
import subprocess
import sys
import threading
import time
import urllib.request
import webbrowser
from pathlib import Path

HERE = Path(__file__).resolve().parent

# The console is read while waiting. Piped or redirected stdout is
# block-buffered, which puts the banner after the server's output -- or
# not until exit, which reads as the launcher doing nothing.
try:
    sys.stdout.reconfigure(line_buffering=True)
except Exception:
    pass


def apply_profile():
    zips = sorted((HERE / "profile").glob("*.zip"))
    if not zips:
        # NOT an error. The profile is a separate, optional download, and
        # calling a deliberate choice a warning trains people to ignore
        # warnings. Say what it costs instead.
        print("  Name profile: none installed (optional, separate download).")
        print("                Assets inside the .wdf archives cannot be named")
        print("                without it -- an archive stores a HASH of each")
        print("                filename, never the name itself.")
        print(r"                See profile\PUT-THE-PROFILE-HERE.txt")
        return
    r = subprocess.run(
        [sys.executable, str(HERE / "tools" / "profilepack.py"), "apply",
         str(zips[0]), "--yes", "--if-needed"],
        capture_output=True, text=True)
    out = (r.stdout or "") + (r.stderr or "")
    if r.returncode != 0:
        print("  [warning] the name profile did not apply. Archived assets may")
        print("            not resolve. Reason follows:")
        for line in out.strip().splitlines()[-6:]:
            print("            " + line)
    elif "already applied" in out:
        print("  Name profile: already applied.")
    else:
        print("  Name profile: applied and verified against your install.")


def free_port() -> int:
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


def open_when_up(url: str):
    """Open the browser only once the server answers -- so a server that dies
    on startup shows its error in this window instead of a browser error page
    that blames the wrong thing."""
    for _ in range(120):
        time.sleep(0.5)
        try:
            urllib.request.urlopen(url, timeout=1).read(1)
            webbrowser.open(url)
            return
        except Exception:
            continue
    print("  [warning] the server did not come up within 60s; not opening a browser.")


def main() -> int:
    print()
    print("  CCO Swap")
    print("  --------")
    cfg = os.environ.get("APPDATA", "")
    if not cfg.lower().startswith(str(HERE).lower()):
        print("  [warning] APPDATA is not pointed at this folder, so this copy will")
        print("            read and write the settings of any other installed copy.")
        print("            Launch with 'CCO Swap.cmd' rather than running this directly.")
    apply_profile()

    port = free_port()
    url = "http://127.0.0.1:%d/" % port
    print("  Opening %s in your browser." % url)
    print("  Leave this window open while you use it. Close it to stop.")
    print()
    threading.Thread(target=open_when_up, args=(url,), daemon=True).start()
    return subprocess.call(
        [sys.executable, str(HERE / "tools" / "coviewer.py"), "--port", str(port),
         # WE open the browser, once, after confirming the server answers.
         # coviewer opens one too on a 0.6s timer; without this the user gets
         # TWO tabs of the same page.
         "--no-browser"])


if __name__ == "__main__":
    raise SystemExit(main())
