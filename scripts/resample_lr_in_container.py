import os
from pathlib import Path

import nibabel as nib
from nibabel.processing import resample_from_to


def main() -> None:
    # Resolve workspace path from env (fallback to historical default).
    workdir = Path(os.environ.get("THOMAS_WORKDIR", "/repo/run_thomas_batch"))
    left = sorted((workdir / "all_out").glob("*.nii.gz"))
    right = sorted((workdir / "all_out_R").glob("*.nii.gz"))
    out = workdir / "all_out_full_lr"

    # Ensure resampling output is deterministic for each run.
    out.mkdir(parents=True, exist_ok=True)
    for f in out.glob("*.nii.gz"):
        f.unlink()

    target = nib.load(str(next(workdir.glob("*.nii*"))))
    written = 0
    skipped = 0

    # Resample each side's labels into full-source space with nearest-neighbor interpolation.
    for segs, side in ((left, "L"), (right, "R")):
        for seg_path in segs:
            stem = seg_path.stem
            if (not stem) or (not stem[0].isdigit()) or stem.startswith("m") or stem.startswith("san_"):
                skipped += 1
                continue
            img = nib.load(str(seg_path))
            res = resample_from_to(img, target, order=0, cval=0)
            out_path = out / f"{stem}-{side}_full.nii.gz"
            nib.save(res, str(out_path))
            written += 1

    print(f"[resample] left={len(left)} right={len(right)} skipped={skipped} written={written}")


if __name__ == "__main__":
    main()
