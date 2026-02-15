# thomas-helper

>[!WARNING]
>This Software has been designed for research purposes only and has not been reviewed or approved by the Food and Drug Administration or by any other agency. YOU ACKNOWLEDGE AND AGREE THAT CLINICAL APPLICATIONS ARE NEITHER RECOMMENDED NOR ADVISED. Any use of the Software is at the sole risk of the party or parties engaged in such use.

`thomas-helper` is a Docker-based command-line workflow for running HIPS-THOMAS segmentation and packaging results for Brainlab import. It is designed to take common clinical/research inputs (Brainlab zip export, DICOM series, or NIfTI), perform bilateral segmentation, and produce a consistent export package without requiring manual file preparation.

The pipeline performs preflight environment checks, detects image contrast class (T1 versus WMn/FGATIR) from metadata, selects the appropriate THOMAS mode, and generates three deliverables: a copied source DICOM series (`source_dicom/`), bilateral segmentation as DICOM-SEG (`dicom_seg/`), and a burned-in DICOM series (`burned_dicom/`) with user-selected nuclei overlays.

## THOMAS

THOMAS (Thalamus Optimized Multi-Atlas Segmentation) is a structural MRI segmentation method for thalamic nuclei. The newer HIPS-THOMAS/sTHOMAS workflow extends THOMAS to support standard T1 images (via WMn-like synthesis) and broader deep grey nuclei outputs. Upstream implementation and release repository: [thalamicseg/sthomas](https://github.com/thalamicseg/sthomas).

## References

1. Su JH, Thomas FT, Kasoff WS, Tourdias T, Choi EY, Rutt BK, Saranathan M. *Thalamus Optimized Multi Atlas Segmentation (THOMAS): fast, fully automated segmentation of thalamic nuclei from structural MRI.* NeuroImage. 2019;194:272-282. DOI: [10.1016/j.neuroimage.2019.03.021](https://doi.org/10.1016/j.neuroimage.2019.03.021).  

2. Vidal JP, Danet L, Peran P, Pariente J, Bach Cuadra M, Zahr NM, Barbeau EJ, Saranathan M. *Robust thalamic nuclei segmentation from T1-weighted MRI using polynomial intensity transformation.* Brain Structure and Function. 2024;229(5):1087-1101. PubMed: [38546872](https://pubmed.ncbi.nlm.nih.gov/38546872/).  

3. Saranathan M, Coligandro G, Hicks T, Patterson D, Vachha B, Hader A, Shazeeb MS, Cacciola A. *Comprehensive Segmentation of Deep Grey Nuclei From Structural MRI Data.* Human Brain Mapping. 2025;46(14). DOI: [10.1002/hbm.70350](https://doi.org/10.1002/hbm.70350).  

## Processing summary

`scripts/thomas-helper` runs a deterministic end-to-end sequence: validate Docker and required images, choose input and burn-in targets, stage and normalize source imaging, run bilateral THOMAS segmentation (or reuse previous segmentation with `--skip-thomas`), resample masks back to source space, generate DICOM-SEG, and create a burned-in DICOM series whose metadata is explicitly renamed with a `-burnedin` suffix for unambiguous identification in Brainlab.

## Prerequisites

- Docker
- Apple Silicon is supported via `--platform=linux/amd64` in the script
- Platform note from current testing:
  - On macOS, this workflow was not reliable under Colima in our tests.
  - On macOS, it ran reliably with Docker Desktop.
  - Other platforms have not been tested yet.

Required images:

- `anagrammarian/sthomas`
- `scitran/dcm2niix`
- `qiicr/dcmqi`
- `biocontainers/plastimatch:v1.7.4dfsg.1-2-deb_cv1`

## Install command on PATH

To run `thomas-helper` from anywhere:

```bash
# Run from the repo root
echo "export PATH=\"$(pwd)/scripts:\$PATH\"" >> ~/.zshrc
source ~/.zshrc
```

Verify:

```bash
which thomas-helper
```

Alternative (works from any current directory):

```bash
echo 'export PATH="/absolute/path/to/thomas-helper/scripts:$PATH"' >> ~/.zshrc
source ~/.zshrc
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
