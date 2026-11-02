---
name: sop-from-recording
description: Turn a Loom, screen recording or recorded call (Zoom, Google Meet, Fathom, Teams), of any length, into a step-by-step SOP, a Google Doc with a screenshot for every step, a red box on what to click and private info blurred, then file it in an Airtable SOP library. Use this whenever someone shares a Loom link, a recording or a call and wants it documented, says "turn this into an SOP", "write up this process", "make training docs for my employees / team", "document how I do this", or wants to delegate a task they recorded, even if they never say "SOP".
---

# SOP from a recording

They recorded themselves doing a task. You watch it and write the SOP their team follows from then on: every step in order, a screenshot per step with the thing to click boxed in red, filed in their SOP library.

Every command is `python3 SKILL_DIR/scripts/sop.py ...` (`python` on Windows), written `sop` below, where SKILL_DIR is this skill's folder. The first run installs what it needs. Work in `sop-work/<task-slug>/`.

## 1. Get the recording and transcript

Ask once, all together, only for what's missing:
- **The recording.** A file, or a link.
  - Link (Loom, YouTube, Vimeo): `sop fetch <link> --out sop-work/<slug>`. It downloads links set to "Anyone with the link", and for a Loom it also saves Loom's own transcript, so there's nothing to transcribe. If it can't reach the site, ask them to attach the file instead, or to use the Claude desktop app's Code tab. Free Loom plans can't download videos, so for them the link is the way in.
  - File too big to attach (claude.ai takes 30 MB, about 2-4 minutes of Loom): any length works in the Claude desktop app's Code tab or Claude Code, pointed at the folder with the recording. Tell them that.
- **The transcript, if they have one** (pasted text is fine): Loom, Transcript tab (copy it); Zoom, the .vtt beside the cloud recording; Fathom or Meet, copy the transcript. With none (and none from `fetch`), run `sop transcribe <video> --out sop-work/<slug>`. It downloads a speech model once, which claude.ai chat blocks; there, ask them to paste the transcript, or work from the screen alone and say so.
- **Owner and department** (Marketing, Sales, Operations...). Infer them if they don't say.

## 2. Find the steps

Read the whole transcript. In a long recording or call, find where the task is actually shown (the screen share, the "let me show you"), and work on just those stretches.

`sop sheet <video> --out sop-work/<slug>/sheets [--start S --end E]` makes contact sheets with one tile, labelled with its time, each time the screen changes (a page, menu or dialog opens), so it suits recordings of any length. Open the sheets for the stretches you're documenting. Use `--every 1 --start S --end E` where a lot happens fast. Over ~200 tiles: narrow it with `--start/--end`.

**Images are limited** (about 100 per conversation, after which older ones drop out), so spend them on the steps. Open each sheet once, don't reopen images, and on a long recording open only the sheets you need.

From the transcript and sheets, write each step's moment (just *before* the click, with the target on screen), the action in the app's own words, and the speaker's reasons, decisions and warnings. Several separate tasks in one recording: one SOP each. A long stretch of trial and error (editing a setting until it works) becomes one step showing the end state. If the speaker says the right order differs from what they did, follow what they say.

## 3. One screenshot per step

1. `sop shot <video> --at T --grid --out sop-work/<slug>/check.png`, then open it. Read the target's edges off the grid (x then y, percent of the whole frame from the top-left). For a small target (most links, menu items, checkboxes), use `--zoom x0,y0,x1,y1` around it instead of `--grid`: it enlarges that area with a 1% grid. Target not on screen yet? Try a second either side.
2. Draw it with the grid still on: `sop shot <video> --at T --box x0,y0,x1,y1 [--crop ...] --grid --out sop-work/<slug>/check.png`, then open it. The box must sit exactly on the thing to click, not clipping its text and not touching the next button. If it's off, fix the numbers and redraw. A box on the wrong button is worse than none.
   - `--crop x0,y0,x1,y1` cuts browser bars, the taskbar, empty space and webcam bubbles.
   - `--arrow` helps with a small target on a busy screen.
   - `--blur x0,y0,x1,y1` (repeatable) goes over anything private.
3. Once it's right, run the same command without `--grid`, with `--out sop-work/<slug>/shots/stepNN.png`. It's the same picture minus the grid, so there's no need to open it again.

- One box per step. Use two only for "copy this, paste it there". For "pick X, then Continue", box X and mention Continue in the text.
- **Keyboard-driven screens** (terminals, menus you arrow through): box the command or the highlighted row, put keys in bold (**Enter**, **Esc**), and write commands in `code` so they can be copied.
- **Text too small to read** in the doc (terminals, wide spreadsheets): write the exact value or command in the step's text; the screenshot shows where it goes.
- **Blur** API keys, passwords, tokens and card or bank numbers, even partly masked ones, plus anyone else's personal details (a customer, a resume, a colleague's email). The requester's own business name can stay.
- A step with nothing to point at (a decision, a wait, something off screen) gets no screenshot.

## 4. Write it and build the doc

Write `sop-work/<slug>/sop.json` (its shape is at the top of `scripts/sop.py`):
- **title**: the task as the team would search for it ("Add a new client to Stripe").
- **overview**: what it gets done, when, and how long it takes.
- **before_you_start**: logins, access, files.
- **steps**: one action each, in the imperative, with on-screen labels exactly as shown, in **bold**. The speaker's reasons, warnings and "if X then Y" go in the body or `note`. Use `section` to group long SOPs. Keep their own names for sheets, folders and clients.
- **tips**: mistakes they warn about, shortcuts they mention.
- **source** (the recording's link), **owner**, **department**, **updated** (today).

Write only what the recording shows.
- Transcripts mishear names: spell apps, buttons and settings the way the screen does. A name you can't match to anything on screen stays as said, with a **To confirm** note ("the transcript says 'AIP', which tool is this?"). Never swap in a guess (a tool, person or number they didn't show or say).
- Anything missing (where a login lives, who approves) becomes a note: `**To confirm:** ...`.
- Narration that doesn't match the screen (an old menu name): follow the screen, and note the difference.
- A recording that stops early: write the rest from what they say, without screenshots, each with a To confirm note.
- An edited video: order the steps the way the task is done.

`sop doc sop-work/<slug>/sop.json --out "sop-work/<slug>/SOP - <title>.docx"`. Then put the .docx where they can get it: in the Claude app, the outputs folder; in Claude Code, next to the recording.

## 5. Google Doc and Airtable

- **Google Doc:** they drag the .docx into drive.google.com, then right-click it and choose Open with -> Google Docs (screenshots and links come across). If you can write to their Google Drive folder on this computer, offer to save it there. Ask for the doc's link.
- **Airtable**, only if it's connected (Airtable tools are available):
  - Search their bases for "SOP" and use their table if there is one.
  - If there isn't, ask once before creating a base "SOP Library" with a table "SOPs": SOP (text), Department (single select), Owner (text), Summary (long text), Steps (number), Google Doc (URL), Recording (URL), Updated (date), Status (single select: Active, Needs review, Retired).
  - Add the row.

  If Airtable isn't connected, say they can connect it so the next SOP files itself.

## 6. Hand it over

Tell them in a few lines: the doc (and its link), how many steps and screenshots, where it's filed, every To confirm, and what you blurred. Don't paste the SOP into the chat.
