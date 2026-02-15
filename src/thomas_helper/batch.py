from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import tarfile
import tempfile
import zipfile
from pathlib import Path
from typing import Iterable

import nibabel as nib
import numpy as np
from nibabel.processing import resample_from_to


DEFAULT_THOMAS_IMAGE = "anagrammarian/sthomas"
DEFAULT_DCM2NIIX_IMAGE = "scitran/dcm2niix"
DEFAULT_DCMQI_IMAGE = "qiicr/dcmqi"
DEFAULT_PLASTIMATCH_IMAGE = "biocontainers/plastimatch:v1.7.4dfsg.1-2-deb_cv1"


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Batch pipeline: THOMAS L/R, DICOM-SEG, burned-in DICOM, and source DICOM export."
    )
    parser.add_argument("input_path", type=Path, help="Input zip/tar, DICOM folder, or NIfTI file.")
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=Path("brainlab_export"),
        help="Output directory (burned_dicom, dicom_seg, source_dicom).",
    )
    parser.add_argument(
        "--workdir",
        type=Path,
        default=Path("run_thomas_batch"),
        help="Working directory (kept for debugging).",
    )
    parser.add_argument("--threads", type=int, default=4, help="THOMAS threads (-p).")
    parser.add_argument("--skip-thomas", action="store_true", help="Reuse existing THOMAS outputs.")
    parser.add_argument(
        "--thomas-image",
        default=DEFAULT_THOMAS_IMAGE,
        help="Docker image for THOMAS (default anagrammarian/sthomas).",
    )
    parser.add_argument(
        "--dcm2niix-image",
        default=DEFAULT_DCM2NIIX_IMAGE,
        help="Docker image for dcm2niix.",
    )
    parser.add_argument(
        "--dcmqi-image",
        default=DEFAULT_DCMQI_IMAGE,
        help="Docker image for dcmqi.",
    )
    parser.add_argument(
        "--plastimatch-image",
        default=DEFAULT_PLASTIMATCH_IMAGE,
        help="Docker image for plastimatch.",
    )
    parser.add_argument(
        "--burn-nuclei",
        default="AV,CM,VLP",
        help="Comma-separated nuclei for burned-in image (default AV,CM,VLP).",
    )
    parser.add_argument(
        "--burn-base",
        type=float,
        default=0.25,
        help="Base burn-in intensity as fraction of p99 (default 0.25).",
    )
    parser.add_argument(
        "--burn-av-cm-scale",
        type=float,
        default=0.75,
        help="Scale AV/CM relative to base (default 0.75).",
    )
    args = parser.parse_args()

    args.output_dir.mkdir(parents=True, exist_ok=True)
    workdir = args.workdir
    workdir.mkdir(parents=True, exist_ok=True)

    staged = stage_input(args.input_path, workdir / "staged")
    source_dicom = find_dicom_dir(staged)
    source_nifti = find_nifti(staged)

    if source_dicom:
        source_dicom_out = args.output_dir / "source_dicom"
        copy_source_dicom(source_dicom, source_dicom_out)
    else:
        source_dicom_out = None

    if source_nifti is None:
        if not source_dicom:
            raise RuntimeError("No DICOM or NIfTI input detected.")
        source_nifti = convert_dicom_to_nifti(source_dicom, workdir / "converted", args.dcm2niix_image)

    if not args.skip_thomas:
        run_thomas_lr(args.thomas_image, workdir, source_nifti, args.threads)

    full_lr_dir = workdir / "all_out_full_lr"
    full_lr_dir.mkdir(parents=True, exist_ok=True)
    resample_lr_outputs(workdir / "all_out", workdir / "all_out_R", source_nifti, full_lr_dir)

    if source_dicom_out:
        dicom_seg_dir = args.output_dir / "dicom_seg"
        dicom_seg_dir.mkdir(parents=True, exist_ok=True)
        dicom_seg_path = build_dicom_seg(
            full_lr_dir,
            source_dicom_out,
            workdir / "rt_dicomseg",
            dicom_seg_dir,
            args.dcmqi_image,
        )
        print(f"[ok] DICOM-SEG: {dicom_seg_path}")

    burned_nifti = workdir / "burned" / "burned_av_cm_vlp.nii.gz"
    burned_nifti.parent.mkdir(parents=True, exist_ok=True)
    burn_nuclei = {n.strip().upper() for n in args.burn_nuclei.split(",") if n.strip()}
    burn_in_nuclei(
        source_nifti,
        full_lr_dir,
        burn_nuclei,
        burned_nifti,
        base_fraction=args.burn_base,
        av_cm_scale=args.burn_av_cm_scale,
    )

    if source_dicom_out:
        burned_dicom_dir = args.output_dir / "burned_dicom"
        burned_dicom_dir.mkdir(parents=True, exist_ok=True)
        convert_nifti_to_dicom(
            burned_nifti,
            source_dicom_out,
            burned_dicom_dir,
            args.plastimatch_image,
        )
        print(f"[ok] Burned DICOM: {burned_dicom_dir}")


