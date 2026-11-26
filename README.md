# Movie Guide

A visual guide to the movies and shows in your folders: posters, IMDb / Rotten Tomatoes / Metacritic scores, cast and plot, plus libraries, playlists, pins and your own ratings.

## Download
Get **MovieGuide.exe** from the [latest release](https://github.com/AAmbrose123/MovieGuide/releases/latest) and run it. Nothing to install. Your settings and cache live in `%APPDATA%\MovieGuide`.

The app checks for new versions on startup and every few hours. When there is one, it shows a prompt in the bottom-right corner; **Update now** downloads it and restarts the app.

Optional files next to the exe:
- `matches.json`: hand-picked matches, `{"<file or folder path>": "tt1234567"}`
- `custom posters\tt1234567.jpg`: your own poster for a title

## Publishing an update
1. Make your changes to `movie_guide.py` / `guide_ui.html`.
2. Double-click **release.bat**, then enter a new version number and a one-line note.
3. GitHub Actions builds `MovieGuide.exe` and publishes it as a release (about 3–5 min).

You can also create a release on github.com (tag like `v1.2.0`), and the build attaches the exe to it.

## Running from source
`Movie Guide.bat` (needs Python; installs pywebview on first run). `build_exe.bat` builds the exe locally for testing.
