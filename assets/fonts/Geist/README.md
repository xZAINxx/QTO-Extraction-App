# Geist Fonts

This directory holds the Geist Sans and Geist Mono variable font files used by the Zeconic QTO Tool UI.

The font binaries are NOT vendored in the repository to keep the working tree light. Download the latest release from <https://github.com/vercel/geist-font/releases>, extract it, and copy the variable `.ttf` files here so the layout looks like:

```
assets/fonts/Geist/
  Geist[wght].ttf          # variable Sans
  GeistMono[wght].ttf      # variable Mono
  README.md
  LICENSE.txt
```

The loader at `ui/theme/fonts.py` discovers any `.ttf` file present in this folder via `QFontDatabase.addApplicationFont`, so any naming variant Vercel ships (`Geist-Variable.ttf`, `GeistMono-Regular.ttf`, etc.) will be picked up automatically. If the files are missing, the loader logs a warning and the UI silently falls back to the platform's default sans/mono families — nothing crashes.

The bundled `LICENSE.txt` holds the SIL Open Font License 1.1 text under which Vercel ships Geist; keep it next to the font files when redistributing.
