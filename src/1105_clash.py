"""IFC Clash Detection and Alignment Script

What this script does:
1) Aligns all IFC files to a common coordinate system (optional).
2) Loads all IFC files in the given directory up front (once).
3) Runs clash detection for each unique file-pair.
4) Writes one output per pair named: Clash_<FileA>_<FileB>.(json|bcfzip)

This avoids paying IFC open/load costs repeatedly during the pairwise loop.
"""

from __future__ import annotations

import argparse
import json
import logging
import re
import sys
from pathlib import Path

import ifcopenshell
from ifcclash.ifcclash import Clasher, ClashSettings


# Try to import BcfGenerator first, but fall back to SimpleBcfGenerator
BCF_GENERATOR_AVAILABLE = False
BCF_GENERATOR_TYPE = ""
BCF_IMPORT_ERROR = ""

try:
    from BcfGenerator import generate_bcf_from_ifc_elements

    BCF_GENERATOR_AVAILABLE = True
    BCF_GENERATOR_TYPE = "full"
except ImportError as e:
    BCF_IMPORT_ERROR = str(e)
    try:
        from SimpleBcfGenerator import export_clashes_to_bcf

        BCF_GENERATOR_AVAILABLE = True
        BCF_GENERATOR_TYPE = "simple"
    except ImportError as e2:
        BCF_GENERATOR_AVAILABLE = False
        BCF_IMPORT_ERROR = f"Full: {str(e)}, Simple: {str(e2)}"


def setup_logging(verbose: bool = False) -> logging.Logger:
    """Setup logging with optional verbosity control."""
    # Remove any existing handlers to avoid conflicts
    for handler in logging.root.handlers[:]:
        logging.root.removeHandler(handler)

    level = logging.INFO if verbose else logging.WARNING
    logging.basicConfig(
        level=level,
        format="%(levelname)s - %(message)s",
        force=True,  # Force reconfiguration
    )
    return logging.getLogger("ClashDetection")


def find_ifc_files(directory: str) -> list[str]:
    ifc_dir = Path(directory)
    if not ifc_dir.exists():
        raise FileNotFoundError(f"Directory not found: {directory}")

    ifc_files = sorted(ifc_dir.glob("*.ifc"))
    if not ifc_files:
        raise FileNotFoundError(f"No IFC files found in {directory}")

    return [str(f.absolute()) for f in ifc_files]


def _clean_ifc_for_clash_detection(ifc_file) -> None:
    """Remove property sets and other non-geometric data from IFC file.

    This significantly speeds up clash detection by keeping only geometry.
    Modifies the ifc_file in-place.

    Args:
        ifc_file: ifcopenshell.file object to clean
    """
    # Remove all IfcPropertySet instances
    property_sets = ifc_file.by_type("IfcPropertySet")
    for pset in property_sets:
        try:
            ifc_file.remove(pset)
        except Exception:
            pass

    # Remove all IfcQuantitySet instances
    quantity_sets = ifc_file.by_type("IfcQuantitySet")
    for qset in quantity_sets:
        try:
            ifc_file.remove(qset)
        except Exception:
            pass

    # Remove all IfcRelDefinesByProperties relationships
    relations = ifc_file.by_type("IfcRelDefinesByProperties")
    for rel in relations:
        try:
            ifc_file.remove(rel)
        except Exception:
            pass

    # Remove all IfcMaterial instances (not needed for clash detection)
    materials = ifc_file.by_type("IfcMaterial")
    for mat in materials:
        try:
            ifc_file.remove(mat)
        except Exception:
            pass

    # Remove all IfcMaterialDefinitionRepresentation instances
    mat_defs = ifc_file.by_type("IfcMaterialDefinitionRepresentation")
    for mat_def in mat_defs:
        try:
            ifc_file.remove(mat_def)
        except Exception:
            pass


def _safe_stem(path: str) -> str:
    """Make a filename-safe stem for output naming."""
    stem = Path(path).stem
    stem = re.sub(r"[^A-Za-z0-9._-]+", "_", stem)
    return stem.strip("_") or "Unnamed"


def _print_progress(current: int, total: int, label: str = "") -> None:
    """Print a simple progress indicator."""
    percentage = int((current / total) * 100) if total > 0 else 0
    bar_length = 30
    filled = int((current / total) * bar_length) if total > 0 else 0
    bar = "█" * filled + "░" * (bar_length - filled)

    label_str = f" {label}" if label else ""
    print(
        f"\r  [{bar}] {percentage:3d}% ({current}/{total}){label_str}",
        end="",
        flush=True,
    )

    if current == total:
        print()  # New line when complete


