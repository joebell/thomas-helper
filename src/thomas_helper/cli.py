from __future__ import annotations

import argparse
import os
import platform
import shutil
import subprocess
import sys
import tarfile
import tempfile
import zipfile
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

import nibabel as nib
import numpy as np
import pydicom


FGATIR_HINTS = ("fgatir", "white matter", "wmn", "wm nulled", "wm-nulled")
T1_HINTS = ("mprage", "t1", "spgr", "bravo", "tfl", "ir-spgr")


@dataclass
class Inputs:
    source_type: str
    nifti_path: Path | None = None
    dicom_dir: Path | None = None
    metadata_text: str = ""


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Run HIPS-THOMAS segmentation and build a Brainlab-ready burned-in output."
    )
    parser.add_argument("input_path", type=Path, help="Input file/folder (NIfTI, DICOM folder, or zip/tar).")
    parser.add_argument("--output-dir", type=Path, default=Path("output"), help="Output directory.")
    parser.add_argument(
        "--docker-image",
        default="thalamicseg/hipsthomasdocker",
        help="Docker image that contains hipsthomas.sh.",
    )
    parser.add_argument(
        "--modality",
        choices=("auto", "t1", "wmn"),
        default="auto",
        help="Force modality if auto-detection is wrong.",
    )
    parser.add_argument(
        "--labels",
        default="",
        help="Comma-separated segmentation labels to burn in. If omitted, selection is interactive.",
    )
    parser.add_argument(
        "--non-interactive",
        action="store_true",
        help="If set and --labels is omitted, all segmentations are used.",
    )
    parser.add_argument(
        "--skip-thomas",
        action="store_true",
        help="Skip Docker THOMAS run and reuse existing left/right output in workdir.",
    )
    parser.add_argument(
        "--workdir",
        type=Path,
        default=None,
        help="Working directory. If omitted, a temporary directory is used.",
    )
    parser.add_argument(
        "--keep-workdir",
        action="store_true",
        help="Keep temporary workdir for debugging.",
    )
    args = parser.parse_args()

    args.output_dir.mkdir(parents=True, exist_ok=True)
    owns_workdir = args.workdir is None
    workdir = args.workdir or Path(tempfile.mkdtemp(prefix="thomas_helper_"))
    workdir.mkdir(parents=True, exist_ok=True)

    try:
        staged = stage_input(args.input_path, workdir / "staged")
        inputs = inspect_input(staged)
        if inputs.source_type == "dicom":
            nifti = convert_dicom_to_nifti(inputs.dicom_dir, workdir / "converted")
            source_nifti = nifti
            metadata = inputs.metadata_text + " " + dicom_metadata_snippet(inputs.dicom_dir)
        else:
            source_nifti = inputs.nifti_path
            metadata = inputs.metadata_text

        if not source_nifti:
            raise RuntimeError("No source NIfTI detected.")

        modality = args.modality
        if modality == "auto":
            modality = detect_modality(metadata, source_nifti.name)
            print(f"[info] Modality auto-detected as: {modality}")

        if not args.skip_thomas:
            run_thomas_docker(args.docker_image, workdir, source_nifti, modality)

        segmentations = discover_segmentations(workdir)
        if not segmentations:
            raise RuntimeError("No segmentation files were found after THOMAS run.")

        chosen = choose_segmentations(segmentations, args.labels, args.non_interactive)
        if not chosen:
            raise RuntimeError("No segmentations selected.")

        burned_output = args.output_dir / f"{source_nifti.stem}_burned_20pct.nii.gz"
        combined_mask = args.output_dir / f"{source_nifti.stem}_selected_mask.nii.gz"
        burn_in_segmentations(source_nifti, chosen, burned_output, combined_mask)
        print(f"[ok] Burned output: {burned_output}")
        print(f"[ok] Combined mask: {combined_mask}")
    finally:
        if owns_workdir and not args.keep_workdir:
            shutil.rmtree(workdir, ignore_errors=True)
        elif owns_workdir:
            print(f"[info] Workdir kept at: {workdir}")


def stage_input(input_path: Path, staged_root: Path) -> Path:
    if not input_path.exists():
        raise FileNotFoundError(f"Input path not found: {input_path}")
    staged_root.mkdir(parents=True, exist_ok=True)

    if input_path.is_dir():
        return input_path
    if zipfile.is_zipfile(input_path):
        with zipfile.ZipFile(input_path, "r") as zf:
            zf.extractall(staged_root)
        return staged_root
    if tarfile.is_tarfile(input_path):
        with tarfile.open(input_path, "r:*") as tf:
            tf.extractall(staged_root)
        return staged_root
    return input_path


