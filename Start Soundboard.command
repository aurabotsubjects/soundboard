#!/bin/bash
# Double-click launcher for Clip Soundboard (macOS / Linux)

cd "$(dirname "$0")" || exit 1
export PATH="/opt/homebrew/bin:/usr/local/bin:$PATH"

pause_exit() {
  echo
  read -n 1 -s -r -p "Press any key to close..."
  exit "${1:-1}"
}

# Find the app: next to this file, or inside the clip-soundboard folder
[ -f "clip-soundboard/app.py" ] && cd clip-soundboard
if [ ! -f "app.py" ]; then
  echo "Could not find app.py."
  echo "Put this file in the same folder as the unzipped clip-soundboard folder."
  pause_exit 1
fi

if ! command -v python3 >/dev/null 2>&1; then
  echo "Python 3 is not installed. Opening the download page."
  echo "Install it, then double-click this file again."
  open "https://www.python.org/downloads/" 2>/dev/null || xdg-open "https://www.python.org/downloads/" 2>/dev/null
  pause_exit 1
fi

# First run: create a private Python environment and install what the app needs
if [ ! -x ".venv/bin/python" ]; then
  echo "Setting up for the first time. This takes a minute or two..."
  python3 -m venv .venv \
    && .venv/bin/python -m pip install -q --upgrade pip \
    && .venv/bin/python -m pip install -r requirements.txt
  if [ $? -ne 0 ]; then
    rm -rf .venv
    echo "Setup failed. Check your internet connection and try again."
    pause_exit 1
  fi
fi

# Keep the YouTube downloader up to date, because YouTube changes often
.venv/bin/python -m pip install -q -U yt-dlp >/dev/null 2>&1

# ffmpeg is needed to cut the clips
if ! command -v ffmpeg >/dev/null 2>&1; then
  if command -v brew >/dev/null 2>&1; then
    echo "ffmpeg was not found. Installing it with Homebrew..."
    brew install ffmpeg || { echo "Could not install ffmpeg."; pause_exit 1; }
  else
    echo "ffmpeg was not found, and Homebrew is not installed."
    echo "Install Homebrew from https://brew.sh, then double-click this file again."
    open "https://brew.sh" 2>/dev/null
    pause_exit 1
  fi
fi

echo
echo "Starting Clip Soundboard. Your browser will open in a moment."
echo "Keep this window open while you use it. Close it to stop the app."
echo
.venv/bin/python app.py
echo
echo "Clip Soundboard has stopped."
pause_exit 0
