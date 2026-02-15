import os
import re
from pathlib import Path

import nibabel as nib
import numpy as np


def parse_nucleus(stem: str) -> str:
    # Example full_lr stem: "2-AV.nii-L_full.nii" or "6_VLPd.nii-R_full.nii".
    base = stem.replace("_full", "").replace(".nii", "")
    m = re.match(r"^\d+[-_](.+?)-[LR]$", base)
    if m:
        return re.sub(r"[^A-Za-z0-9]+", "", m.group(1)).upper()
    return ""


def main() -> None:
    # Resolve workspace path from env (fallback to historical default).
    workdir = Path(os.environ.get("THOMAS_WORKDIR", "/repo/run_thomas_batch"))
    full_lr = workdir / "all_out_full_lr"
    src = next(workdir.glob("*.nii*"))
    out = workdir / "burned" / "burned_av_cm_vlp.nii.gz"
    out.parent.mkdir(parents=True, exist_ok=True)

    img = nib.load(str(src))
    data = np.asarray(img.get_fdata(dtype=np.float32))
    p99 = float(np.nanpercentile(data, 99))
    base = 0.25 * p99
    selected = os.environ.get("BURN_NUCLEI", "AV,CM,VLP")
    nuclei = [re.sub(r"[^A-Za-z0-9]+", "", x).upper() for x in selected.split(",")]
    nuclei = [x for x in nuclei if x]
    nuclei_set = set(nuclei)
    if not nuclei_set:
        raise RuntimeError("BURN_NUCLEI is empty after normalization")

    # Burn selected nuclei bilaterally. Preserve prior AV/CM/VLP relative weighting.
    burned = data.copy()
    touched = {k: 0 for k in nuclei_set}
    for seg in sorted(full_lr.glob("*.nii.gz")):
        nucleus = parse_nucleus(seg.stem)
        if nucleus not in nuclei_set:
            continue
        seg_data = np.asarray(nib.load(str(seg)).get_fdata(dtype=np.float32))
        scale = 0.75 if nucleus in {"AV", "CM"} else 1.0
        vox = int(np.count_nonzero(seg_data > 0))
        touched[nucleus] = touched.get(nucleus, 0) + vox
        burned[seg_data > 0] = burned[seg_data > 0] + (base * scale)

    # Preserve source dtype constraints for DICOM conversion compatibility.
    dtype = img.get_data_dtype()
    if np.issubdtype(dtype, np.integer):
        info = np.iinfo(dtype)
        burned = np.clip(np.rint(burned), info.min, info.max).astype(dtype)
    else:
        burned = burned.astype(dtype)

    nib.save(nib.Nifti1Image(burned, img.affine, img.header), str(out))
    print(f"[burn] out={out}")
    print(f"[burn] selected_nuclei={','.join(nuclei)}")
    print(f"[burn] p99={p99:.3f} base_delta={base:.3f}")
    print("[burn] voxels " + " ".join(f"{k}={touched.get(k, 0)}" for k in sorted(touched)))


if __name__ == "__main__":
    main()
