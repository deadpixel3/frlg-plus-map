#!/usr/bin/env python3
"""
FRLG-Plus Interactive Map Generator
Extracts tilesets, stitched overworld & dungeon maps, items, hidden items,
trainers, Master Trainers, Move Tutors, key NPCs, warps, and wild encounters
directly from the Deokishisu/FRLG-Plus decompilation repository.

Zero external dependencies (uses only Python 3 standard library).
"""

import glob
import json
import os
import re
import struct
import sys
import time
import zlib
from collections import deque
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
REPO = ROOT / ".repo" / "FRLG-Plus"
OUT_MAPS = ROOT / "assets" / "maps"
OUT_DATA = ROOT / "assets" / "data"

NUM_TILES_IN_PRIMARY = 640
NUM_METATILES_IN_PRIMARY = 640
NUM_PALS_IN_PRIMARY = 7

# ---------------------------------------------------------------------------
# Pure-Python PNG Reader (for indexed 4bpp/8bpp tiles.png) & RGB PNG Writer
# ---------------------------------------------------------------------------

def paeth_predictor(a, b, c):
    p = a + b - c
    pa = abs(p - a)
    pb = abs(p - b)
    pc = abs(p - c)
    if pa <= pb and pa <= pc:
        return a
    elif pb <= pc:
        return b
    return c


def read_indexed_png(path):
    """Read an indexed-color (color_type=3) 4-bit or 8-bit PNG into (w, h, pixels_bytes).
    Returns palette indices (0..15 or 0..255) as a flat bytearray of length w*h.
    """
    with open(path, "rb") as f:
        data = f.read()
    assert data[:8] == b"\x89PNG\r\n\x1a\n", f"Not a PNG: {path}"
    pos = 8
    w = h = bd = ct = 0
    idat = bytearray()
    while pos < len(data):
        ln, typ = struct.unpack(">I4s", data[pos:pos + 8])
        chunk = data[pos + 8:pos + 8 + ln]
        pos += 12 + ln
        if typ == b"IHDR":
            w, h, bd, ct, cm, fm, im = struct.unpack(">IIBBBBB", chunk)
            assert im == 0, f"Interlaced PNG unsupported: {path}"
        elif typ == b"IDAT":
            idat.extend(chunk)
        elif typ == b"IEND":
            break

    raw = zlib.decompress(bytes(idat))
    if ct == 3:
        stride = (w * bd + 7) // 8
        bpp = 1
    elif ct == 0:
        stride = (w * bd + 7) // 8
        bpp = 1
    elif ct == 2:
        stride = w * 3
        bpp = 3
    elif ct == 6:
        stride = w * 4
        bpp = 4
    else:
        raise ValueError(f"Unsupported PNG color_type={ct} in {path}")

    recon = bytearray(h * stride)
    prev_row = bytearray(stride)
    in_pos = 0
    for y in range(h):
        ftype = raw[in_pos]
        in_pos += 1
        row = bytearray(raw[in_pos:in_pos + stride])
        in_pos += stride
        if ftype == 1:  # Sub
            for i in range(bpp, stride):
                row[i] = (row[i] + row[i - bpp]) & 0xFF
        elif ftype == 2:  # Up
            for i in range(stride):
                row[i] = (row[i] + prev_row[i]) & 0xFF
        elif ftype == 3:  # Average
            for i in range(stride):
                a = row[i - bpp] if i >= bpp else 0
                row[i] = (row[i] + ((a + prev_row[i]) >> 1)) & 0xFF
        elif ftype == 4:  # Paeth
            for i in range(stride):
                a = row[i - bpp] if i >= bpp else 0
                b = prev_row[i]
                c = prev_row[i - bpp] if i >= bpp else 0
                row[i] = (row[i] + paeth_predictor(a, b, c)) & 0xFF
        recon[y * stride:(y + 1) * stride] = row
        prev_row = row

    if ct in (3, 0) and bd == 4:
        pixels = bytearray(w * h)
        out_i = 0
        for y in range(h):
            row_start = y * stride
            for x in range(0, w, 2):
                byte = recon[row_start + (x >> 1)]
                pixels[out_i] = (byte >> 4) & 0xF
                out_i += 1
                if x + 1 < w:
                    pixels[out_i] = byte & 0xF
                    out_i += 1
        return w, h, pixels
    elif ct in (3, 0) and bd == 8:
        return w, h, recon
    else:
        # Fallback: convert grayscale/RGB to index 0..15 by luminance
        pixels = bytearray(w * h)
        for i in range(w * h):
            pixels[i] = recon[i * bpp] >> 4
        return w, h, pixels


def write_rgb_png(path, w, h, rgb_buf):
    """Write a 24-bit RGB PNG from a bytearray of length w * h * 3."""
    def make_chunk(typ, data):
        crc = zlib.crc32(typ)
        crc = zlib.crc32(data, crc) & 0xFFFFFFFF
        return struct.pack(">I", len(data)) + typ + data + struct.pack(">I", crc)

    stride = w * 3
    # Build filtered scanlines (filter type 0 = None)
    raw = bytearray(h * (stride + 1))
    mv_in = memoryview(rgb_buf)
    mv_out = memoryview(raw)
    for y in range(h):
        dst = y * (stride + 1)
        raw[dst] = 0
        mv_out[dst + 1:dst + 1 + stride] = mv_in[y * stride:(y + 1) * stride]

    comp = zlib.compress(bytes(raw), level=6)
    ihdr = struct.pack(">IIBBBBB", w, h, 8, 2, 0, 0, 0)
    with open(path, "wb") as f:
        f.write(b"\x89PNG\r\n\x1a\n")
        f.write(make_chunk(b"IHDR", ihdr))
        f.write(make_chunk(b"IDAT", comp))
        f.write(make_chunk(b"IEND", b""))


# ---------------------------------------------------------------------------
# Tileset Loader & Fast Metatile Renderer
# ---------------------------------------------------------------------------

def parse_tileset_definitions():
    """Parse src/data/tilesets/{headers,graphics,metatiles}.h and src/graphics.c to map gTileset_* -> paths."""
    headers_txt = (REPO / "src/data/tilesets/headers.h").read_text(errors="ignore")
    graphics_txt = (
        (REPO / "src/data/tilesets/graphics.h").read_text(errors="ignore")
        + "\n"
        + (REPO / "src/graphics.c").read_text(errors="ignore")
    )
    metatiles_txt = (REPO / "src/data/tilesets/metatiles.h").read_text(errors="ignore")

    # Map gTilesetTiles_* -> tiles.png path
    tiles_paths = {}
    for m in re.finditer(r'(gTilesetTiles_\w+)\[\]\s*=\s*INCBIN_\w+\("([^"]+)"\)', graphics_txt):
        sym, p = m.group(1), m.group(2)
        p = re.sub(r'\.4bpp(\.lz)?$', '.png', p)
        tiles_paths[sym] = REPO / p

    # Map gTilesetPalettes_* -> list of .pal paths
    pal_paths = {}
    for m in re.finditer(r'(gTilesetPalettes_\w+)\[\]\[16\]\s*=\s*\{([^}]+)\}', graphics_txt, re.S):
        sym, body = m.group(1), m.group(2)
        pals = [REPO / re.sub(r'\.gbapal$', '.pal', p) for p in re.findall(r'INCBIN_U16\("([^"]+)"\)', body)]
        pal_paths[sym] = pals

    # Map gMetatiles_* -> metatiles.bin path
    mt_paths = {}
    for m in re.finditer(r'(gMetatiles_\w+)\[\]\s*=\s*INCBIN_U16\("([^"]+)"\)', metatiles_txt):
        mt_paths[m.group(1)] = REPO / m.group(2)

    tilesets = {}
    for m in re.finditer(r'const struct Tileset (gTileset_\w+)\s*=\s*\{([^}]+)\};', headers_txt, re.S):
        name, body = m.group(1), m.group(2)
        t_sym = re.search(r'\.tiles\s*=\s*(\w+)', body)
        p_sym = re.search(r'\.palettes\s*=\s*(\w+)', body)
        m_sym = re.search(r'\.metatiles\s*=\s*(\w+)', body)
        tilesets[name] = {
            "tiles": tiles_paths.get(t_sym.group(1)) if t_sym else None,
            "palettes": pal_paths.get(p_sym.group(1), []) if p_sym else [],
            "metatiles": mt_paths.get(m_sym.group(1)) if m_sym else None,
        }
    return tilesets


def load_jasc_pal(path):
    if not path or not path.exists():
        return [(0, 0, 0)] * 16
    lines = path.read_text(errors="ignore").splitlines()
    colors = []
    for line in lines[3:]:
        parts = line.strip().split()
        if len(parts) == 3:
            colors.append((int(parts[0]), int(parts[1]), int(parts[2])))
    while len(colors) < 16:
        colors.append((0, 0, 0))
    return colors[:16]


