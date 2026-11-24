# Pokémon FRLG+ Interactive Map

A MapGenie-style interactive web map generated directly from the [Deokishisu/FRLG-Plus](https://github.com/Deokishisu/FRLG-Plus) decompilation repository.

## Features
- **25 pixel-accurate map layers** rendered directly from FRLG-Plus tilesets & layouts:
  - **Kanto Overworld** (all 37 connected cities, towns, and routes including Saffron City)
  - **Sevii & Bonus Islands (FRLG+)** (Islands 1–7, Mt. Ember Exterior, Berry Forest, Pattern Bush, Four Island Base, Birth Island, Navel Rock Exterior, Faraway Island, Southern Island, Battle Frontier, Route 20 Underwater, Bond Bridge Underwater)
  - **23 Multi-Floor Dungeon Layers** (Viridian Forest, Mt. Moon, S.S. Anne, Diglett's Cave, Rock Tunnel, Pokémon Tower, Rocket Hideout, Silph Co., Safari Zone + FRLG+ NorthWest/NorthEast zones, Power Plant, Seafoam Islands, Pokémon Mansion, Victory Road, Cerulean Cave, Mt. Ember, Icefall Cave, Rocket Warehouse, Lost Cave, Dotted Hole, Tanoby Ruins, Altering Cave & Dunsparce Tunnel, Navel Rock, Artisan Cave)
- **1,063 interactive markers**: Visible items, TMs/HMs (with move names), hidden items, trainers & Gym Leaders (with full teams, levels, held items, and moves), FRLG+ Master Trainers, Move Tutors, Poké Centers, Poké Marts, static Pokémon, and clickable dungeon entrance warps.
- **136 Wild Pokémon Encounter Tables** with live **FireRed / LeafGreen** toggle (Grass/Cave, Surfing, Rock Smash, Old/Good/Super Rod).
- **Global Search** across all markers, areas, and wild Pokémon species.
- **Progress Checklist** saved to `localStorage` with "Hide found", JSON export/import, and reset.

## Running Locally
Either double-click `index.html` directly in your browser, or serve it locally:
```bash
python3 -m http.server 8080
```
Then open `http://localhost:8080`.

## Deploying to GitHub Pages
Push this folder to a GitHub repository (`.repo/` is already gitignored) and enable **Settings → Pages → Deploy from branch (`main` / root)**.

## Regenerating Data after ROM Hack Updates
```bash
git -C .repo/FRLG-Plus pull
python3 scripts/build_map_data.py
```
