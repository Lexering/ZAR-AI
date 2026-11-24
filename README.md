# ZAR — AI study buddy

ZAR is a voice AI friend for students (Spanish / English): tutor, notes, flashcards, exam planning,
documents & PDF, tasks, web, images and PC control, with an animated desktop (Lively Wallpaper).

> Keep this repository **private**. Keys are never stored in the repo.

## Make an installer for a student (one click for them)
1. GitHub → **Actions** → **Build ZAR installer** → **Run workflow**.
2. Fill in: student name, their Gemini API key, and (optional) an image key
   (a Gemini key with billing, or a Hugging Face token `hf_...`).
3. When it finishes (~5 min): **Releases** → download `ZAR-Setup-<name>.exe` and send it.

The student double-clicks it: it installs its own Python, the AI components, Lively Wallpaper,
LibreOffice, sets the animated wallpaper and starts ZAR hidden every time Windows starts. No cmd.

The keys are inside that .exe: send each one only to its student.

## Updates
`python tools/make_update.py` → `updates/ZAR_Update_vN.cmd` (double-click on the student's PC).