class TilesetCache:
    def __init__(self, defs):
        self.defs = defs
        self._tiles = {}     # ts_name -> list of 64-byte tiles (8x8 indices)
        self._pals = {}      # ts_name -> list of 16 palettes
        self._metatiles = {} # ts_name -> tuple of uint16
        self._pair_cache = {}  # (prim, sec) -> dict(mt_id -> list of 16 48-byte RGB rows)

    def get_tiles(self, ts_name):
        if ts_name not in self._tiles:
            info = self.defs.get(ts_name)
            if not info or not info["tiles"] or not info["tiles"].exists():
                self._tiles[ts_name] = []
                return []
            w, h, px = read_indexed_png(info["tiles"])
            tiles = []
            tx_count = w // 8
            ty_count = h // 8
            for ty in range(ty_count):
                for tx in range(tx_count):
                    tile = bytearray(64)
                    for row in range(8):
                        src = (ty * 8 + row) * w + tx * 8
                        tile[row * 8:(row + 1) * 8] = px[src:src + 8]
                    tiles.append(bytes(tile))
            self._tiles[ts_name] = tiles
        return self._tiles[ts_name]

    def get_pals(self, ts_name):
        if ts_name not in self._pals:
            info = self.defs.get(ts_name)
            if not info:
                self._pals[ts_name] = [[(0, 0, 0)] * 16 for _ in range(16)]
            else:
                pals = [load_jasc_pal(p) for p in info["palettes"]]
                while len(pals) < 16:
                    pals.append([(0, 0, 0)] * 16)
                self._pals[ts_name] = pals
        return self._pals[ts_name]

    def get_metatiles(self, ts_name):
        if ts_name not in self._metatiles:
            info = self.defs.get(ts_name)
            if not info or not info["metatiles"] or not info["metatiles"].exists():
                self._metatiles[ts_name] = ()
            else:
                raw = info["metatiles"].read_bytes()
                self._metatiles[ts_name] = struct.unpack(f"<{len(raw)//2}H", raw)
        return self._metatiles[ts_name]

    def render_metatile(self, prim_name, sec_name, mt_id):
        key = (prim_name, sec_name)
        cache = self._pair_cache.get(key)
        if cache is None:
            cache = {}
            self._pair_cache[key] = cache
        rows = cache.get(mt_id)
        if rows is not None:
            return rows

        p_tiles = self.get_tiles(prim_name)
        s_tiles = self.get_tiles(sec_name)
        p_pals = self.get_pals(prim_name)
        s_pals = self.get_pals(sec_name)

        if mt_id < NUM_METATILES_IN_PRIMARY:
            mts = self.get_metatiles(prim_name)
            base = mt_id * 8
        else:
            mts = self.get_metatiles(sec_name)
            base = (mt_id - NUM_METATILES_IN_PRIMARY) * 8

        if base + 8 > len(mts):
            rows = [bytes(48)] * 16
            cache[mt_id] = rows
            return rows

        buf = bytearray(16 * 16 * 3)
        # Layer 0 = bottom (opaque), Layer 1 = top (index 0 transparent)
        for layer in range(2):
            for sub in range(4):
                entry = mts[base + layer * 4 + sub]
                tile_id = entry & 0x03FF
                xflip = (entry >> 10) & 1
                yflip = (entry >> 11) & 1
                pal_idx = (entry >> 12) & 0xF

                if tile_id < NUM_TILES_IN_PRIMARY:
                    tile = p_tiles[tile_id] if tile_id < len(p_tiles) else None
                else:
                    st = tile_id - NUM_TILES_IN_PRIMARY
                    tile = s_tiles[st] if st < len(s_tiles) else None

                if tile is None:
                    continue

                if pal_idx < NUM_PALS_IN_PRIMARY:
                    pal = p_pals[pal_idx]
                else:
                    pal = s_pals[pal_idx] if pal_idx < len(s_pals) else p_pals[0]

                ox = (sub & 1) * 8
                oy = (sub >> 1) * 8
                for py in range(8):
                    sy = (7 - py) if yflip else py
                    row_off = sy * 8
                    dst_row = (oy + py) * 48 + ox * 3
                    for px in range(8):
                        sx = (7 - px) if xflip else px
                        cidx = tile[row_off + sx]
                        if layer == 1 and cidx == 0:
                            continue
                        r, g, b = pal[cidx]
                        d = dst_row + px * 3
                        buf[d] = r
                        buf[d + 1] = g
                        buf[d + 2] = b

        rows = [bytes(buf[y * 48:(y + 1) * 48]) for y in range(16)]
        cache[mt_id] = rows
        return rows


def blit_map_into_canvas(canvas, canvas_w_px, offset_x_mt, offset_y_mt, layout, ts_cache):
    """Render a single map layout into an RGB bytearray canvas at metatile offset."""
    w = layout["width"]
    h = layout["height"]
    prim = layout["primary_tileset"]
    sec = layout["secondary_tileset"]
    map_bin = REPO / layout["blockdata_filepath"]
    if not map_bin.exists():
        return
    raw = map_bin.read_bytes()
    blocks = struct.unpack(f"<{len(raw)//2}H", raw)
    stride = canvas_w_px * 3
    mv = memoryview(canvas)

    for my in range(h):
        cy_px = (offset_y_mt + my) * 16
        row_idx = my * w
        for mx in range(w):
            if row_idx + mx >= len(blocks):
                break
            mt_id = blocks[row_idx + mx] & 0x03FF
            rows = ts_cache.render_metatile(prim, sec, mt_id)
            cx_px = (offset_x_mt + mx) * 16
            base_off = cy_px * stride + cx_px * 3
            for r in range(16):
                off = base_off + r * stride
                mv[off:off + 48] = rows[r]


# ---------------------------------------------------------------------------
# Game Data Parsers (Items, Species, Moves, Trainers, Master Trainers, Encounters)
# ---------------------------------------------------------------------------

def title_from_const(s, prefix=""):
    if prefix and s.startswith(prefix):
        s = s[len(prefix):]
    words = s.replace("_", " ").lower().split()
    out = []
    for w in words:
        if w in ("tm", "hm", "hp", "pp", "ss", "vs", "iv", "ev", "id", "fr", "lg"):
            out.append(w.upper())
        elif re.match(r"^(tm|hm)\d+$", w):
            out.append(w.upper())
        elif re.match(r"^b?\d+f$", w):
            out.append(w.upper())
        else:
            out.append(w.capitalize())
    return " ".join(out)


def parse_items():
    data = json.load(open(REPO / "src/data/items.json"))
    items = {}
    for it in data.get("items", []):
        iid = it["itemId"]
        eng = it.get("english", "")
        # Clean pokefirered caps like "RARE CANDY" -> "Rare Candy", keep TM01 etc.
        if eng.startswith("TM") or eng.startswith("HM"):
            name = eng
        else:
            name = " ".join(w.capitalize() if not w.startswith("POK") else "Poké" + w[4:].capitalize() for w in eng.split())
        desc = it.get("description_english", "").replace("\\n", " ").replace("POKéMON", "Pokémon").replace("POKé", "Poké")
        items[iid] = {
            "name": name or title_from_const(iid, "ITEM_"),
            "description": desc,
            "pocket": it.get("pocket", ""),
            "price": it.get("price", 0),
        }

    # Parse TM/HM move names from src/data/pokemon/tmhm_learnsets.h or include/constants/items.h or src/item_menu.c
    tm_moves = {}
    tm_file = REPO / "src/data/pokemon/tmhm_learnsets.h"
    if tm_file.exists():
        txt = tm_file.read_text(errors="ignore")
        for m in re.finditer(r'\[ITEM_(TM\d+|HM\d+)\s*-\s*ITEM_TM01\]\s*=\s*(MOVE_\w+)', txt):
            tm_moves["ITEM_" + m.group(1)] = title_from_const(m.group(2), "MOVE_")
    # Also check src/party_menu.c or src/item.c for sTMHMMoves
    if not tm_moves:
        for p in [REPO / "src/party_menu.c", REPO / "src/item.c", REPO / "src/data/party_menu.h"]:
            if p.exists():
                txt = p.read_text(errors="ignore")
                m = re.search(r'sTMHMMoves\[\]\s*=\s*\{([^}]+)\}', txt, re.S)
                if m:
                    moves = re.findall(r'MOVE_\w+', m.group(1))
                    for i, mv in enumerate(moves):
                        key = f"ITEM_TM{i+1:02d}" if i < 50 else f"ITEM_HM{i-49:02d}"
                        tm_moves[key] = title_from_const(mv, "MOVE_")
                    break

    for iid, mv in tm_moves.items():
        if iid in items:
            items[iid]["name"] = f"{items[iid]['name']} ({mv})"
            items[iid]["move"] = mv
    return items


def parse_species_types():
    """Parse src/data/pokemon/species_info.h -> { 'Pidgeot': ['Normal', 'Flying'], ... }."""
    path = REPO / "src/data/pokemon/species_info.h"
    if not path.exists():
        return {}
    txt = path.read_text(errors="ignore")
    species_types = {}
    for m in re.finditer(r'\[(SPECIES_\w+)\]\s*=\s*\{(.*?)\n    \},', txt, re.S):
        sp_const, body = m.group(1), m.group(2)
        if sp_const in ("SPECIES_NONE", "SPECIES_EGG"):
            continue
        tm = re.search(r'\.types\s*=\s*\{\s*(TYPE_\w+)\s*,\s*(TYPE_\w+)\s*\}', body)
        if not tm:
            continue
        t1 = title_from_const(tm.group(1), "TYPE_")
        t2 = title_from_const(tm.group(2), "TYPE_")
        types = [t1] if t1 == t2 else [t1, t2]
        species_types[title_from_const(sp_const, "SPECIES_")] = types
    return species_types


def parse_party_file(path):
    """Parse trainer_parties.h or master_trainer_parties.h into dict(party_sym -> list of mon dicts)."""
    txt = path.read_text(errors="ignore")
    parties = {}
    # Match: static const struct TrainerMon... sParty_Name[] = { ... };
    pattern = re.compile(r'static const struct\s+\w+\s+(sParty_\w+)\[\]\s*=\s*\{(.*?)\n\};', re.S)
    for m in pattern.finditer(txt):
        sym, body = m.group(1), m.group(2)
        mons = []
        for bm in re.finditer(r'\{([^{}]*(?:\{[^{}]*\}[^{}]*)*)\}', body):
            block = bm.group(1)
            sp = re.search(r'\.species\s*=\s*(SPECIES_\w+)', block)
            lv = re.search(r'\.lvl\s*=\s*(\d+)', block)
            if not sp or not lv:
                continue
            item_m = re.search(r'\.heldItem\s*=\s*(ITEM_\w+)', block)
            moves_m = re.search(r'\.moves\s*=\s*\{([^}]+)\}', block)
            mon = {
                "species": title_from_const(sp.group(1), "SPECIES_"),
                "level": int(lv.group(1)),
            }
            if item_m and item_m.group(1) != "ITEM_NONE":
                mon["item"] = title_from_const(item_m.group(1), "ITEM_")
            if moves_m:
                mvs = [title_from_const(x, "MOVE_") for x in re.findall(r'MOVE_\w+', moves_m.group(1)) if x != "MOVE_NONE"]
                if mvs:
                    mon["moves"] = mvs
            mons.append(mon)
        parties[sym] = mons
    return parties