def inspect_input(path: Path) -> Inputs:
    if path.is_file() and is_nifti(path):
        return Inputs(source_type="nifti", nifti_path=path, metadata_text=path.name.lower())
    if path.is_file() and is_dicom(path):
        return Inputs(source_type="dicom", dicom_dir=path.parent, metadata_text=path.name.lower())

    files = [p for p in path.rglob("*") if p.is_file()]
    nifti_files = [p for p in files if is_nifti(p)]
    dicom_files = [p for p in files if is_dicom(p)]

    if nifti_files:
        nifti = sorted(nifti_files, key=lambda p: p.stat().st_size, reverse=True)[0]
        return Inputs(source_type="nifti", nifti_path=nifti, metadata_text=nifti.name.lower())
    if dicom_files:
        series_dir = choose_dicom_series(dicom_files)
        metadata = " ".join((series_dir.name.lower(), dicom_files[0].name.lower()))
        return Inputs(source_type="dicom", dicom_dir=series_dir, metadata_text=metadata)
    raise RuntimeError(f"No NIfTI or DICOM files found in {path}")


def choose_dicom_series(files: list[Path]) -> Path:
    buckets: dict[Path, int] = {}
    for f in files:
        buckets[f.parent] = buckets.get(f.parent, 0) + 1
    return sorted(buckets.items(), key=lambda kv: kv[1], reverse=True)[0][0]


def is_nifti(path: Path) -> bool:
    name = path.name.lower()
    return name.endswith(".nii") or name.endswith(".nii.gz")


def is_dicom(path: Path) -> bool:
    ext = path.suffix.lower()
    if ext in {".dcm", ".ima"}:
        return True
    try:
        with path.open("rb") as f:
            header = f.read(132)
        return len(header) >= 132 and header[128:132] == b"DICM"
    except OSError:
        return False


def run_cmd(cmd: list[str], cwd: Path | None = None) -> None:
    printable = " ".join(cmd)
    print(f"[run] {printable}")
    subprocess.run(cmd, cwd=cwd, check=True)


def convert_dicom_to_nifti(dicom_dir: Path | None, out_dir: Path) -> Path:
    if dicom_dir is None:
        raise RuntimeError("DICOM directory was not provided.")
    out_dir.mkdir(parents=True, exist_ok=True)

    if shutil.which("dcm2niix"):
        run_cmd(["dcm2niix", "-z", "y", "-o", str(out_dir), str(dicom_dir)])
    elif shutil.which("docker"):
        run_cmd(
            [
                "docker",
                "run",
                "--rm",
                "-v",
                f"{dicom_dir}:/dicom:ro",
                "-v",
                f"{out_dir}:/out",
                "nipy/dcm2niix",
                "-z",
                "y",
                "-o",
                "/out",
                "/dicom",
            ]
        )
    else:
        raise RuntimeError("Neither dcm2niix nor docker is available for DICOM conversion.")

    produced = sorted(out_dir.glob("*.nii*"), key=lambda p: p.stat().st_mtime, reverse=True)
    if not produced:
        raise RuntimeError("DICOM conversion produced no NIfTI files.")
    return produced[0]


def dicom_metadata_snippet(dicom_dir: Path | None) -> str:
    if dicom_dir is None:
        return ""
    files = sorted([p for p in dicom_dir.iterdir() if p.is_file()])
    if not files:
        return ""
    try:
        ds = pydicom.dcmread(str(files[0]), stop_before_pixels=True, force=True)
        values = [
            str(getattr(ds, "SeriesDescription", "")),
            str(getattr(ds, "ProtocolName", "")),
            str(getattr(ds, "SequenceName", "")),
        ]
        return " ".join(values).lower()
    except Exception:
        return ""


def detect_modality(*hints: str) -> str:
    text = " ".join(hints).lower()
    if any(h in text for h in FGATIR_HINTS):
        return "wmn"
    if any(h in text for h in T1_HINTS):
        return "t1"
    return "t1"


