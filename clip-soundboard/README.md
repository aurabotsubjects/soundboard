# Clip Soundboard

Cut 1-10 second audio clips from YouTube videos, keep them in a local folder, and play them from a list or a 10-button soundboard.

## Setup (once)

1. Install **Python 3.9+** and **ffmpeg** (it must be on your PATH).
   - Windows: `winget install ffmpeg`
   - macOS: `brew install ffmpeg`
   - Linux: `sudo apt install ffmpeg`
2. In this folder, run:
   ```
   pip install -r requirements.txt
   ```

## Run

```
python app.py
```

Your browser opens at http://127.0.0.1:5000. Stop the app with Ctrl+C.

## Using it

- **Library tab**: paste a video link, set a start time (`83` or `1:23`) and a length (1-10 s), then Save. Click a name to rename it, press ▶ to play, `+ Board` to put it on the soundboard.
- **Trimming a sound**: press **✂ Edit** on any sound in the Library. Drag the two handles on the waveform (or click the waveform, use the arrow keys, or type exact milliseconds) to choose the part to keep, press **▶ Preview** to hear it, then **Save trim**. Everything outside the handles is cut off. The first time you trim a sound, the untouched original is kept as `<id>.orig.mp3` in the `clips` folder, and **Restore original** in the editor brings it back.
- **Evening out volume**: in the same ✂ Edit window, use the **Volume boost** slider (-20 to +24 dB) to make a quiet sound louder, and press **▶ Preview** to hear it (it updates live while playing). **Auto-match** measures the sound and picks a boost that brings it to a typical loudness. Press **Save**. The MP3 file itself is not changed; the boost is stored in `library.json` and applied when the sound plays, with a limiter so loud peaks never distort. Set it back to **0 dB** to undo. Boosted sounds show e.g. `1.73s · +16 dB` in the Library.
- **Soundboard tab**: shows only the 10 round buttons. Click one to play it, or press keys 1-9 and 0. Use **Edit buttons** to pick each button's sound and color. **Stop all** silences everything.

## Saving layouts

On the **Soundboard** tab, set up your 10 buttons with **Edit buttons**, then type a name in the box at the top and press **Save layout** (or Enter). Later, pick that name from the **Layout** dropdown and the buttons switch to it straight away. To change a saved layout, load it, tweak the buttons, and Save it again under the same name (you'll be asked to confirm replacing it). **Delete** removes the selected layout from the list; it never deletes your sounds or changes the buttons you currently have. If you delete a sound from the Library, that button is simply empty in any layout that used it.

## Where things are saved

Everything lives in the `clips` folder next to `app.py`: the MP3 files plus `library.json` (names, volume boosts, board layout, colors, saved layouts). Back that folder up to keep your sounds.

## If a download fails

YouTube changes often. Update the downloader with `pip install -U yt-dlp`.