def export_pair_json(clash_set: dict, output_file: str) -> None:
    """Export a single clash-set to JSON (without IFC objects)."""

    def _strip_ifc(sources: list[dict]) -> list[dict]:
        return [{k: v for k, v in src.items() if k != "ifc"} for src in sources]

    # Avoid copying ifcopenshell.file objects (not deepcopy-safe).
    cleaned_set = dict(clash_set)
    cleaned_set["a"] = _strip_ifc(clash_set.get("a", []))
    if "b" in clash_set:
        cleaned_set["b"] = _strip_ifc(clash_set.get("b", []))

    with open(output_file, "w", encoding="utf-8") as f:
        json.dump([cleaned_set], f, indent=4)


def export_to_bcf(clasher: Clasher, ifc_files: list[str], output_file: str) -> None:
    """Export clash results to BCF format (bcfzip) with colored visualization.

    Creates a BCF file where:
    - Clashing elements are shown in RED (FF0000)
    - All other elements have NO COLOR (neutral/grey display)
    - Viewpoint shows an overview of the entire model
    """

    if not clasher.clash_sets or not clasher.clash_sets[0].get("clashes"):
        return

    try:
        import zipfile
        import uuid
        from pathlib import Path
        import tempfile
        import os

        # Get clash information
        clash_set = clasher.clash_sets[0]
        clashes_dict = clash_set.get("clashes", {})
        clash_name = clash_set.get("name", "Clashes")

        # Collect all clashing element GUIDs
        clashing_guids = set()
        for clash_id, clash_data in clashes_dict.items():
            a_guid = clash_data.get("a_global_id")
            b_guid = clash_data.get("b_global_id")
            if a_guid:
                clashing_guids.add(a_guid)
            if b_guid:
                clashing_guids.add(b_guid)

        if not clashing_guids:
            return

        # Create a temporary directory for BCF contents
        with tempfile.TemporaryDirectory() as tmpdir:
            tmpdir_path = Path(tmpdir)

            # Create BCF structure directories
            (tmpdir_path / "Topics").mkdir(exist_ok=True)

            # Create a unique topic GUID
            topic_guid = str(uuid.uuid4())
            topic_dir = tmpdir_path / "Topics" / topic_guid
            topic_dir.mkdir(exist_ok=True)

            # Create markup.bcf (XML file with topic metadata)
            markup_content = f'''<?xml version="1.0" encoding="UTF-8"?>
<Topic Guid="{topic_guid}" TopicType="Clash" Priority="High">
    <Title>{clash_name}</Title>
    <Description>Clash Detection Results: {len(clashing_guids)} elements clashing</Description>
    <CreationDate>2026-06-03T00:00:00</CreationDate>
    <CreatedBy>Clash Detector</CreatedBy>
</Topic>'''

            (topic_dir / "markup.bcf").write_text(markup_content)

            # Create viewpoint.bcfv (Visualization settings with colors)
            viewpoint_guid = str(uuid.uuid4())
            viewpoint_content = f'''<?xml version="1.0" encoding="UTF-8"?>
<VisualizationInfo Guid="{viewpoint_guid}">
    <Components>
        <ViewSetupHints OpeningsVisible="true" SpacesVisible="true" ShadedMode="true" />
'''

            # Add clashing elements in RED (RGB: 1.0, 0.0, 0.0)
            for guid in clashing_guids:
                viewpoint_content += f'''        <Component Guid="{guid}" Visible="true">
            <Color A="1.0" R="1.0" G="0.0" B="0.0" />
        </Component>
'''

            viewpoint_content += """    </Components>
    <Viewpoint>
        <Camera>
            <CameraViewPoint>0 0 100</CameraViewPoint>
            <CameraDirection>0 0 -1</CameraDirection>
            <CameraUpVector>0 1 0</CameraUpVector>
        </Camera>
        <Lines />
        <ClippingPlanes />
    </Viewpoint>
</VisualizationInfo>"""

            (topic_dir / "viewpoint.bcfv").write_text(viewpoint_content)

            # Create project.bcf
            project_content = """<?xml version="1.0" encoding="UTF-8"?>
<Project name="Clash Detection" />"""

            (tmpdir_path / "project.bcf").write_text(project_content)

            # Create bcf.version
            version_content = """<?xml version="1.0" encoding="UTF-8"?>
<Version VersionId="3.0" />"""

            (tmpdir_path / "bcf.version").write_text(version_content)

            # Create the BCF ZIP file
            with zipfile.ZipFile(output_file, "w", zipfile.ZIP_DEFLATED) as bcf_zip:
                for root, dirs, files in os.walk(tmpdir_path):
                    for file in files:
                        file_path = Path(root) / file
                        arcname = file_path.relative_to(tmpdir_path)
                        bcf_zip.write(file_path, arcname)

    except Exception as e:
        # If BCF export fails, silently continue - clash detection already succeeded
        pass


