# dupehunt

**Find the duplicates, then decide what to do about them.**

Every disk fills up the same way: `IMG_2041.jpg` copied to a backup folder, a photo album downloaded twice, `report.pdf` in three project directories, screenshots saved from the same chat. `dupehunt` finds identical files and visually near-identical images, shows you what it found, and then either moves the extra copies into a folder you choose or replaces them with hard links.

```console
$ dupehunt scan ~/Pictures --min-size 100KB
# dupehunt report

Scanned **8123** files under `/home/you/Pictures`.

- duplicate copies: **412**
- reclaimable space: **3.4 GB**
- clusters: 187

## 1. exact duplicate (2 files, 4.1 MB)

- keep: `/home/you/Pictures/2024/trip/beach.jpg`
- copy: `/home/you/Pictures/backup/beach.jpg` (4.1 MB)
```

Then, once you have read the report:

```bash
dupehunt quarantine ~/Pictures --to ~/duplicates-outbox --min-size 100KB --apply
dupehunt link ~/Pictures --min-size 100KB --apply
```

## Install

```bash
python -m pip install -e ".[dev]"    # Pillow is needed for image similarity
```

Python 3.9+. The duplicate scanner is standard library only; near-duplicate image detection uses Pillow.

## How it decides

1. **Group by size first.** A file with a unique size cannot be a duplicate of anything, so it is never read.
2. **Hash the rest** with BLAKE2b, in 1 MiB blocks, so a scan uses little memory.
3. **Group identical hashes** into exact-duplicate clusters.
4. **Compare images** with a difference hash (a 64-bit fingerprint of the image at 9×8 pixels). Images whose fingerprints are within `--threshold` bits are grouped as *similar*, so a photo saved at two sizes or recompressed still clusters.
5. **Choose one file to keep** per cluster: `--keep oldest` (default), `newest`, `shortest` or `first`.

## Safety model

- `scan` never writes anything.
- `quarantine` and `link` are **dry runs** unless you pass `--apply`, and they print exactly what they would do.
- `quarantine` *moves* duplicates into a folder you name, keeping the relative path. Nothing is deleted, so undo is a drag-and-drop.
- `link` replaces a duplicate with a hard link to the kept file through a temporary name and `os.replace`, so there is no moment where the file is missing. It only works on one filesystem; failures are reported and the copy is left alone.
- Files that cannot be read are listed under `Skipped` instead of aborting the scan.

## Flags

| flag | meaning |
| --- | --- |
| `--min-size 1MB` | ignore small files; accepts `B`, `KB`, `MB`, `GB` |
| `--include` / `--exclude` | globs, repeatable |
| `--no-images` | skip image similarity (faster, stdlib only) |
| `--threshold 6` | image fingerprint distance, lower is stricter |
| `--keep oldest` | which copy to keep: `oldest`, `newest`, `shortest`, `first` |
| `--json` | machine-readable output |
| `--html report.html` | write a browsable report |

## Reports

`scan` prints markdown, `--json` prints the full cluster structure, and `--html` writes a single self-contained file you can open, search or attach to a bug report. Nothing leaves your machine.

## Limits worth knowing

- Near-duplicate detection covers images only. Audio, video and document similarity are not attempted.
- A dHash catches resizes, recompression and small crops, but not rotations or large crops.
- Hard linking is skipped across filesystems; use `quarantine` for external drives.
- The scan reads every candidate file fully. On slow or network disks, use `--min-size` to cut the work.

## Tests

```bash
python -m pytest
```

MIT licensed.