def run_thomas_docker(image: str, workdir: Path, source_nifti: Path, modality: str) -> None:
    src_in_workdir = workdir / source_nifti.name
    if src_in_workdir.resolve() != source_nifti.resolve():
        shutil.copy2(source_nifti, src_in_workdir)
    cmd = [
        "docker",
        "run",
        "--rm",
        "-v",
        f"{workdir}:/data",
        "-w",
        "/data",
    ]
    # HIPS-THOMAS docs recommend --user for Linux/WSL, but not macOS/Windows Docker Desktop.
    if platform.system() == "Linux" and hasattr(os, "getuid"):
        cmd.extend(["--user", f"{os.getuid()}:{os.getgid()}"])
    cmd.append(image)
    cmd.extend(["hipsthomas.sh", "-v"])
    if modality == "t1":
        cmd.append("-t1")
    cmd.extend(["-i", src_in_workdir.name])
    run_cmd(cmd)


def discover_segmentations(workdir: Path) -> dict[str, Path]:
    candidates: list[Path] = []
    for side in ("left", "right"):
        side_dir = workdir / side
        if side_dir.exists():
            candidates.extend(side_dir.glob("*.nii*"))

    found: dict[str, Path] = {}
    for p in sorted(candidates):
        name = p.name.lower()
        if "thomasfull" in name or "merged" in name:
            continue
        stem_upper = p.stem.upper()
        is_per_nucleus = ("-" in p.stem) or stem_upper in {"CL_L", "CL_R"}
        if not is_per_nucleus:
            continue
        side = p.parent.name[0].upper() if p.parent.name.lower() in ("left", "right") else "U"
        label = f"{side}:{p.stem}"
        found[label] = p
    return found


def choose_segmentations(
    discovered: dict[str, Path], labels_arg: str, non_interactive: bool
) -> list[Path]:
    if labels_arg.strip():
        wanted = {s.strip() for s in labels_arg.split(",") if s.strip()}
        matches = [p for label, p in discovered.items() if label in wanted or p.stem in wanted]
        if not matches:
            raise RuntimeError(f"No requested labels matched. Available: {', '.join(discovered.keys())}")
        return matches

    if non_interactive:
        return list(discovered.values())

    print("\nAvailable segmentations:")
    labels = list(discovered.keys())
    for i, label in enumerate(labels, start=1):
        print(f"  {i:>2}. {label}")
    raw = input("Select by number (comma-separated), or press Enter for all: ").strip()
    if not raw:
        return list(discovered.values())
    chosen_idx = set()
    for token in raw.split(","):
        token = token.strip()
        if not token:
            continue
        try:
            chosen_idx.add(int(token))
        except ValueError as e:
            raise RuntimeError(f"Invalid selection token: {token}") from e
    out = [discovered[labels[i - 1]] for i in sorted(chosen_idx) if 1 <= i <= len(labels)]
    if not out:
        raise RuntimeError("No valid selections provided.")
    return out


def burn_in_segmentations(
    source_nifti: Path, selected_segmentations: Iterable[Path], out_nifti: Path, out_mask: Path
) -> None:
    img = nib.load(str(source_nifti))
    source_data = np.asarray(img.get_fdata(dtype=np.float32))
    mask = np.zeros(source_data.shape, dtype=bool)

    for seg_path in selected_segmentations:
        seg_img = nib.load(str(seg_path))
        seg_data = np.asarray(seg_img.get_fdata(dtype=np.float32))
        if seg_data.shape != source_data.shape:
            print(f"[warn] Skipping {seg_path.name}: shape mismatch {seg_data.shape} != {source_data.shape}")
            continue
        mask |= seg_data > 0

    if not mask.any():
        raise RuntimeError("No valid segmentation voxels after filtering.")

    bright_delta = 0.2 * float(np.nanmax(source_data))
    burned = source_data.copy()
    burned[mask] = burned[mask] + bright_delta

    original_dtype = img.get_data_dtype()
    if np.issubdtype(original_dtype, np.integer):
        info = np.iinfo(original_dtype)
        burned = np.clip(np.rint(burned), info.min, info.max).astype(original_dtype)
    else:
        burned = burned.astype(original_dtype)

    out_nifti.parent.mkdir(parents=True, exist_ok=True)
    out_mask.parent.mkdir(parents=True, exist_ok=True)
    nib.save(nib.Nifti1Image(burned, img.affine, img.header), str(out_nifti))
    nib.save(nib.Nifti1Image(mask.astype(np.uint8), img.affine, img.header), str(out_mask))


if __name__ == "__main__":
    main()
