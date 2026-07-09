# ELT Finder - CAP Grid / Zulu / Edit Build

This folder contains the updated GitHub-ready ELT Finder project.

## Files

- `index.html`
- `css/style.css`
- `js/app.js`

## Changes in this build

- Removed the Wing Null workflow.
- Map-clicked points now look up the official CAP Grid at the clicked location and add it to Notes.
- Every saved point records Zulu date and Zulu time.
- The Recorded Points table displays the Zulu date/time for each saved point.
- Added an Edit button for recorded points.
  - Edit loads the point back into Point Entry.
  - The original row is removed.
  - The user can correct coordinates, bearing, or notes and add it back.
- Record GPS Coordinate automatically records a Circumcenter point.
- Record GPS Coordinate also populates current GPS fields and CAP Grid note.

## GitHub use

Copy this folder into the desired GitHub subfolder. Keep the relative structure intact:

```text
index.html
css/style.css
js/app.js
```

