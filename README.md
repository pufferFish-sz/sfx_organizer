# sfx_organizer

**English** | [简体中文](README.zh-CN.md)

A Python GUI tool that automatically organizes a sound effects library according to **UCS v8.2.1** (Universal Category System: 753 entries across 82 categories).
It handles file names in Chinese, English, or a mix of both, and outputs a consistent folder structure using the English UCS category names.

For the full requirements, see [`sfx_organizer_spec.md`](../sfx_organizer_spec.md).

---

## Installation

Requires Python 3.10 or later.

```bash
cd sfx_organizer
pip install -r requirements.txt
```

Optional dependencies degrade gracefully when missing instead of raising errors:

| Package | Purpose | Behavior when missing |
|----|------|--------------|
| `jieba` | Chinese word segmentation | Falls back to longest-match dictionary lookup |
| `mutagen` | Reads audio metadata (BWF/ID3/Vorbis) | Skips the P3 metadata fallback and classifies by file name only |
| `py7zr` | Extracts `.7z` archives | `.7z` files are logged as "unsupported" |
| `rarfile` | Extracts `.rar` archives | `.rar` files are logged as "unsupported" |
| `pyzipper` | Writes encrypted zips (tests only) | Encrypted-zip tests are skipped |

## Running it

```bash
python main.py
```

## Three working modes

Selectable with radio buttons in the GUI:

- **Organize directly (no extraction):** audio files and archives are classified by file name and moved or copied to the destination
- **Extract, then organize:** all archives are first extracted to a temporary folder, each audio file is classified, and the temporary folder is cleaned up afterward
- **Add to an existing library:** takes a source plus the root of an existing library, reuses its folder structure, and automatically ignores the destination so it isn't scanned recursively

## Classification strategy (spec 2.5)

Four priority levels. **Classification stops at the first match:**

1. **Direct CatID prefix match:** if a file name starts with a standard UCS CatID (for example `AMBLake_xxx` or `GUNAntq_001`), it goes straight into that subcategory
2. **Weighted keyword matching:** the file name goes through nine preprocessing steps (strip numbering, split Chinese and English, segment Chinese words, map Chinese to English, remove stopwords) to produce a token set, which is scored against every UCS entry:
   - Exact category name match: `4.0 × len`
   - Exact subcategory name match: `3.0 × len`
   - Subcategory name substring match (len ≥ 5): `2.0 × len`
   - CatShort match: `2.0 × len`
   - Synonym match: `1.0 × len`

   Each (token, entry) pair keeps only its *highest*-weighted match. Then:
   - If the **best entry's category** ✓ is also the **category with the highest total score**, the entry scores ≥ 4.0, and it leads the runner-up in the same category by ≥ 1.5 → **file goes into that subcategory**
   - Otherwise, if the category's total score is ≥ 4.0 and it leads the second-best category by ≥ 10.0 → **file goes into the category root**
   - Otherwise → `_misc/`

3. **Audio metadata fallback:** if everything above fails and the file is audio, the tool reads the BWF/ID3/Vorbis Title, Description, Comment, Subject, and Keywords fields, treats that text as a new file name, and runs step 2 again
4. **Everything fails** → `_misc/`

There's also a built-in garbage-filename filter: default names like `untitled_001.wav` or `track01.wav` go straight to `_misc/` so they don't get pulled into a category by a chance subcategory match.

## Data files

All in `data/`, and all editable by hand:

| File | Contents |
|------|------|
| `ucs_v8_2_1.csv` | UCS table (753 entries). Bundled with the project; swap in your own version if needed |
| `zh_en_dict.json` | Chinese–English synonym dictionary (282 entries covering all 82 categories). Keys starting with `_` are comments |
| `stopwords.json` | Industry noise words (SFX, Sample, Pack, 音效, 采样, ...) |
| `brands.txt` | Sound library vendor names, one per line; lines starting with `#` are comments. Add the vendors you use most |
| `extra_synonyms.json` | Extra UCS synonyms (without modifying the original table), keyed as `"CATEGORY/SUBCATEGORY"` |

## Tests

```bash
python -m pytest tests/
```

112 test cases (4 require optional dependencies), covering:
- `ucs_loader` / `tokenizer` / `preprocessor` / `classifier` (including the real-world samples from spec 2.6)
- `scanner` / `organizer` / `disk_checker` / `extractor`