def parse_trainers_file(path, parties):
    txt = path.read_text(errors="ignore")
    trainers = {}
    pattern = re.compile(r'\[((?:TRAINER|MASTER)_\w+)\]\s*=\s*\{(.*?)\n    \},', re.S)
    for m in pattern.finditer(txt):
        tid, body = m.group(1), m.group(2)
        tclass_m = re.search(r'\.trainerClass\s*=\s*(TRAINER_CLASS_\w+)', body)
        tname_m = re.search(r'\.trainerName\s*=\s*_\("([^"]*)"\)', body)
        party_m = re.search(r'\.party\s*=\s*[^\(]*\((sParty_\w+)\)', body)
        if not party_m:
            party_m = re.search(r'\.party\s*=\s*\{\s*\.\w+\s*=\s*(sParty_\w+)\s*\}', body)
        dbl_m = re.search(r'\.doubleBattle\s*=\s*(TRUE|FALSE)', body)
        tclass = title_from_const(tclass_m.group(1), "TRAINER_CLASS_") if tclass_m else "Trainer"
        tclass = tclass.replace("Pkmn", "Pokémon")
        tname = tname_m.group(1).title() if tname_m else ""
        party_sym = party_m.group(1) if party_m else None
        trainers[tid] = {
            "id": tid,
            "class": tclass,
            "name": tname,
            "double": bool(dbl_m and dbl_m.group(1) == "TRUE"),
            "party": parties.get(party_sym, []) if party_sym else [],
        }
    return trainers


def parse_wild_encounters():
    """Parse src/data/wild_encounters.h for both FireRed & LeafGreen tables per MAP_ID."""
    txt = (REPO / "src/data/wild_encounters.h").read_text(errors="ignore")

    # Slot rates
    LAND_RATES = [20, 20, 10, 10, 10, 10, 5, 5, 4, 4, 1, 1]
    WATER_RATES = [60, 30, 5, 4, 1]
    ROCK_RATES = [60, 30, 5, 4, 1]
    OLD_ROD_RATES = [70, 30]
    GOOD_ROD_RATES = [60, 20, 20]
    SUPER_ROD_RATES = [40, 40, 15, 4, 1]

    # 1. Parse all WildPokemon arrays
    arrays = {}
    for m in re.finditer(r'const struct WildPokemon\s+(s\w+)\[\]\s*=\s*\{(.*?)\};', txt, re.S):
        sym, body = m.group(1), m.group(2)
        slots = []
        for sm in re.finditer(r'\{\s*(\d+)\s*,\s*(\d+)\s*,\s*(SPECIES_\w+)\s*\}', body):
            slots.append((int(sm.group(1)), int(sm.group(2)), title_from_const(sm.group(3), "SPECIES_")))
        arrays[sym] = slots

    # 2. Parse WildPokemonInfo structs -> (rate, array_sym)
    infos = {}
    for m in re.finditer(r'const struct WildPokemonInfo\s+(s\w+)\s*=\s*\{\s*(\d+)\s*,\s*(s\w+)\s*\};', txt):
        infos[m.group(1)] = (int(m.group(2)), m.group(3))

    def summarize(slots, rates):
        agg = {}
        for i, (mn, mx, sp) in enumerate(slots):
            if i >= len(rates):
                break
            r = rates[i]
            if sp not in agg:
                agg[sp] = {"species": sp, "min": mn, "max": mx, "rate": r}
            else:
                agg[sp]["min"] = min(agg[sp]["min"], mn)
                agg[sp]["max"] = max(agg[sp]["max"], mx)
                agg[sp]["rate"] += r
        return sorted(agg.values(), key=lambda x: (-x["rate"], x["species"]))

    def extract_table(info_sym, kind):
        if not info_sym or info_sym == "NULL" or info_sym not in infos:
            return None
        _, arr_sym = infos[info_sym]
        slots = arrays.get(arr_sym, [])
        if not slots:
            return None
        if kind == "land":
            return {"land": summarize(slots, LAND_RATES)}
        elif kind == "water":
            return {"water": summarize(slots, WATER_RATES)}
        elif kind == "rock":
            return {"rock_smash": summarize(slots, ROCK_RATES)}
        elif kind == "fishing":
            res = {}
            if len(slots) >= 2:
                res["old_rod"] = summarize(slots[0:2], OLD_ROD_RATES)
            if len(slots) >= 5:
                res["good_rod"] = summarize(slots[2:5], GOOD_ROD_RATES)
            if len(slots) >= 10:
                res["super_rod"] = summarize(slots[5:10], SUPER_ROD_RATES)
            return res
        return None

    # 3. Parse gWildMonHeaders[]
    encounters = {}
    hdr_idx = txt.rfind("gWildMonHeaders[]")
    if hdr_idx != -1:
        hdr_txt = txt[hdr_idx:]
        for m in re.finditer(r'\{([^{}]+)\}', hdr_txt):
            block = m.group(1)
            mg = re.search(r'\.mapGroup\s*=\s*MAP_GROUP\((\w+)\)', block)
            if not mg:
                continue
            map_id = "MAP_" + mg.group(1)
            entry = {"FR": {}, "LG": {}}
            for ver in ("FR", "LG"):
                for field, kind in [
                    (f"landMonsInfo_{ver}", "land"),
                    (f"waterMonsInfo_{ver}", "water"),
                    (f"rockSmashMonsInfo_{ver}", "rock"),
                    (f"fishingMonsInfo_{ver}", "fishing"),
                ]:
                    fm = re.search(rf'\.{field}\s*=\s*&?(s\w+|NULL)', block)
                    if fm and fm.group(1) != "NULL":
                        tbl = extract_table(fm.group(1), kind)
                        if tbl:
                            entry[ver].update(tbl)
            if entry["FR"] or entry["LG"]:
                encounters[map_id] = entry
    return encounters


def parse_all_scripts():
    """Index script labels -> item / trainer / master_trainer / move_tutor / shop / healer / pokemon info."""
    script_items = {}          # label -> list of (ITEM_*, qty)
    script_trainers = {}       # label -> list of TRAINER_* IDs directly in label
    script_calls = {}          # label -> set of called/goto'd labels
    script_masters = {}
    script_tutors = {}
    script_shops = set()
    script_healers = set()
    script_legendaries = {}    # label -> (species, level, is_gift)

    def scan_file(path):
        txt = path.read_text(errors="ignore")
        cur_label = None
        for line in txt.splitlines():
            s = line.strip()
            if not s or s.startswith("@") or s.startswith("//"):
                continue
            lm = re.match(r'^(\w+)::?$', s)
            if lm:
                cur_label = lm.group(1)
                continue
            if not cur_label:
                continue
            # Track call / goto edges for multi-step scripts
            cm = re.search(r'\b(?:call|goto|call_if_\w+|goto_if_\w+)\s+(?:[^,]+,\s*)*(\w+)$', s)
            if cm:
                script_calls.setdefault(cur_label, set()).add(cm.group(1))
            # Item ball / giveitem / additem
            im = re.search(r'\b(?:finditem|giveitem_msg|giveitem|additem)\s+(ITEM_\w+)(?:\s*,\s*(\d+))?', s)
            if im:
                qty = int(im.group(2)) if im.group(2) else 1
                pair = (im.group(1), qty)
                lst = script_items.setdefault(cur_label, [])
                if all(x[0] != pair[0] for x in lst):
                    lst.append(pair)
            # Regular trainer battle (collect all in order)
            tm = re.search(r'\btrainerbattle_\w+\s+(TRAINER_\w+)', s)
            if tm:
                lst = script_trainers.setdefault(cur_label, [])
                if tm.group(1) not in lst:
                    lst.append(tm.group(1))
            # Master Trainer battle
            mm = re.search(r'\btrainerbattle_\w+\s+(MASTER_\w+)', s)
            if mm and cur_label not in script_masters:
                script_masters[cur_label] = mm.group(1)
            # Move tutor
            mt = re.search(r'MOVETUTOR_(\w+)', s)
            if mt and cur_label not in script_tutors:
                script_tutors[cur_label] = title_from_const(mt.group(1))
            # Mart / Shop
            if re.search(r'\bpokemart\b', s):
                script_shops.add(cur_label)
            # Healer
            if re.search(r'\b(?:special\s+HealPlayerParty|call\s+EventScript_PkmnCenterNurse)\b', s):
                script_healers.add(cur_label)
            # Static / Legendary wild battle or gift Pokémon
            wm = re.search(r'\bsetwildbattle\s+(SPECIES_\w+)\s*,\s*(\d+)', s)
            if wm and cur_label not in script_legendaries:
                script_legendaries[cur_label] = (title_from_const(wm.group(1), "SPECIES_"), int(wm.group(2)), False)
            gm = re.search(r'\bgivemon\s+(SPECIES_\w+)\s*,\s*(\d+)', s)
            if gm and cur_label not in script_legendaries:
                script_legendaries[cur_label] = (title_from_const(gm.group(1), "SPECIES_"), int(gm.group(2)), True)

    for p in glob.glob(str(REPO / "data/scripts/*.inc")):
        scan_file(Path(p))
    for p in glob.glob(str(REPO / "data/maps/*/scripts.inc")):
        scan_file(Path(p))

    def resolve_chain(lbl, max_hops=5):
        visited = {lbl}
        order = [lbl]
        frontier = [lbl]
        for _ in range(max_hops):
            nxt_f = []
            for cur in frontier:
                for nxt in script_calls.get(cur, ()):
                    if nxt not in visited:
                        visited.add(nxt)
                        order.append(nxt)
                        nxt_f.append(nxt)
            frontier = nxt_f
            if not frontier:
                break
        return order

    all_labels = (
        set(script_trainers.keys())
        | set(script_items.keys())
        | set(script_tutors.keys())
        | set(script_legendaries.keys())
        | set(script_calls.keys())
    )

    resolved_trainers = {}
    resolved_items = {}
    resolved_tutors = {}
    resolved_legendaries = {}

    for lbl in all_labels:
        chain = resolve_chain(lbl)
        t_list = []
        i_list = []
        for cur in chain:
            for tid in script_trainers.get(cur, []):
                if tid not in t_list:
                    t_list.append(tid)
            for ipair in script_items.get(cur, []):
                if all(x[0] != ipair[0] for x in i_list):
                    i_list.append(ipair)
            if lbl not in resolved_tutors and cur in script_tutors:
                resolved_tutors[lbl] = script_tutors[cur]
            if lbl not in resolved_legendaries and cur in script_legendaries:
                resolved_legendaries[lbl] = script_legendaries[cur]
        if t_list:
            resolved_trainers[lbl] = t_list
        if i_list:
            resolved_items[lbl] = i_list

    return {
        "items": resolved_items,
        "trainers": resolved_trainers,
        "masters": script_masters,
        "tutors": resolved_tutors,
        "shops": script_shops,
        "healers": script_healers,
        "legendaries": resolved_legendaries,
    }