def align_ifc_files(
    ifc_dir: str,
    output_dir: str,
) -> bool:
    """Align IFC files using IfcPlacementAligner.

    Args:
        ifc_dir: Directory containing source IFC files
        output_dir: Directory to save aligned IFC files

    Returns:
        True if alignment succeeds, False otherwise
    """
    try:
        # Import IfcPlacementAligner from local folder
        sys.path.insert(
            0, str(Path(__file__).parent.parent / "IfcPlacementAligner" / "src")
        )
        from ifcplacementaligner import modelAligner

        ifc_source = Path(ifc_dir)
        if not ifc_source.exists():
            print(f"✗ Source IFC directory not found: {ifc_dir}")
            return False

        ifc_files = sorted(ifc_source.glob("*.ifc"))
        if not ifc_files:
            print(f"✗ No IFC files found in {ifc_dir}")
            return False

        print(f"\nFound {len(ifc_files)} IFC file(s) to align:")
        for f in ifc_files:
            print(f"  • {f.name}")

        # Create output directory
        output_path = Path(output_dir)
        output_path.mkdir(parents=True, exist_ok=True)

        # Step 1: Gather models
        print(f"\n[1/3] Gathering model information...")
        models_json = output_path / "ifc_models.json"
        try:
            modelAligner.gather_ifc_models(
                [str(ifc_source)],
                str(models_json),
                base_path=str(ifc_source),
                prompt_func=None,  # Auto-confirm (force mode)
            )
            print(f"      ✓ Gathered {len(ifc_files)} models")
        except Exception as e:
            print(f"      ✗ Failed to gather models: {str(e)}")
            return False

        # Step 2: Analyze transformations
        print(f"[2/3] Analyzing grid alignment...")
        transformations_json = output_path / "transformations.json"
        try:
            transformations = modelAligner.analyze_and_determine_transformations(
                str(models_json),
                str(transformations_json),
                base_path=str(ifc_source),
                prompt_func=None,  # Auto-confirm
            )
            valid = sum(1 for t in transformations if t.is_valid)
            print(
                f"      ✓ Analysis complete: {valid}/{len(transformations)} models have valid grids"
            )

            # Show which models failed
            failed = [t for t in transformations if not t.is_valid]
            if failed:
                print(f"\n      ⚠ Failed to analyze {len(failed)} model(s):")
                for t in failed:
                    print(f"        • {Path(t.file_path).name}: {t.message}")
                print()
        except Exception as e:
            print(f"      ✗ Failed to analyze transformations: {str(e)}")
            return False

        # Step 3: Apply transformations
        print(f"[3/3] Applying transformations...")
        try:
            results = modelAligner.apply_transformations(
                str(transformations_json),
                str(output_path),
                base_path=str(ifc_source),
                copy_untransformed=True,
                prompt_func=None,
                force_ifc=True,
            )
            success = sum(1 for _, s, _ in results if s)
            print(
                f"      ✓ Transformation complete: {success}/{len(results)} models transformed"
            )

            # Show which files succeeded and which failed
            if success < len(results):
                failed_results = [(p, s, m) for p, s, m in results if not s]
                print(f"\n      ⚠ Failed to transform {len(failed_results)} model(s):")
                for path, _, msg in failed_results:
                    print(f"        • {Path(path).name}: {msg}")
                print()
        except Exception as e:
            print(f"      ✗ Failed to apply transformations: {str(e)}")
            return False

        # Clean up intermediate JSON files
        models_json.unlink(missing_ok=True)
        transformations_json.unlink(missing_ok=True)

        print(f"✓ Alignment completed. Aligned files saved to {output_dir}")
        return True

    except ImportError as e:
        print(f"✗ Failed to import IfcPlacementAligner: {str(e)}")
        return False
    except Exception as e:
        print(f"✗ Alignment failed: {str(e)}")
        return False


def _clean_aligned_ifc_files(ifc_dir: str) -> bool:
    """Clean aligned IFC files to remove property sets and keep only geometry.

    This is done in-place on the .aligned IFC files to speed up clash detection.

    Args:
        ifc_dir: Directory containing IFC files to clean

    Returns:
        True if successful, False otherwise
    """
    try:
        ifc_path = Path(ifc_dir)
        ifc_files = sorted(ifc_path.glob("*.ifc"))

        if not ifc_files:
            return True

        print(f"\nCleaning {len(ifc_files)} IFC file(s) for faster clash detection:")

        for i, ifc_file_path in enumerate(ifc_files, 1):
            try:
                print(
                    f"  [{i}/{len(ifc_files)}] {ifc_file_path.name}...",
                    end=" ",
                    flush=True,
                )
                ifc_file = ifcopenshell.open(str(ifc_file_path))
                _clean_ifc_for_clash_detection(ifc_file)
                output_path = str(ifc_file_path)
                ifc_file.write(output_path)
                print("✓")
            except Exception as e:
                print(f"⚠ ({str(e)})")
                # Continue with other files even if one fails
                pass

        return True

    except Exception as e:
        print(f"✗ Failed to clean IFC files: {str(e)}")
        return False


