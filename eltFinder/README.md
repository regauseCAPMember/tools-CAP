# ELT Finder with Official CAP Grid

This folder is designed to be placed in a GitHub repository subfolder, for example:

```text
tools-CAP/elt-finder-cap-grid/
  index.html
  css/style.css
  js/app.js
```

Open `index.html` directly or publish the folder with GitHub Pages.

## CAP Grid source

The map uses the official Civil Air Patrol Search and Rescue SAR Grids ArcGIS FeatureServer:

- Grids layer: `/FeatureServer/0`
- Subgrids layer: `/FeatureServer/1`

The application queries the subgrid layer first. If no subgrid is found, it falls back to the main grid layer.