def run_cmd(cmd: list[str]) -> None:
    printable = " ".join(cmd)
    print(f"[run] {printable}")
    subprocess.run(cmd, check=True)


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


def find_nifti(root: Path) -> Path | None:
    if root.is_file() and is_nifti(root):
        return root
    files = [p for p in root.rglob("*") if p.is_file() and is_nifti(p)]
    if not files:
        return None
    return sorted(files, key=lambda p: p.stat().st_size, reverse=True)[0]


def find_dicom_dir(root: Path) -> Path | None:
    if root.is_file() and is_dicom(root):
        return root.parent
    files = [p for p in root.rglob("*") if p.is_file() and is_dicom(p)]
    if not files:
        return None
    buckets: dict[Path, int] = {}
    for f in files:
        buckets[f.parent] = buckets.get(f.parent, 0) + 1
    return sorted(buckets.items(), key=lambda kv: kv[1], reverse=True)[0][0]


def copy_source_dicom(source_dir: Path, out_dir: Path) -> None:
    out_dir.mkdir(parents=True, exist_ok=True)
    for item in source_dir.iterdir():
        if item.is_file():
            shutil.copy2(item, out_dir / item.name)


def convert_dicom_to_nifti(dicom_dir: Path, out_dir: Path, image: str) -> Path:
    out_dir.mkdir(parents=True, exist_ok=True)
    run_cmd(
        [
            "docker",
            "run",
            "--rm",
            "-v",
            f"{dicom_dir}:/dicom:ro",
            "-v",
            f"{out_dir}:/out",
            image,
            "-z",
            "y",
            "-o",
            "/out",
            "/dicom",
        ]
    )
    produced = sorted(out_dir.glob("*.nii*"), key=lambda p: p.stat().st_mtime, reverse=True)
    if not produced:
        raise RuntimeError("DICOM conversion produced no NIfTI files.")
    return produced[0]


def run_thomas_lr(image: str, workdir: Path, source_nifti: Path, threads: int) -> None:
    input_in_workdir = workdir / source_nifti.name
    if input_in_workdir.resolve() != source_nifti.resolve():
        shutil.copy2(source_nifti, input_in_workdir)

    base_cmd = [
        "docker",
        "run",
        "--rm",
        "-v",
        f"{workdir}:/data",
        "-w",
        "/data",
        image,
        "THOMAS.py",
        f"/data/{input_in_workdir.name}",
        "ALL",
        "-a",
        "v2",
        "-p",
        str(threads),
        "-v",
    ]

    run_cmd(base_cmd + ["--output_path", "/data/all_out"])
    run_cmd(base_cmd + ["-R", "--output_path", "/data/all_out_R"])


def resample_lr_outputs(left_dir: Path, right_dir: Path, target_nifti: Path, out_dir: Path) -> None:
    if not left_dir.exists() or not right_dir.exists():
        raise RuntimeError("Missing THOMAS outputs (all_out or all_out_R).")

    target_img = nib.load(str(target_nifti))

    for side_dir, side_tag in ((left_dir, "L"), (right_dir, "R")):
        for seg_path in sorted(side_dir.glob("*.nii.gz")):
            stem = seg_path.stem
            if not stem or not stem[0].isdigit():
                continue
            if stem.startswith("m") or stem.startswith("san_"):
                continue
            out_path = out_dir / f"{stem}-{side_tag}_full.nii.gz"
            resample_label(seg_path, target_img, out_path)


def resample_label(seg_path: Path, target_img: nib.Nifti1Image, out_path: Path) -> None:
    seg_img = nib.load(str(seg_path))
    resampled = resample_from_to(seg_img, target_img, order=0, cval=0)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    nib.save(resampled, str(out_path))


