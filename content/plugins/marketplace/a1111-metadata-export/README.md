# A1111 Metadata Export Plugin

A marketplace plugin for PotionUI that exports generations as PNGs carrying
the A1111/Forge-style `parameters` text chunk, so they can be dropped straight
into Automatic1111, Forge, CivitAI's upload flow, or any PNG-info tool that
reads that chunk.

## Features

- **Single-image export**: a button on the workbench image actions downloads
  one generated image with its parameters embedded.
- **Batch export**: a history tool downloads the selected generations as a ZIP
  of PNGs, each carrying its own parameters chunk, plus a report of any file
  that was skipped and why.
- **Format-neutral metadata**: the embedded `parameters` text follows the
  A1111 convention (prompt, negative prompt, steps, sampler, CFG scale, seed,
  size, model hash/name, LoRA hashes) that A1111, Forge and CivitAI all parse
  on upload.

## Installation

1. Go to Admin -> Plugins
2. Scan for plugins
3. Enable "A1111 Metadata Export"

## Usage

Once enabled:
- An export button appears on image actions in the workbench and history.
- "Download with A1111 metadata" appears as a history tool for a multi-image
  selection.

## API

| Method | Endpoint | Description |
|--------|----------|--------------|
| GET | `/api/plugins/a1111-metadata-export/export-png` | Export one image with embedded parameters |
| POST | `/api/plugins/a1111-metadata-export/export-zip` | Export a batch of generations as a ZIP |

## License

Same as PotionUI