# ---------------------------------------------------------------------------
# Map Layer Definitions & Stitching
# ---------------------------------------------------------------------------

def pretty_map_name(name):
    # Special cleanups
    name = name.replace("SSAnne", "S.S. Anne")
    name = name.replace("MtMoon", "Mt. Moon")
    name = name.replace("MtEmber", "Mt. Ember")
    name = name.replace("SilphCo", "Silph Co.")
    name = name.replace("Pokemon", "Pokémon")
    name = name.replace("Digletts", "Diglett's")
    name = name.replace("Loreleis", "Lorelei's")
    name = name.replace("Brunos", "Bruno's")
    name = name.replace("Agathas", "Agatha's")
    name = name.replace("Lances", "Lance's")
    name = name.replace("Champions", "Champion's")
    name = name.replace("Players", "Player's")
    name = name.replace("Rivals", "Rival's")
    name = name.replace("ProfessorOaks", "Prof. Oak's")
    name = name.replace("Wardens", "Warden's")
    name = name.replace("Copycats", "Copycat's")
    name = name.replace("Scotts", "Scott's")
    name = name.replace("_", " — ")
    # Insert spaces before capitals / numbers in CamelCase
    name = re.sub(r'([a-z])([A-Z])', r'\1 \2', name)
    name = re.sub(r'(Route)(\d+)', r'\1 \2', name)
    name = re.sub(r'(Room)(\d+)', r'\1 \2', name)
    name = re.sub(r'(House)(\d+)', r'\1 \2', name)
    name = re.sub(r'(Lounge)(\d+)', r'\1 \2', name)
    name = re.sub(r'([A-Za-z])(\d+F)', r'\1 \2', name)
    return name


def bfs_stitch(start_id, maps_by_id, layouts, replace_map=None):
    """Stitch connected maps via BFS starting at start_id. Returns {map_id: (x_mt, y_mt)}."""
    pos = {start_id: (0, 0)}
    q = deque([start_id])
    while q:
        cid = q.popleft()
        cx, cy = pos[cid]
        cm = maps_by_id[cid]
        cl = layouts[cm["layout"]]
        cw, ch = cl["width"], cl["height"]
        for conn in cm.get("connections") or []:
            tid = conn["map"]
            if replace_map and tid in replace_map:
                tid = replace_map[tid]
            if tid not in maps_by_id:
                continue
            tl = layouts[maps_by_id[tid]["layout"]]
            tw, th = tl["width"], tl["height"]
            d = conn["direction"]
            off = conn["offset"]
            if d == "up":
                nx, ny = cx + off, cy - th
            elif d == "down":
                nx, ny = cx + off, cy + ch
            elif d == "left":
                nx, ny = cx - tw, cy + off
            elif d == "right":
                nx, ny = cx + cw, cy + off
            else:
                continue
            if tid not in pos:
                pos[tid] = (nx, ny)
                q.append(tid)
    # Normalize to (0, 0)
    min_x = min(x for x, _ in pos.values())
    min_y = min(y for _, y in pos.values())
    return {mid: (x - min_x, y - min_y) for mid, (x, y) in pos.items()}


def pack_components(components, maps_by_id, layouts, pad=4, max_row_w=160):
    """Pack multiple map groups (each a dict of map_id -> (x, y)) into a clean 2D sheet."""
    placed = {}
    cur_x = pad
    cur_y = pad
    row_h = 0
    for comp in components:
        if not comp:
            continue
        cw = max(x + layouts[maps_by_id[m]["layout"]]["width"] for m, (x, y) in comp.items())
        ch = max(y + layouts[maps_by_id[m]["layout"]]["height"] for m, (x, y) in comp.items())
        if cur_x > pad and cur_x + cw + pad > max_row_w:
            cur_x = pad
            cur_y += row_h + pad
            row_h = 0
        for m, (x, y) in comp.items():
            placed[m] = (cur_x + x, cur_y + y)
        cur_x += cw + pad
        row_h = max(row_h, ch)
    return placed


