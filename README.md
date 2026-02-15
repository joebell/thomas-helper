# thomas-helper

Docker-first batch pipeline for HIPS-THOMAS segmentation and Brainlab export packaging.

It supports Brainlab zip exports, DICOM folders, and NIfTI input. It produces:

- `source_dicom/` (copied source series)
- `dicom_seg/` (bilateral DICOM-SEG)
- `burned_dicom/` (source volume with selected nuclei burned in)

## What the script does

`scripts/thomas-helper` runs this workflow:

1. Preflight checks:
   - verifies Docker is reachable
   - verifies required images are present (prompts to pull if missing)
2. Input selection:
   - if no input argument is passed, scans the current directory and prompts you to choose a file
3. Burn-in nuclei selection:
   - prompts up front (or use `--burn-nuclei`)
4. Input staging and format detection:
   - handles zip/tar/folder/NIfTI
   - detects DICOM series and converts to NIfTI if needed
5. Contrast detection from metadata:
   - detects `T1` vs `WMN/FGATIR` from metadata fields (not filename)
   - uses `hipsthomas.sh -t1` for T1, default mode otherwise
6. THOMAS segmentation:
   - runs bilateral HIPS-THOMAS once (or reuses existing results with `--skip-thomas`)
7. Post-processing:
   - resamples bilateral masks to source image space
   - builds DICOM-SEG
   - builds burned-in DICOM with `SeriesDescription` suffixed by `-burnedin`

## Prerequisites

- Docker running locally (Docker Desktop or Colima)
- Apple Silicon is supported via `--platform=linux/amd64` in the script

Required images:

- `anagrammarian/sthomas`
- `scitran/dcm2niix`
- `qiicr/dcmqi`
- `biocontainers/plastimatch:v1.7.4dfsg.1-2-deb_cv1`

## Install command on PATH

To run `thomas-helper` from anywhere:

```bash
echo 'export PATH="/Users/joe/Code/thomas-helper/scripts:$PATH"' >> ~/.zshrc
source ~/.zshrc
```

Verify:

```bash
which thomas-helper
```

## Usage

### Fully interactive

No required arguments:

```bash
thomas-helper
```

It will prompt for:

- input file (if multiple candidates are found)
- burn-in nuclei selection

It auto-generates:

- workdir: `run_<input_slug>`
- output dir: `brainlab_export_<input_slug>`

### Explicit run

```bash
thomas-helper /path/to/input.brainlab.zip \
  --output-dir /path/to/brainlab_export \
  --workdir /path/to/run_thomas_batch \
  --threads 4 \
  --debug
```

### Skip THOMAS and rebuild exports from prior results

```bash
thomas-helper /path/to/input.brainlab.zip \
  --workdir /path/to/existing_workdir \
  --output-dir /path/to/output \
  --skip-thomas --no-clean
```

### Burn-in nuclei selection

Interactive prompt appears by default.

CLI override:

```bash
thomas-helper /path/to/input.brainlab.zip --burn-nuclei AV,CM,VLP
thomas-helper /path/to/input.brainlab.zip --burn-nuclei ALL
```

Supported nuclei catalog:

`THALAMUS, AV, VA, VLa, VLP, VPL, VL, Pul, LGN, MGN, CM, MD-Pf, Hb, MTT, Acc, Cau, Cla, GPe, GPi, Put, RN, GP, Amy, CL, VLPd, VLPv`

## Logs

Run logs are written to:

- `<workdir>/logs/run_YYYYMMDD_HHMMSS.log`

THOMAS container stdout is also captured in:

- `<workdir>/logs/hipsthomas_bilateral.log`

## Output layout

`--output-dir` contains:

- `source_dicom/` original source DICOM series
- `dicom_seg/ALL_lr_seg.dcm` bilateral DICOM-SEG
- `burned_dicom/` burned-in DICOM series

`--workdir` contains intermediates (`all_out`, `all_out_R`, `all_out_full_lr`, metadata, logs).

## Notes

- The script is designed around HIPS-THOMAS container behavior from:
  - [hipsthomasdocker](https://github.com/thalamicseg/hipsthomasdocker/tree/main)
- For Brainlab distinction, burned-in DICOM `SeriesDescription` is automatically set to:
  - `<source_series_description>-burnedin`
