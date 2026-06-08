# IFC Clash Detector

A powerful, fast pairwise IFC clash detection tool with alignment, cleaning, and BCF export capabilities.

## Features

- **Pairwise Clash Detection**: Automatically detects clashes between all pairs of IFC models
- **IFC Alignment** (Optional): Align models to a common coordinate system using IfcPlacementAligner
- **Performance Optimization**: Automatically cleans IFC files (removes properties/materials) for faster detection
- **Multiple Export Formats**:
  - **JSON**: Detailed clash reports with element GUIDs and coordinates
- **Configurable Workflow**: Run alignment, clash detection, or cleaning separately

## Installation

### Prerequisites

- Python 3.10+
- `uv` package manager

### Setup

```bash
# Clone the repository
git clone https://github.com/yourusername/IFC-Clash-Detector.git
cd IFC-Clash-Detector

# Install dependencies using uv
uv sync
```

## Usage

### Quick Start

Place your IFC files in the `ifc/` folder, then run:

```bash
# Run clash detection without alignment (fastest, recommended)
uv run main.py

# Output: clash_outputs/*.json
```

### With Alignment

To align models to a common coordinate system before clash detection:

```bash
uv run main.py --align
```

### Export Formats

**JSON format** (default - detailed technical data):
```bash
uv run main.py -f json
```

### All Available Options

| Option | Description |
|--------|-------------|
| `--ifc-dir DIR` | Directory containing IFC files (default: `ifc`) |
| `-f {json}` | Output format (default: `json`) |
| `--out-dir DIR` | Output directory (default: `clash_outputs`) |
| `--prefix NAME` | Output filename prefix (default: `Clash`) |
| `--align` | Enable IFC alignment before clash detection |
| `--keep-aligned` | Keep aligned IFC files after detection (only with `--align`) |
| `--clean-only` | Only clean IFC files, skip alignment and detection |
| `--skip-existing` | Skip generating outputs that already exist |

### Examples

```bash
# Align and keep the aligned files for inspection
uv run main.py --align --keep-aligned

# Only clean IFC files (remove properties/materials)
uv run main.py --clean-only

# Run with custom directories
uv run main.py --ifc-dir models --out-dir results

# Skip alignment and use files directly
uv run main.py
```

## How It Works

### Workflow (Without Alignment)

1. **Load IFC Files**: Reads all `.ifc` files from `ifc-dir`
2. **Cleaning IFC Files**: Removes all `psets`, `materials`, etc., for faster clash detection.
3. **Clash Detection**: Runs pairwise comparison on all models
4. **Export Results**: Generates JSON or BCF reports

### Workflow (With `--align`)

1. **Alignment**: Aligns all models to common coordinate system
2. **Cleaning**: Removes properties and materials for speed
3. **Clash Detection**: Runs pairwise comparison on cleaned files
4. **Export**: Generates reports referencing original files
5. **Cleanup**: Removes temporary aligned files (unless `--keep-aligned`)

### Performance Tips

- **Alignment is optional**: Most use cases don't need it. Run without `--align` for fastest results.
- **Large models**: Use `--keep-aligned` with `--align` to inspect aligned geometry if clashes seem incorrect.
- **Incremental runs**: Use `--skip-existing` to only run clash detection for new file pairs.

## Output Format

### JSON Output

```json
{
  "file_a": "25-08-D-ARCH.ifc",
  "file_b": "25-08-D-GEO.ifc",
  "clash_count": 5,
  "clashes": {
    "clash_001": {
      "a_global_id": "1234...",
      "a_name": "Wall-001",
      "b_global_id": "5678...",
      "b_name": "Column-042",
      "overlap_volume": 0.125,
      "distance": -0.050
    }
  }
}
```


## Dependencies

- **ifcopenshell**: IFC file parsing and clash detection
- **bcf-client**: BCF format export (Not implemented yet)
- **ifcpatch**: IFC file cleaning and manipulation
- **typer**: CLI argument parsing
- **loguru**: Structured logging
- **numpy**: Numerical computations

## License

See LICENSE file for details.

## Credits

- Alignment functionality powered by [IfcPlacementAligner](https://github.com/KaareH/IfcPlacementAligner)
- Clash detection via [ifcopenshell](https://github.com/IfcOpenShell/IfcOpenShell)