The fixture `tests/fixtures/real_filenames.txt` contains the real-world samples from the spec. **Any change to the classification logic must keep all 13 of them passing.**

## Building a .exe (PyInstaller)

```bash
pip install pyinstaller
python build.py             # onedir by default; output goes to dist/sfx_organizer/
python build.py --onefile   # single .exe (one file to distribute, but slower startup since it unpacks to TEMP each run)
python build.py --skip-tests --clean   # pass-through options
```

All packaging details live in [`sfx_organizer.spec`](sfx_organizer.spec), so you can also run `pyinstaller sfx_organizer.spec` directly. The spec:

- Bundles the five resource files in `data/` (UCS table, dictionary, stopwords, brands, extra_synonyms)
- Detects jieba automatically (includes its `dict.txt` if installed, skips it otherwise)
- Excludes large unused packages like matplotlib, numpy, pandas, and Qt
- Sets `console=False` so double-clicking doesn't open a console window

In the built .exe, the `data/*.json/.csv/.txt` files are read-only (they live in a temporary unpack folder). **To extend the dictionary, add vendor names, or adjust extra_synonyms, use the "Choose..." button in the GUI to point to an external copy** (just copy the bundled file of the same name into any writable folder).

Resource path resolution is handled in [`utils/paths.py`](utils/paths.py): it checks `sys._MEIPASS` first and falls back to `__file__.parent.parent` in dev mode, so the rest of the code doesn't need to know whether it's running frozen.

## Project structure

```
sfx_organizer/
├── main.py
├── data/
│   ├── ucs_v8_2_1.csv          # UCS table (bundled)
│   ├── zh_en_dict.json         # Chinese–English mapping dictionary
│   ├── stopwords.json          # industry noise words
│   ├── brands.txt              # vendor names (editable)
│   └── extra_synonyms.json     # extra UCS synonyms
├── core/
│   ├── ucs_loader.py           # CSV loading + indexing
│   ├── preprocessor.py         # ★ nine-step file name cleanup
│   ├── tokenizer.py            # splitting + Chinese segmentation + zh–en mapping
│   ├── classifier.py           # ★ four-level priority + weighted scoring
│   ├── extractor.py            # archive handling (zip/rar/7z + passwords)
│   ├── organizer.py            # file moves + folder creation + conflicts
│   ├── scanner.py              # recursive scanning
│   └── disk_checker.py         # disk space estimate
├── gui/
│   ├── main_window.py          # main window (layout from spec section 6)
│   ├── worker.py               # background worker thread + events
│   ├── password_dialog.py      # password prompt for encrypted archives + session cache
│   └── preview_dialog.py       # classification preview table
├── utils/
│   ├── logger.py               # logging to both file and GUI
│   ├── metadata_reader.py      # mutagen wrapper
│   └── paths.py                # resource paths (dev / PyInstaller compatible)
├── sfx_organizer.spec          # PyInstaller spec (see "Building a .exe" above)
├── build.py                    # one-step build script
└── tests/
    ├── test_*.py               # 112 test cases
    └── fixtures/
        └── real_filenames.txt
```

## FAQ

**Q: Files are misclassified, or everything ends up in `_misc`?**
- Check whether `data/brands.txt` is filtering out words you don't want filtered (a vendor name accidentally removing a real keyword)
- Check whether `data/zh_en_dict.json` covers the Chinese terms you use most; new mappings take effect immediately
- For subcategories that are genuinely hard to match, add synonyms in `data/extra_synonyms.json`

**Q: How fast is it?**
- Around a few seconds for 1,000 files on a single thread (scoring runs entirely in memory; file copying is the bottleneck)
- Multithreading is planned as a V2 enhancement and isn't implemented yet

**Q: How are encrypted zips handled?**
- The GUI shows a password prompt with 3 attempts. Check "Remember this password" to reuse it automatically for later encrypted archives in the same session
- If you click Skip, the file is logged and the run continues

**Q: Errors with Chinese paths on Windows?**
- The project uses `pathlib.Path` and UTF-8 throughout, and this has been tested. If you still see errors, check whether another process is using the source folder

## Limitations (planned for V2)

- AI-based semantic classification (local model or API)
- Audio fingerprint deduplication
- Automatic renaming to UCS naming conventions
- SQLite index for searching
- Multithreaded processing (the worker currently runs single-threaded)