def burn_in_nuclei(
    source_nifti: Path,
    full_lr_dir: Path,
    nuclei: set[str],
    out_nifti: Path,
    base_fraction: float,
    av_cm_scale: float,
) -> None:
    img = nib.load(str(source_nifti))
    source_data = np.asarray(img.get_fdata(dtype=np.float32))
    p99 = float(np.nanpercentile(source_data, 99))
    base_delta = base_fraction * p99

    burned = source_data.copy()

    for seg_path in sorted(full_lr_dir.glob("*.nii.gz")):
        nucleus = parse_nucleus(seg_path.stem)
        if nucleus not in nuclei:
            continue
        seg_img = nib.load(str(seg_path))
        seg_data = np.asarray(seg_img.get_fdata(dtype=np.float32))
        if seg_data.shape != source_data.shape:
            continue
        scale = av_cm_scale if nucleus in {"AV", "CM"} else 1.0
        burned[seg_data > 0] = burned[seg_data > 0] + (base_delta * scale)

    original_dtype = img.get_data_dtype()
    if np.issubdtype(original_dtype, np.integer):
        info = np.iinfo(original_dtype)
        burned = np.clip(np.rint(burned), info.min, info.max).astype(original_dtype)
    else:
        burned = burned.astype(original_dtype)

    out_nifti.parent.mkdir(parents=True, exist_ok=True)
    nib.save(nib.Nifti1Image(burned, img.affine, img.header), str(out_nifti))


def parse_nucleus(stem: str) -> str:
    if "-" in stem:
        nucleus = stem.split("-", 1)[1]
    elif "_" in stem:
        nucleus = stem.split("_", 1)[1]
    else:
        nucleus = stem
    return nucleus.upper()


def build_dicom_seg(
    full_lr_dir: Path,
    source_dicom_dir: Path,
    workdir: Path,
    output_dir: Path,
    dcmqi_image: str,
) -> Path:
    workdir.mkdir(parents=True, exist_ok=True)
    image_list = workdir / "all_lr_image_list.txt"
    seg_attrs = workdir / "segment_attributes_all_lr.json"
    output_dcm = output_dir / "ALL_lr_seg.dcm"

    dicom_files = sorted([p for p in source_dicom_dir.iterdir() if p.is_file()])
    if not dicom_files:
        raise RuntimeError("No source DICOM files found for DICOM-SEG.")
    image_list.write_text(
        "\n".join(f"/data/{p.relative_to(workdir.parent)}" for p in dicom_files)
    )

    seg_files = sorted([p for p in full_lr_dir.glob("*.nii.gz")])
    if not seg_files:
        raise RuntimeError("No resampled segmentations found for DICOM-SEG.")

    seg_attrs.write_text(json.dumps(build_segment_attributes(seg_files), indent=2))
    seg_list = ",".join(f"/data/{p.relative_to(workdir.parent)}" for p in seg_files)

    run_cmd(
        [
            "docker",
            "run",
            "--rm",
            "-v",
            f"{workdir.parent}:/data",
            dcmqi_image,
            "itkimage2segimage",
            "--inputImageList",
            f"/data/{image_list.relative_to(workdir.parent)}",
            "--inputSegmentations",
            seg_list,
            "--outputDICOM",
            f"/data/{output_dcm.relative_to(workdir.parent)}",
            "--segmentAttributes",
            f"/data/{seg_attrs.relative_to(workdir.parent)}",
        ]
    )
    return output_dcm


def build_segment_attributes(seg_files: Iterable[Path]) -> dict[str, object]:
    entries = []
    for seg in seg_files:
        desc = seg.stem.replace("_full", "")
        entries.append(
            [
                {
                    "labelID": 1,
                    "SegmentDescription": desc,
                    "SegmentAlgorithmType": "AUTOMATIC",
                    "SegmentAlgorithmName": "HIPS-THOMAS",
                    "SegmentedPropertyCategoryCodeSequence": {
                        "CodeValue": "T-D0050",
                        "CodingSchemeDesignator": "SRT",
                        "CodeMeaning": "Tissue",
                    },
                    "SegmentedPropertyTypeCodeSequence": {
                        "CodeValue": "T-A0100",
                        "CodingSchemeDesignator": "SRT",
                        "CodeMeaning": "Brain",
                    },
                    "AnatomicRegionSequence": {
                        "CodeValue": "T-A0100",
                        "CodingSchemeDesignator": "SRT",
                        "CodeMeaning": "Brain",
                    },
                }
            ]
        )
    return {"segmentAttributes": entries, "seriesDescription": "THOMAS ALL LR SEG"}


def convert_nifti_to_dicom(
    nifti_path: Path,
    source_dicom_dir: Path,
    output_dir: Path,
    plastimatch_image: str,
) -> None:
    output_dir.mkdir(parents=True, exist_ok=True)
    run_cmd(
        [
            "docker",
            "run",
            "--rm",
            "-v",
            f"{nifti_path.parent}:/nifti",
            "-v",
            f"{source_dicom_dir}:/dicom:ro",
            "-v",
            f"{output_dir}:/out",
            plastimatch_image,
            "plastimatch",
            "convert",
            "--input",
            f"/nifti/{nifti_path.name}",
            "--output-dicom",
            "/out",
            "--referenced-ct",
            "/dicom",
        ]
    )


if __name__ == "__main__":
    main()
