from __future__ import annotations

import argparse
import importlib.util
import shutil
import sys
from pathlib import Path

# Load module with non-standard name using importlib
_spec = importlib.util.spec_from_file_location(
    "clash_module", 
    str(Path(__file__).parent / "src" / "1105_clash.py")
)
_clash_module = importlib.util.module_from_spec(_spec)
sys.modules["clash_module"] = _clash_module
_spec.loader.exec_module(_clash_module)

run_pairwise_clash_detection = _clash_module.run_pairwise_clash_detection
align_ifc_files = _clash_module.align_ifc_files
_clean_aligned_ifc_files = _clash_module._clean_aligned_ifc_files


def main():
    parser = argparse.ArgumentParser(
        description="Align IFC models to common coordinate system and detect clashes between them."
    )
    parser.add_argument(
        "--ifc-dir",
        default="ifc",
        help="Directory containing source IFC files (default: ifc)",
    )
    parser.add_argument(
        "-f",
        "--format",
        choices=["json", "bcf"],
        default="json",
        help="Output format: json or bcf (default: json)",
    )
    parser.add_argument(
        "--out-dir",
        default="clash_outputs",
        help="Output directory for clash detection results (default: clash_outputs)",
    )
    parser.add_argument(
        "--prefix",
        default="Clash",
        help="Output filename prefix (default: Clash)",
    )
    parser.add_argument(
        "--skip-existing",
        action="store_true",
        help="Skip generating outputs that already exist",
    )
    parser.add_argument(
        "--align",
        action="store_true",
        help="Enable alignment step to align IFC files to common coordinate system (default: disabled)",
    )
    parser.add_argument(
        "--keep-aligned",
        action="store_true",
        help="Keep aligned IFC files after clash detection completes (only used with --align)",
    )
    parser.add_argument(
        "--clean-only",
        action="store_true",
        help="Only clean IFC files (remove properties) without running alignment or clash detection",
    )

    args = parser.parse_args()

    # Handle --clean-only option
    if args.clean_only:
        print("\n" + "=" * 60)
        print("CLEANING IFC FILES")
        print("=" * 60)
        success = _clean_aligned_ifc_files(args.ifc_dir)
        if success:
            print("\n✓ IFC files cleaned successfully")
        else:
            print("\n✗ Failed to clean IFC files")
        raise SystemExit(0 if success else 1)

    # Setup directories
    ifc_source_dir = Path(args.ifc_dir)
    aligned_dir = ifc_source_dir / ".aligned"
    working_dir = aligned_dir if args.align else ifc_source_dir

    try:
        # Step 1: Alignment (only if --align is specified)
        if args.align:
            print("\n" + "=" * 60)
            print("STEP 1: IFC ALIGNMENT")
            print("=" * 60)
            success = align_ifc_files(
                ifc_dir=str(ifc_source_dir),
                output_dir=str(aligned_dir),
            )
            if not success:
                print("\n✗ Alignment failed. Aborting clash detection.")
                return False
        else:
            print("\n⊘ Alignment disabled (using files directly from ifc-dir)")

        # Step 1.5: Clean aligned files for clash detection (faster processing)
        if args.align:
            print("\n" + "=" * 60)
            print("STEP 1.5: CLEANING ALIGNED FILES FOR CLASH DETECTION")
            print("=" * 60)
            _clean_aligned_ifc_files(str(aligned_dir))

        # Step 2: Clash Detection
        print("\n" + "=" * 60)
        print("STEP 2: CLASH DETECTION")
        print("=" * 60)
        ok = run_pairwise_clash_detection(
            ifc_dir=str(working_dir),
            out_dir=args.out_dir,
            export_format=args.format,
            prefix=args.prefix,
            skip_existing=args.skip_existing,
            original_ifc_dir=str(ifc_source_dir) if args.align else None,
        )

        # Step 3: Cleanup (if alignment was done and keep-aligned not set)
        if args.align and not args.keep_aligned:
            print("\n" + "=" * 60)
            print("STEP 3: CLEANUP - Removing temporary aligned files")
            print("=" * 60)
            if aligned_dir.exists():
                shutil.rmtree(aligned_dir)
                print("✓ Temporary aligned files removed")
        elif args.align and args.keep_aligned:
            print("\n" + "=" * 60)
            print("STEP 3: INFO - Keeping aligned files in", aligned_dir)
            print("=" * 60)

        print("\n" + "=" * 60)
        if ok:
            print("✓ WORKFLOW COMPLETED SUCCESSFULLY")
        else:
            print("✗ WORKFLOW COMPLETED WITH ERRORS (see above)")
        print("=" * 60 + "\n")

        return ok

    except Exception as e:
        print(f"\n✗ FATAL ERROR: {str(e)}", file=sys.stderr)
        return False


if __name__ == "__main__":
    success = main()
    raise SystemExit(0 if success else 1)