def preload_ifcs(clasher: Clasher, ifc_files: list[str]) -> None:
    """Load all IFCs once, before any clash processing starts."""
    total = len(ifc_files)
    for i, p in enumerate(ifc_files, 1):
        # ifcclash.Clasher caches by path in clasher.ifcs
        clasher.load_ifc(p)
        _print_progress(i, total, "Loading IFC files")


def run_pairwise_clash_detection(
    *,
    ifc_dir: str,
    out_dir: str,
    export_format: str,
    prefix: str,
    skip_existing: bool,
    original_ifc_dir: str = None,
) -> bool:
    try:
        ifc_files = find_ifc_files(ifc_dir)

        print(f"\nFound {len(ifc_files)} IFC file(s):")
        for f in ifc_files:
            print(f"  • {Path(f).name}")

        # If we have original files (from alignment), map them to working files
        original_file_map = {}
        if original_ifc_dir and original_ifc_dir != ifc_dir:
            original_files = find_ifc_files(original_ifc_dir)
            # Create a mapping from filename to full path for both directories
            for work_file in ifc_files:
                work_name = Path(work_file).name
                for orig_file in original_files:
                    if Path(orig_file).name == work_name:
                        original_file_map[work_file] = orig_file
                        break

        out_path = Path(out_dir)
        out_path.mkdir(parents=True, exist_ok=True)

        # Setup logger for ClashSettings (required by ifcclash)
        logger = setup_logging()
        settings = ClashSettings()
        settings.logger = logger
        clasher = Clasher(settings)

        # Load ALL files up front with progress bar
        print(f"\nLoading IFC files:")
        preload_ifcs(clasher, ifc_files)

        if len(ifc_files) < 2:
            print("⚠ Need at least two IFC files to run clash detection.")
            return True

        # Calculate total pairs
        total_pairs = sum(1 for i in range(len(ifc_files)) for _ in ifc_files[i + 1 :])
        pair_count = 0
        total_clashes = 0

        print(f"\nRunning clash detection on {total_pairs} file pair(s):")

        for i, file_a in enumerate(ifc_files):
            for file_b in ifc_files[i + 1 :]:
                pair_count += 1

                a = _safe_stem(file_a)
                b = _safe_stem(file_b)
                ext = "bcfzip" if export_format.lower() == "bcf" else "json"
                output_file = str(out_path / f"{prefix}_{a}_{b}.{ext}")

                if skip_existing and Path(output_file).exists():
                    _print_progress(pair_count, total_pairs, f"[skipped] {a} vs {b}")
                    continue

                try:
                    clash_set = {
                        "name": f"{a} vs {b}",
                        "a": [
                            {"file": file_a}
                        ],  # Run clash detection on cleaned/aligned files
                        "b": [
                            {"file": file_b}
                        ],  # Run clash detection on cleaned/aligned files
                        "mode": "collision",
                        "allow_touching": False,
                    }

                    clasher.clash_sets = [clash_set]
                    clasher.clash()

                    # Now update the file references to the original files for JSON/BCF output
                    if original_file_map and clash_set.get("clashes"):
                        clash_set["a"] = [
                            {"file": original_file_map.get(file_a, file_a)}
                        ]
                        clash_set["b"] = [
                            {"file": original_file_map.get(file_b, file_b)}
                        ]

                    if export_format.lower() == "json":
                        export_pair_json(clasher.clash_sets[0], output_file)
                    elif export_format.lower() == "bcf":
                        export_to_bcf(clasher, [file_a, file_b], output_file)

                    pair_clashes = len(clasher.clash_sets[0].get("clashes", {}) or {})
                    total_clashes += pair_clashes
                    _print_progress(
                        pair_count, total_pairs, f"{a} vs {b} ({pair_clashes} clashes)"
                    )

                except Exception as e:
                    _print_progress(pair_count, total_pairs, f"✗ {a} vs {b}")
                    print(f"\n✗ Error processing pair {a} vs {b}: {str(e)}")

        print(f"\n✓ Clash detection complete:")
        print(f"  • Pairs processed: {pair_count}/{total_pairs}")
        print(f"  • Total clashes found: {total_clashes}")

        return True

    except FileNotFoundError as e:
        print(f"✗ {str(e)}")
        return False
    except Exception as e:
        print(f"✗ Error: {str(e)}")
        return False