def place_cluster_near(placed, cluster, anchor_xy, maps_by_id, layouts, pad=2):
    """Place a compact cluster {map_id: (rx, ry)} at the closest non-overlapping position to anchor_xy."""
    if not cluster:
        return
    cw = max(x + layouts[maps_by_id[m]["layout"]]["width"] for m, (x, y) in cluster.items())
    ch = max(y + layouts[maps_by_id[m]["layout"]]["height"] for m, (x, y) in cluster.items())

    # Existing occupied rects with padding
    occ = []
    for m, (ox, oy) in placed.items():
        lay = layouts[maps_by_id[m]["layout"]]
        occ.append((ox - pad, oy - pad, ox + lay["width"] + pad, oy + lay["height"] + pad))

    def overlaps(x0, y0):
        x1, y1 = x0 + cw, y0 + ch
        for rx0, ry0, rx1, ry1 in occ:
            if x0 < rx1 and x1 > rx0 and y0 < ry1 and y1 > ry0:
                return True
        return False

    ax, ay = int(anchor_xy[0] - cw // 2), int(anchor_xy[1] - ch // 2)
    best = None
    best_dist = 1e18

    # Search in expanding rings (step 4 metatiles for speed)
    step = 4
    for r in range(0, 260, step):
        if best is not None and r * r > best_dist:
            break
        candidates = []
        if r == 0:
            candidates.append((ax, ay))
        else:
            for dx in range(-r, r + 1, step):
                candidates.append((ax + dx, ay - r))
                candidates.append((ax + dx, ay + r))
            for dy in range(-r + step, r, step):
                candidates.append((ax - r, ay + dy))
                candidates.append((ax + r, ay + dy))
        for cx, cy in candidates:
            if not overlaps(cx, cy):
                # Distance from cluster center to anchor
                dx = (cx + cw / 2) - anchor_xy[0]
                dy = (cy + ch / 2) - anchor_xy[1]
                d2 = dx * dx + dy * dy
                if d2 < best_dist:
                    best_dist = d2
                    best = (cx, cy)

    bx, by = best if best is not None else (ax, ay)
    for m, (rx, ry) in cluster.items():
        placed[m] = (bx + rx, by + ry)


def normalize_positions(pos, pad=3):
    min_x = min(x for x, _ in pos.values())
    min_y = min(y for _, y in pos.values())
    return {m: (x - min_x + pad, y - min_y + pad) for m, (x, y) in pos.items()}


def draw_rect_border(canvas, w_px, h_px, x0, y0, rw, rh, color=(148, 163, 184), thickness=2):
    stride = w_px * 3
    r, g, b = color
    for t in range(thickness):
        # Top and bottom edges
        for py in (y0 - 1 - t, y0 + rh + t):
            if 0 <= py < h_px:
                row_off = py * stride
                for px in range(max(0, x0 - 1 - t), min(w_px, x0 + rw + 1 + t)):
                    off = row_off + px * 3
                    canvas[off] = r
                    canvas[off + 1] = g
                    canvas[off + 2] = b
        # Left and right edges
        for px in (x0 - 1 - t, x0 + rw + t):
            if 0 <= px < w_px:
                for py in range(max(0, y0 - 1 - t), min(h_px, y0 + rh + 1 + t)):
                    off = py * stride + px * 3
                    canvas[off] = r
                    canvas[off + 1] = g
                    canvas[off + 2] = b


def read_png_palette(path):
    """Extract RGB tuples from an indexed PNG's PLTE chunk."""
    data = Path(path).read_bytes()
    pos = 8
    while pos < len(data):
        ln, typ = struct.unpack(">I4s", data[pos:pos + 8])
        chunk = data[pos + 8:pos + 8 + ln]
        pos += 12 + ln
        if typ == b"PLTE":
            return [(chunk[i], chunk[i + 1], chunk[i + 2]) for i in range(0, len(chunk), 3)]
        elif typ == b"IEND":
            break
    return [(0, 0, 0)] * 16


def load_all_object_sprites():
    """Parse FRLG-Plus object_event tables and load every overworld sprite (NPCs, trainers, Pokémon, items, HM obstacles)."""
    oe_dir = REPO / "src/data/object_events"
    ptrs_txt = (oe_dir / "object_event_graphics_info_pointers.h").read_text(errors="ignore")
    info_txt = (oe_dir / "object_event_graphics_info.h").read_text(errors="ignore")
    pics_txt = (oe_dir / "object_event_pic_tables.h").read_text(errors="ignore")
    gfx_txt = (oe_dir / "object_event_graphics.h").read_text(errors="ignore")

    # 1. gObjectEventPic_* -> .png path
    pic_paths = {}
    for m in re.finditer(r'(gObjectEventPic_\w+)\[\]\s*=\s*INCBIN_\w+\("([^"]+)"\)', gfx_txt):
        p = re.sub(r'\.4bpp(\.lz)?$', '.png', m.group(2))
        pic_paths[m.group(1)] = REPO / p

    # 2. sPicTable_* -> first gObjectEventPic_*
    table_to_pic = {}
    for m in re.finditer(r'static const struct SpriteFrameImage (sPicTable_\w+)\[\]\s*=\s*\{([^}]+)\};', pics_txt, re.S):
        pm = re.search(r'(gObjectEventPic_\w+)', m.group(2))
        if pm:
            table_to_pic[m.group(1)] = pm.group(1)

    # 3. gObjectEventGraphicsInfo_* -> (w, h, pic_sym)
    info_map = {}
    for m in re.finditer(r'const struct ObjectEventGraphicsInfo (gObjectEventGraphicsInfo_\w+)\s*=\s*\{([^}]+)\};', info_txt, re.S):
        body = m.group(2)
        wm = re.search(r'\.width\s*=\s*(\d+)', body)
        hm = re.search(r'\.height\s*=\s*(\d+)', body)
        im = re.search(r'\.images\s*=\s*(sPicTable_\w+)', body)
        if wm and hm and im:
            info_map[m.group(1)] = (int(wm.group(1)), int(hm.group(1)), table_to_pic.get(im.group(1)))

    # 4. OBJ_EVENT_GFX_* -> gObjectEventGraphicsInfo_*
    gfx_to_info = {}
    for m in re.finditer(r'\[(OBJ_EVENT_GFX_\w+)\]\s*=\s*&(gObjectEventGraphicsInfo_\w+)', ptrs_txt):
        gfx_to_info[m.group(1)] = m.group(2)

    # Cache decoded PNGs by path
    png_cache = {}
    sprites = {}
    for gfx_id, info_sym in gfx_to_info.items():
        if info_sym not in info_map:
            continue
        fw, fh, pic_sym = info_map[info_sym]
        path = pic_paths.get(pic_sym)
        if not path or not path.exists():
            continue
        if path not in png_cache:
            try:
                w, h, px = read_indexed_png(path)
                pal = read_png_palette(path)
                png_cache[path] = (w, h, px, pal)
            except Exception:
                png_cache[path] = None
        decoded = png_cache[path]
        if not decoded:
            continue
        w, h, px, pal = decoded
        num_frames = max(1, w // fw)
        # Pre-extract frames 0 (down), 1 (up), 2 (left) if available
        frames = {}
        for fidx in (0, 1, 2):
            if fidx >= num_frames:
                continue
            ox = fidx * fw
            pts = []
            for dy in range(min(fh, h)):
                row_off = dy * w + ox
                for dx in range(min(fw, w - ox)):
                    cidx = px[row_off + dx]
                    if cidx != 0 and cidx < len(pal):
                        r, g, b = pal[cidx]
                        pts.append((dx, dy, r, g, b))
            frames[fidx] = pts
        sprites[gfx_id] = (fw, fh, frames)
    return sprites


def blit_object_sprite(canvas, canvas_w_px, canvas_h_px, tile_x_px, tile_y_px, sprite_entry, movement_type=""):
    fw, fh, frames = sprite_entry
    # Pick facing direction frame for standard 3-frame people/Pokémon sheets
    fidx = 0
    flip_x = False
    if len(frames) >= 3:
        if "FACE_UP" in movement_type or "LOOK_UP" in movement_type:
            fidx = 1
        elif "FACE_LEFT" in movement_type:
            fidx = 2
        elif "FACE_RIGHT" in movement_type:
            fidx = 2
            flip_x = True
    pts = frames.get(fidx) or frames.get(0)
    if not pts:
        return
    # Anchor bottom-center of sprite onto bottom-center of 16x16 metatile
    x0 = tile_x_px + (16 - fw) // 2
    y0 = tile_y_px + (16 - fh)
    stride = canvas_w_px * 3
    for dx, dy, r, g, b in pts:
        px = x0 + ((fw - 1 - dx) if flip_x else dx)
        py = y0 + dy
        if 0 <= px < canvas_w_px and 0 <= py < canvas_h_px:
            off = py * stride + px * 3
            canvas[off] = r
            canvas[off + 1] = g
            canvas[off + 2] = b


def render_layer(layer_id, map_positions, maps_by_id, layouts, ts_cache, obj_sprites=None, framed_maps=None, bg_rgb=(15, 23, 42)):
    """Render all maps in map_positions onto one PNG and return (width_px, height_px)."""
    pad = 3
    max_x = max(x + layouts[maps_by_id[m]["layout"]]["width"] for m, (x, y) in map_positions.items()) + pad
    max_y = max(y + layouts[maps_by_id[m]["layout"]]["height"] for m, (x, y) in map_positions.items()) + pad
    w_px = max_x * 16
    h_px = max_y * 16
    canvas = bytearray(bytes(bg_rgb) * (w_px * h_px))
    for mid, (mx, my) in map_positions.items():
        mdata = maps_by_id[mid]
        layout = layouts[mdata["layout"]]
        blit_map_into_canvas(canvas, w_px, mx, my, layout, ts_cache)
        if obj_sprites:
            # Depth-sort by y so southern sprites overlap northern ones cleanly
            objs = sorted(mdata.get("object_events", []), key=lambda o: (o.get("y", 0), o.get("x", 0)))
            for obj in objs:
                gfx = obj.get("graphics_id", "")
                sp = obj_sprites.get(gfx)
                if sp:
                    blit_object_sprite(
                        canvas, w_px, h_px,
                        (mx + obj["x"]) * 16, (my + obj["y"]) * 16,
                        sp, obj.get("movement_type", "")
                    )
        if framed_maps and mid in framed_maps:
            draw_rect_border(
                canvas, w_px, h_px,
                mx * 16, my * 16, layout["width"] * 16, layout["height"] * 16,
                color=(148, 163, 184), thickness=2
            )
    out_path = OUT_MAPS / f"{layer_id}.png"
    write_rgb_png(out_path, w_px, h_px, canvas)
    return w_px, h_px


# ---------------------------------------------------------------------------
# Marker Extraction
# ---------------------------------------------------------------------------

def extract_markers_for_map(mdata, ox_mt, oy_mt, layer_id, game_data, dungeon_lookup, is_indoor_summary=False, entrance_xy=None, parent_name=None):
    """Extract all markers from a single map's JSON."""
    markers = []
    map_name = pretty_map_name(mdata["name"])
    scripts = game_data["scripts"]
    items_db = game_data["items"]
    trainers_db = game_data["trainers"]
    masters_db = game_data["masters"]

    def make_xy(x, y):
        if is_indoor_summary and entrance_xy:
            return entrance_xy[0], entrance_xy[1]
        return (ox_mt + x) * 16 + 8, (oy_mt + y) * 16 + 8

    sub_prefix = f"Inside {map_name}: " if is_indoor_summary else ""

    # 1. Object events (Items, Trainers, Master Trainers, Move Tutors, Legendaries, Shops, Healers)
    seen_story_trainers = set()
    for idx, obj in enumerate(mdata.get("object_events", [])):
        gfx = obj.get("graphics_id", "")
        script = obj.get("script", "")
        ttype = obj.get("trainer_type", "TRAINER_TYPE_NONE")
        px, py = make_xy(obj["x"], obj["y"])

        # Master Trainers (FRLG+)
        if script in scripts["masters"] or "MasterTrainer_EventScript_" in script:
            mid = scripts["masters"].get(script)
            if not mid and "MasterTrainer_EventScript_" in script:
                mid = "MASTER_" + script.replace("MasterTrainer_EventScript_", "").upper()
            mt = masters_db.get(mid, {})
            species = title_from_const(mid or script.replace("MasterTrainer_EventScript_", ""), "MASTER_")
            tname = mt.get("name", "")
            tclass = mt.get("class", "Master Trainer")
            party = mt.get("party", [])
            title = f"{species} Master ({tclass} {tname})".strip()
            markers.append({
                "id": f"{layer_id}:{mdata['name']}:master:{idx}",
                "layer": layer_id,
                "category": "master_trainer",
                "title": f"{sub_prefix}{title}",
                "map": map_name,
                "x": px,
                "y": py,
                "badge": "FRLG+",
                "party": party,
                "details": f"Post-game Master Trainer for {species}. Defeat in a 1v1 mirror match to earn the {species} Master title.",
            })
            continue

        # Visible Item Ball / TM / NPC Gift Item
        if gfx == "OBJ_EVENT_GFX_ITEM_BALL" or script in scripts["items"]:
            item_list = scripts["items"].get(script, [])
            if item_list:
                for it_i, (iid, qty) in enumerate(item_list):
                    idb = items_db.get(iid, {"name": title_from_const(iid, "ITEM_"), "description": ""})
                    is_tm = iid.startswith("ITEM_TM") or iid.startswith("ITEM_HM")
                    is_gift = gfx != "OBJ_EVENT_GFX_ITEM_BALL"
                    qty_str = f" ×{qty}" if qty > 1 else ""
                    gift_prefix = "NPC Gift: " if is_gift else ""
                    markers.append({
                        "id": f"{layer_id}:{mdata['name']}:item:{idx}:{it_i}" if it_i > 0 else f"{layer_id}:{mdata['name']}:item:{idx}",
                        "layer": layer_id,
                        "category": "tm" if is_tm else "item",
                        "title": f"{sub_prefix}{gift_prefix}{idb['name']}{qty_str}",
                        "map": map_name,
                        "x": px,
                        "y": py,
                        "details": idb.get("description", ""),
                    })
                if gfx == "OBJ_EVENT_GFX_ITEM_BALL":
                    continue

        # Move Tutors
        if script in scripts["tutors"] or "Tutor" in script:
            mv = scripts["tutors"].get(script)
            if not mv:
                m_t = re.search(r'EventScript_(\w+)Tutor', script)
                mv = pretty_map_name(m_t.group(1)) if m_t else "Special Move"
            markers.append({
                "id": f"{layer_id}:{mdata['name']}:tutor:{idx}",
                "layer": layer_id,
                "category": "npc",
                "sub": "Move Tutor",
                "title": f"{sub_prefix}Move Tutor: {mv}",
                "map": map_name,
                "x": px,
                "y": py,
                "details": f"Teaches {mv} (repeatable in FRLG+ after first use).",
            })
            continue

        # Static / Legendary / Gift Pokémon
        if script in scripts["legendaries"]:
            sp, lv, is_gift = scripts["legendaries"][script]
            markers.append({
                "id": f"{layer_id}:{mdata['name']}:legend:{idx}",
                "layer": layer_id,
                "category": "pokemon",
                "title": f"{sub_prefix}{'Gift: ' if is_gift else ''}{sp} (Lv. {lv})",
                "map": map_name,
                "x": px,
                "y": py,
                "details": f"{'Gift Pokémon' if is_gift else 'Static wild encounter'}: Lv. {lv} {sp}.",
            })
            continue

        # Regular Trainers, Story Trainers & Gym Leaders
        if ttype != "TRAINER_TYPE_NONE" or script in scripts["trainers"]:
            tids = scripts["trainers"].get(script, [])
            # Filter out starter-variant duplicates if a story script triggers multiple rival variants,
            # but keep distinct story trainers (like Three Island Biker Gang: Goon 1/2/3 + Cue Ball Paxton)
            added_any = False
            for t_i, tid in enumerate(tids):
                if tid not in trainers_db:
                    continue
                if ttype == "TRAINER_TYPE_NONE" and tid in seen_story_trainers:
                    continue
                seen_story_trainers.add(tid)
                tr = trainers_db[tid]
                is_boss = any(k in tid for k in ("LEADER_", "ELITE_FOUR_", "CHAMPION_", "BOSS_GIOVANNI", "RIVAL_"))
                title = f"{tr['class']} {tr['name']}".strip()
                markers.append({
                    "id": f"{layer_id}:{mdata['name']}:trainer:{idx}:{t_i}" if t_i > 0 else f"{layer_id}:{mdata['name']}:trainer:{idx}",
                    "layer": layer_id,
                    "category": "gym" if is_boss else "trainer",
                    "title": f"{sub_prefix}{title}",
                    "map": map_name,
                    "x": px,
                    "y": py,
                    "double": tr["double"],
                    "party": tr["party"],
                    "details": ("Story / Boss battle." if is_boss else ("Double Battle." if tr["double"] else ("Story trainer battle." if ttype == "TRAINER_TYPE_NONE" else "Trainer battle."))),
                })
                added_any = True
            if added_any:
                continue

        # Shops / Poké Marts (when summarizing indoor or outdoor)
        if script in scripts["shops"] and not is_indoor_summary:
            markers.append({
                "id": f"{layer_id}:{mdata['name']}:shop:{idx}",
                "layer": layer_id,
                "category": "npc",
                "sub": "Shop",
                "title": f"Poké Mart / Shop",
                "map": map_name,
                "x": px,
                "y": py,
                "details": "Buy items and Poké Balls.",
            })

    # 1b. Coord events that trigger story trainer battles (e.g. Rival encounters, Three Island Bikers)
    if not is_indoor_summary:
        for idx, ce in enumerate(mdata.get("coord_events", [])):
            script = ce.get("script", "")
            tids = scripts["trainers"].get(script, [])
            if not tids:
                continue
            px, py = make_xy(ce["x"], ce["y"])
            for t_i, tid in enumerate(tids):
                if tid not in trainers_db or tid in seen_story_trainers:
                    continue
                seen_story_trainers.add(tid)
                tr = trainers_db[tid]
                is_boss = any(k in tid for k in ("LEADER_", "ELITE_FOUR_", "CHAMPION_", "BOSS_GIOVANNI", "RIVAL_"))
                title = f"{tr['class']} {tr['name']}".strip()
                markers.append({
                    "id": f"{layer_id}:{mdata['name']}:story:{idx}:{t_i}",
                    "layer": layer_id,
                    "category": "gym" if is_boss else "trainer",
                    "title": f"{title} (Story Event)",
                    "map": map_name,
                    "x": px,
                    "y": py,
                    "double": tr["double"],
                    "party": tr["party"],
                    "details": "Triggered story battle.",
                })

    # 2. BG Events (Hidden Items)
    for idx, bg in enumerate(mdata.get("bg_events", [])):
        if bg.get("type") == "hidden_item":
            iid = bg.get("item", "")
            qty = bg.get("quantity", 1)
            idb = items_db.get(iid, {"name": title_from_const(iid, "ITEM_"), "description": ""})
            px, py = make_xy(bg["x"], bg["y"])
            qty_str = f" ×{qty}" if qty > 1 else ""
            markers.append({
                "id": f"{layer_id}:{mdata['name']}:hidden:{idx}",
                "layer": layer_id,
                "category": "hidden_item",
                "title": f"{sub_prefix}Hidden: {idb['name']}{qty_str}",
                "map": map_name,
                "x": px,
                "y": py,
                "details": idb.get("description", "Use the Itemfinder to locate this hidden item."),
            })

    # 3. Warps to Dungeons, Gyms, Poké Centers, Marts (only on non-indoor summary maps)
    if not is_indoor_summary:
        seen_dest = set()
        for idx, w in enumerate(mdata.get("warp_events", [])):
            dest = w.get("dest_map", "")
            if not dest or dest == "MAP_DYNAMIC":
                continue
            px, py = make_xy(w["x"], w["y"])
            # Link to a nearby dungeon / indoor area!
            if dest in dungeon_lookup and mdata["id"] not in dungeon_lookup:
                d_info = dungeon_lookup[dest]
                key = ("dungeon", d_info["id"], w["x"] // 2, w["y"] // 2)
                if key not in seen_dest:
                    seen_dest.add(key)
                    markers.append({
                        "id": f"{layer_id}:{mdata['name']}:warp:{idx}",
                        "layer": layer_id,
                        "category": "location",
                        "sub": "Dungeon Entrance",
                        "title": f"Entrance: {d_info['name']}",
                        "map": map_name,
                        "x": px,
                        "y": py,
                        "targetPoint": {
                            "layer": d_info["layer"],
                            "x": d_info["x"],
                            "y": d_info["y"],
                            "name": d_info["name"],
                        },
                        "details": f"Placed right nearby on this map. Click below to pan straight to {d_info['name']}.",
                    })
            elif "_GYM" in dest and ("gym", dest) not in seen_dest:
                seen_dest.add(("gym", dest))
                markers.append({
                    "id": f"{layer_id}:{mdata['name']}:gymwarp:{idx}",
                    "layer": layer_id,
                    "category": "gym",
                    "title": f"{map_name} Gym",
                    "map": map_name,
                    "x": px,
                    "y": py,
                    "details": f"Pokémon Gym entrance in {map_name}.",
                })
            elif "_POKEMON_CENTER_1F" in dest and ("pc", dest) not in seen_dest:
                seen_dest.add(("pc", dest))
                markers.append({
                    "id": f"{layer_id}:{mdata['name']}:pcwarp:{idx}",
                    "layer": layer_id,
                    "category": "npc",
                    "sub": "Pokémon Center",
                    "title": f"{map_name} Pokémon Center",
                    "map": map_name,
                    "x": px,
                    "y": py,
                    "details": "Heal your Pokémon, access PC storage, and visit the 2F Wireless Club.",
                })
            elif ("_MART" in dest or "DEPARTMENT_STORE_1F" in dest) and ("mart", dest) not in seen_dest:
                seen_dest.add(("mart", dest))
                markers.append({
                    "id": f"{layer_id}:{mdata['name']}:martwarp:{idx}",
                    "layer": layer_id,
                    "category": "npc",
                    "sub": "Poké Mart",
                    "title": f"{map_name} {'Dept. Store' if 'DEPARTMENT' in dest else 'Poké Mart'}",
                    "map": map_name,
                    "x": px,
                    "y": py,
                    "details": "Shop for Poké Balls, Potions, and supplies.",
                })

    return markers


# ---------------------------------------------------------------------------
# Main Build Pipeline
# ---------------------------------------------------------------------------

def main():
    t0 = time.time()
    OUT_MAPS.mkdir(parents=True, exist_ok=True)
    OUT_DATA.mkdir(parents=True, exist_ok=True)

    print("1. Loading layouts, maps, tilesets, and game data...")
    layouts = {l["id"]: l for l in json.load(open(REPO / "data/layouts/layouts.json"))["layouts"]}
    maps_by_id = {}
    maps_by_name = {}
    for p in glob.glob(str(REPO / "data/maps/*/map.json")):
        d = json.load(open(p))
        maps_by_id[d["id"]] = d
        maps_by_name[d["name"]] = d

    ts_defs = parse_tileset_definitions()
    ts_cache = TilesetCache(ts_defs)
    obj_sprites = load_all_object_sprites()
    print(f"   Loaded {len(obj_sprites)} overworld object sprites.")

    items_db = parse_items()
    species_types = parse_species_types()
    reg_parties = parse_party_file(REPO / "src/data/trainer_parties.h")
    master_parties = parse_party_file(REPO / "src/data/master_trainer_parties.h")
    trainers_db = parse_trainers_file(REPO / "src/data/trainers.h", reg_parties)
    masters_db = parse_trainers_file(REPO / "src/data/master_trainers.h", master_parties)
    encounters_db = parse_wild_encounters()
    scripts_db = parse_all_scripts()

    game_data = {
        "items": items_db,
        "trainers": trainers_db,
        "masters": masters_db,
        "encounters": encounters_db,
        "scripts": scripts_db,
    }
    print(f"   Loaded {len(maps_by_id)} maps, {len(items_db)} items, {len(trainers_db)} trainers, {len(masters_db)} master trainers, {len(encounters_db)} wild encounter areas.")

    # Define unified overworld maps with all caves & dungeons placed right next to their entrances!
    # 1) Kanto Overworld base
    kanto_pos = bfs_stitch(
        "MAP_SAFFRON_CITY",
        maps_by_id,
        layouts,
        replace_map={"MAP_SAFFRON_CITY_CONNECTION": "MAP_SAFFRON_CITY"},
    )
    kanto_overworld_ids = set(kanto_pos.keys())

    # Kanto dungeons: (id, label, anchor_map_name, map_list, max_row_w)
    kanto_dungeons = [
        ("viridian_forest", "Viridian Forest", "Route2", ["ViridianForest"], 80),
        ("mt_moon", "Mt. Moon", "Route4", ["MtMoon_1F", "MtMoon_B1F", "MtMoon_B2F"], 90),
        ("ss_anne", "S.S. Anne", "VermilionCity", ["SSAnne_Exterior", "SSAnne_1F_Corridor", "SSAnne_2F_Corridor", "SSAnne_3F_Corridor", "SSAnne_B1F_Corridor", "SSAnne_Deck", "SSAnne_Kitchen", "SSAnne_CaptainsOffice"], 95),
        ("digletts_cave", "Diglett's Cave", "Route11", ["DiglettsCave_NorthEntrance", "DiglettsCave_B1F", "DiglettsCave_SouthEntrance"], 90),
        ("rock_tunnel", "Rock Tunnel", "Route10", ["RockTunnel_1F", "RockTunnel_B1F"], 90),
        ("power_plant", "Power Plant", "Route10", ["PowerPlant"], 70),
        ("pokemon_tower", "Pokémon Tower", "LavenderTown", [f"PokemonTower_{i}F" for i in range(1, 8)], 90),
        ("rocket_hideout", "Rocket Hideout", "CeladonCity", ["RocketHideout_B1F", "RocketHideout_B2F", "RocketHideout_B3F", "RocketHideout_B4F"], 85),
        ("silph_co", "Silph Co.", "SaffronCity", [f"SilphCo_{i}F" for i in range(1, 12)], 105),
        ("safari_zone", "Safari Zone (+ FRLG+ Zones)", "FuchsiaCity", ["SafariZone_Center", "SafariZone_East", "SafariZone_North", "SafariZone_West", "SafariZone_NorthWest", "SafariZone_NorthEast"], 115),
        ("seafoam_islands", "Seafoam Islands", "Route20", ["SeafoamIslands_1F", "SeafoamIslands_B1F", "SeafoamIslands_B2F", "SeafoamIslands_B3F", "SeafoamIslands_B4F"], 105),
        ("pokemon_mansion", "Pokémon Mansion", "CinnabarIsland", ["PokemonMansion_1F", "PokemonMansion_2F", "PokemonMansion_3F", "PokemonMansion_B1F"], 95),
        ("victory_road", "Victory Road", "Route23", ["VictoryRoad_1F", "VictoryRoad_2F", "VictoryRoad_3F"], 85),
        ("cerulean_cave", "Cerulean Cave", "CeruleanCity", ["CeruleanCave_1F", "CeruleanCave_2F", "CeruleanCave_B1F"], 90),
        ("route20_underwater", "Route 20 Underwater (FRLG+)", "Route20", ["Route20_Underwater"], 90),
    ]

    framed_kanto = set()
    for did, dlabel, anchor_name, mnames, mw in kanto_dungeons:
        comps = [{maps_by_name[mn]["id"]: (0, 0)} for mn in mnames if mn in maps_by_name]
        cluster = pack_components(comps, maps_by_id, layouts, pad=2, max_row_w=mw)
        amap_id = maps_by_name[anchor_name]["id"]
        ax, ay = kanto_pos[amap_id]
        alay = layouts[maps_by_id[amap_id]["layout"]]
        place_cluster_near(kanto_pos, cluster, (ax + alay["width"] // 2, ay + alay["height"] // 2), maps_by_id, layouts, pad=3)
        framed_kanto.update(cluster.keys())
    kanto_pos = normalize_positions(kanto_pos, pad=3)

    # 2) Sevii Islands & FRLG+ Bonus Islands — each island clustered with its OWN caves right beside it!
    def build_island_cluster(start_map, attached_dungeons):
        cpos = bfs_stitch(start_map, maps_by_id, layouts) if start_map in maps_by_id else {}
        framed = set()
        for anchor_name, mnames, mw in attached_dungeons:
            comps = [{maps_by_name[mn]["id"]: (0, 0)} for mn in mnames if mn in maps_by_name]
            if not comps:
                continue
            sub = pack_components(comps, maps_by_id, layouts, pad=2, max_row_w=mw)
            amap_id = maps_by_name.get(anchor_name, {}).get("id", start_map)
            if amap_id in cpos:
                ax, ay = cpos[amap_id]
                alay = layouts[maps_by_id[amap_id]["layout"]]
                anchor = (ax + alay["width"] // 2, ay + alay["height"] // 2)
            else:
                anchor = (0, 0)
            place_cluster_near(cpos, sub, anchor, maps_by_id, layouts, pad=2)
            framed.update(sub.keys())
        return normalize_positions(cpos, pad=0), framed

    sevii_cluster_specs = [
        ("MAP_ONE_ISLAND", [
            ("OneIsland_KindleRoad", ["MtEmber_Exterior", "MtEmber_SummitPath_1F", "MtEmber_SummitPath_2F", "MtEmber_SummitPath_3F", "MtEmber_Summit", "MtEmber_RubyPath_1F", "MtEmber_RubyPath_B1F", "MtEmber_RubyPath_B2F", "MtEmber_RubyPath_B3F", "MtEmber_RubyPath_B4F", "MtEmber_RubyPath_B5F"], 100),
        ]),
        ("MAP_TWO_ISLAND", []),
        ("MAP_THREE_ISLAND", [
            ("ThreeIsland_Port", ["ThreeIsland_DunsparceTunnel"], 60),
            ("ThreeIsland_BondBridge", ["ThreeIsland_BerryForest", "ThreeIsland_BondBridge_Underwater"], 90),
        ]),
        ("MAP_FOUR_ISLAND", [
            ("FourIsland", ["FourIsland_IcefallCave_Entrance", "FourIsland_IcefallCave_1F", "FourIsland_IcefallCave_B1F", "FourIsland_IcefallCave_Back", "FourIsland_Base"], 85),
        ]),
        ("MAP_FIVE_ISLAND", [
            ("FiveIsland_Meadow", ["FiveIsland_RocketWarehouse"], 60),
            ("FiveIsland_ResortGorgeous", ["FiveIsland_LostCave_Entrance"] + [f"FiveIsland_LostCave_Room{i}" for i in range(1, 15)], 110),
        ]),
        ("MAP_SIX_ISLAND", [
            ("SixIsland_GreenPath", ["SixIsland_PatternBush", "SixIsland_AlteringCave"], 90),
            ("SixIsland_RuinValley", ["SixIsland_DottedHole_1F", "SixIsland_DottedHole_B1F", "SixIsland_DottedHole_B2F", "SixIsland_DottedHole_B3F", "SixIsland_DottedHole_B4F", "SixIsland_DottedHole_SapphireRoom"], 90),
        ]),
        ("MAP_SEVEN_ISLAND", [
            ("SevenIsland_TanobyRuins", ["SevenIsland_SevaultCanyon_TanobyKey", "SevenIsland_TanobyRuins_MoneanChamber", "SevenIsland_TanobyRuins_LiptooChamber", "SevenIsland_TanobyRuins_WeepthChamber", "SevenIsland_TanobyRuins_DilfordChamber", "SevenIsland_TanobyRuins_ScufibChamber", "SevenIsland_TanobyRuins_RixyChamber", "SevenIsland_TanobyRuins_ViapoisChamber"], 105),
        ]),
        ("MAP_BATTLE_FRONTIER_OUTSIDE_WEST", [
            ("BattleFrontier_OutsideEast", ["ArtisanCave_1F", "ArtisanCave_B1F"], 80),
        ]),
        ("MAP_NAVEL_ROCK_EXTERIOR", [
            ("NavelRock_Exterior", ["NavelRock_1F", "NavelRock_B1F", "NavelRock_Fork", "NavelRock_Summit", "NavelRock_Base"], 100),
        ]),
        ("MAP_BIRTH_ISLAND_EXTERIOR", []),
        ("MAP_FARAWAY_ISLAND_EXTERIOR", [
            ("FarawayIsland_Exterior", ["FarawayIsland_Interior"], 60),
        ]),
        ("MAP_SOUTHERN_ISLAND_EXTERIOR", [
            ("SouthernIsland_Exterior", ["SouthernIsland_Interior"], 60),
        ]),
    ]

    sevii_clusters = []
    framed_sevii = set()
    for start_id, attached in sevii_cluster_specs:
        if start_id in maps_by_id:
            cpos, fset = build_island_cluster(start_id, attached)
            sevii_clusters.append(cpos)
            framed_sevii.update(fset)
    sevii_pos = pack_components(sevii_clusters, maps_by_id, layouts, pad=6, max_row_w=460)
    sevii_pos = normalize_positions(sevii_pos, pad=3)

    # Quick Jump list & dungeon entrance lookup (every dungeon points straight to its coordinates on Kanto or Sevii!)
    sevii_dungeon_specs = [
        ("mt_ember", "Mt. Ember", "Sevii & Bonus Dungeons", ["MtEmber_Exterior", "MtEmber_SummitPath_1F", "MtEmber_SummitPath_2F", "MtEmber_SummitPath_3F", "MtEmber_Summit", "MtEmber_RubyPath_1F", "MtEmber_RubyPath_B1F", "MtEmber_RubyPath_B2F", "MtEmber_RubyPath_B3F", "MtEmber_RubyPath_B4F", "MtEmber_RubyPath_B5F"]),
        ("dunsparce_tunnel", "Three Isle Path (Dunsparce Tunnel)", "Sevii & Bonus Dungeons", ["ThreeIsland_DunsparceTunnel"]),
        ("berry_forest", "Berry Forest", "Sevii & Bonus Dungeons", ["ThreeIsland_BerryForest"]),
        ("icefall_cave", "Icefall Cave", "Sevii & Bonus Dungeons", ["FourIsland_IcefallCave_Entrance", "FourIsland_IcefallCave_1F", "FourIsland_IcefallCave_B1F", "FourIsland_IcefallCave_Back"]),
        ("rocket_warehouse", "Rocket Warehouse", "Sevii & Bonus Dungeons", ["FiveIsland_RocketWarehouse"]),
        ("lost_cave", "Lost Cave", "Sevii & Bonus Dungeons", ["FiveIsland_LostCave_Entrance"] + [f"FiveIsland_LostCave_Room{i}" for i in range(1, 15)]),
        ("pattern_bush", "Pattern Bush", "Sevii & Bonus Dungeons", ["SixIsland_PatternBush"]),
        ("altering_cave", "Altering Cave", "Sevii & Bonus Dungeons", ["SixIsland_AlteringCave"]),
        ("dotted_hole", "Dotted Hole", "Sevii & Bonus Dungeons", ["SixIsland_DottedHole_1F", "SixIsland_DottedHole_B1F", "SixIsland_DottedHole_B2F", "SixIsland_DottedHole_B3F", "SixIsland_DottedHole_B4F", "SixIsland_DottedHole_SapphireRoom"]),
        ("tanoby_ruins", "Tanoby Chambers & Key", "Sevii & Bonus Dungeons", ["SevenIsland_SevaultCanyon_TanobyKey", "SevenIsland_TanobyRuins_MoneanChamber", "SevenIsland_TanobyRuins_LiptooChamber", "SevenIsland_TanobyRuins_WeepthChamber", "SevenIsland_TanobyRuins_DilfordChamber", "SevenIsland_TanobyRuins_ScufibChamber", "SevenIsland_TanobyRuins_RixyChamber", "SevenIsland_TanobyRuins_ViapoisChamber"]),
        ("navel_rock", "Navel Rock", "Sevii & Bonus Dungeons", ["NavelRock_1F", "NavelRock_B1F", "NavelRock_Fork", "NavelRock_Summit", "NavelRock_Base"]),
        ("artisan_cave", "Artisan Cave (FRLG+)", "Sevii & Bonus Dungeons", ["ArtisanCave_1F", "ArtisanCave_B1F"]),
        ("battle_frontier", "Battle Frontier (FRLG+)", "Sevii & Bonus Dungeons", ["BattleFrontier_OutsideWest", "BattleFrontier_OutsideEast"]),
        ("faraway_island", "Faraway Island (FRLG+)", "Sevii & Bonus Dungeons", ["FarawayIsland_Exterior", "FarawayIsland_Interior"]),
        ("southern_island", "Southern Island (FRLG+)", "Sevii & Bonus Dungeons", ["SouthernIsland_Exterior", "SouthernIsland_Interior"]),
    ]

    quick_jumps = []
    dungeon_lookup = {}

    def register_jump(jid, jname, jgroup, layer_id, pos_dict, mnames):
        mids = [maps_by_name[mn]["id"] for mn in mnames if mn in maps_by_name and maps_by_name[mn]["id"] in pos_dict]
        if not mids:
            return
        # Center on the first (entrance) map of this dungeon
        first_mid = mids[0]
        ox, oy = pos_dict[first_mid]
        lay = layouts[maps_by_id[first_mid]["layout"]]
        cx = (ox + lay["width"] / 2) * 16
        cy = (oy + lay["height"] / 2) * 16
        info = {"id": jid, "name": jname, "group": jgroup, "layer": layer_id, "x": int(cx), "y": int(cy)}
        quick_jumps.append(info)
        for mid in mids:
            dungeon_lookup[mid] = info

    for did, dlabel, _, mnames, _ in kanto_dungeons:
        register_jump(did, dlabel, "Kanto Dungeons (Jump)", "kanto", kanto_pos, mnames)
    for did, dlabel, dgroup, mnames in sevii_dungeon_specs:
        register_jump(did, dlabel, "Sevii & Bonus (Jump)", "sevii", sevii_pos, mnames)

    # Extra gatehouse warps
    for extra_id, target_key in [
        ("MAP_ROUTE2_VIRIDIAN_FOREST_SOUTH_ENTRANCE", "MAP_VIRIDIAN_FOREST"),
        ("MAP_ROUTE2_VIRIDIAN_FOREST_NORTH_ENTRANCE", "MAP_VIRIDIAN_FOREST"),
        ("MAP_FUCHSIA_CITY_SAFARI_ZONE_ENTRANCE", "MAP_SAFARI_ZONE_CENTER"),
    ]:
        if target_key in dungeon_lookup:
            dungeon_lookup[extra_id] = dungeon_lookup[target_key]

    all_layers_def = [
        ("kanto", "Kanto Overworld & Dungeons", "Overworld", kanto_pos, framed_kanto),
        ("sevii", "Sevii & Bonus Islands (+ Dungeons)", "Overworld", sevii_pos, framed_sevii),
    ]

    # Track which maps are rendered directly on some layer
    rendered_maps = set()
    for _, _, _, pos, _ in all_layers_def:
        rendered_maps.update(pos.keys())

    # Render PNGs & extract zones + markers
    layers_meta = []
    all_markers = []

    print(f"2. Rendering {len(all_layers_def)} unified map layers to PNG and extracting markers...")
    for lid, label, group, pos, framed_set in all_layers_def:
        t_layer = time.time()
        w_px, h_px = render_layer(lid, pos, maps_by_id, layouts, ts_cache, obj_sprites=obj_sprites, framed_maps=framed_set)

        zones = []
        layer_markers = []
        for mid, (ox, oy) in pos.items():
            mdata = maps_by_id[mid]
            lay = layouts[mdata["layout"]]
            zx, zy = ox * 16, oy * 16
            zw, zh = lay["width"] * 16, lay["height"] * 16
            mname = pretty_map_name(mdata["name"])
            enc = encounters_db.get(mid)
            zones.append({
                "id": mid,
                "name": mname,
                "x": zx,
                "y": zy,
                "w": zw,
                "h": zh,
                "hasEncounters": bool(enc),
                "encounters": enc,
            })

            # Extract markers directly on this map
            layer_markers.extend(extract_markers_for_map(mdata, ox, oy, lid, game_data, dungeon_lookup))

            # For overworld maps, also summarize key indoor markers (Master Trainers, Move Tutors, Items, Gym Leaders)
            # attached to their building's entrance warp so nothing in towns/routes is missed!
            if group == "Overworld":
                visited_indoors = set()
                for w in mdata.get("warp_events", []):
                    dest = w.get("dest_map", "")
                    if not dest or dest in rendered_maps or dest in visited_indoors or dest not in maps_by_id:
                        continue
                    visited_indoors.add(dest)
                    ex = (ox + w["x"]) * 16 + 8
                    ey = (oy + w["y"]) * 16 + 8
                    # Check 1-level & 2-level indoor rooms (e.g., 2F of houses/labs/dept store)
                    iq = deque([dest])
                    checked = {dest}
                    while iq:
                        imid = iq.popleft()
                        imdata = maps_by_id[imid]
                        imarkers = extract_markers_for_map(
                            imdata, 0, 0, lid, game_data, dungeon_lookup,
                            is_indoor_summary=True, entrance_xy=(ex, ey), parent_name=mname
                        )
                        # Only attach high-value indoor markers (Master Trainers, TMs, Items, Hidden Items, Move Tutors, Gym Leaders, Legendaries)
                        for mk in imarkers:
                            if mk["category"] in ("master_trainer", "tm", "item", "hidden_item", "gym", "pokemon") or mk.get("sub") == "Move Tutor":
                                layer_markers.append(mk)
                        # Follow indoor stairs/elevators within same building prefix
                        bprefix = imdata["name"].split("_")[0]
                        for iw in imdata.get("warp_events", []):
                            idest = iw.get("dest_map", "")
                            if idest and idest not in checked and idest not in rendered_maps and idest in maps_by_id:
                                if maps_by_id[idest]["name"].startswith(bprefix + "_"):
                                    checked.add(idest)
                                    iq.append(idest)

        all_markers.extend(layer_markers)
        layers_meta.append({
            "id": lid,
            "name": label,
            "group": group,
            "image": f"assets/maps/{lid}.png",
            "width": w_px,
            "height": h_px,
            "zones": zones,
            "markerCount": len(layer_markers),
        })
        print(f"   [{lid}] {w_px}x{h_px}px | {len(zones)} zones | {len(layer_markers)} markers ({time.time() - t_layer:.2f}s)")

    out_payload = {
        "meta": {
            "game": "Pokémon FireRed & LeafGreen+",
            "repo": "https://github.com/Deokishisu/FRLG-Plus",
            "generatedAt": time.strftime("%Y-%m-%d %H:%M:%S"),
            "totalLayers": len(layers_meta),
            "totalMarkers": len(all_markers),
        },
        "speciesTypes": species_types,
        "quickJumps": quick_jumps,
        "layers": layers_meta,
        "markers": all_markers,
    }

    data_path = OUT_DATA / "map_data.json"
    json_str = json.dumps(out_payload, separators=(",", ":"))
    data_path.write_text(json_str)
    # Also write JS bundle so opening index.html directly via file:// works without CORS issues
    (OUT_DATA / "map_data.js").write_text("window.FRLG_MAP_DATA = " + json_str + ";\n")

    size_mb = data_path.stat().st_size / (1024 * 1024)
    print(f"\nDone in {time.time() - t0:.2f}s! Wrote {len(all_markers)} markers across {len(layers_meta)} layers ({size_mb:.2f} MB JSON).")


if __name__ == "__main__":
    main()
