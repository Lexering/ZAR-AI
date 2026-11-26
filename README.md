# ZAR — AI study buddy

ZAR is a voice AI friend for students (Spanish / English): tutor, notes, flashcards, exam planning,
documents & PDF, tasks, web, images and PC control, with an animated desktop (Lively Wallpaper).

> Keep this repository **private**. Keys are never stored in the repo.

## Windows only

Windows 11 "Smart App Control" blocks unsigned .exe files and scripts downloaded from the internet, so ZAR
installs with a script and the ZIP must be **unblocked before extracting** (Properties → Unblock).

### Install on this PC (straight from GitHub)
1. Code → Download ZIP.
2. Right-click the ZIP → **Properties** → tick **Unblock** → OK. Then right-click → **Extract all**.
3. Double-click **Instalar ZAR** → name → Gemini key. A small progress window does the rest (5-15 min).

### Make a package for a student (their key inside)
1. Double-click **Crear_instalador.bat** → student's name and keys → `dist\ZAR-<name>.zip`.
2. The student: unblock the ZIP (Properties → Unblock), extract it, double-click **Instalar ZAR**.

It installs its own Python, the AI components, Lively Wallpaper (winget / Microsoft Store), LibreOffice,
sets the animated wallpaper and starts ZAR hidden every time Windows starts.
The key is inside that ZIP: send each one only to its student.

## Updates
`python tools/make_update.py` → `updates/ZAR_Update_vN.cmd` (double-click on the student's PC).
