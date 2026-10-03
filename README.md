# Nanopore Most Advanced Basecaller (B300 GPU)

**Advanced nanopore basecalling on NVIDIA Blackwell Ultra  is now accessible from your everyday laptop.**

This Archaions fork builds on [Oxford Nanopore Dorado](https://github.com/nanoporetech/dorado). Dorado provides the underlying basecalling engine; the Archaions additions focus on experimental B300 compatibility, GPU configuration, remote processing, and the hosted user workflow.

Archaions brings experimental NVIDIA B300 GPU acceleration to nanopore basecalling, connecting portable sequencing with powerful remote computing. Generate POD5 files locally, then use server-side HAC or SUP basecalling, DNA and RNA modification detection, demultiplexing, and quality control.

**Access the project through this repository, or use the hosted service on the Archaions website: `archaions.com`.**

## Bringing Blackwell Ultra to nanopore sequencing

Dorado's published platform guidance emphasizes A100 and H100 optimization. Archaions extends this workflow to NVIDIA B300, a Blackwell Ultra accelerator from a newer generation than H100 and part of NVIDIA's advanced data-center computing platform. The innovation combines experimental B300 compatibility work, model-specific configuration, and a browser-accessible sequencing workflow. [Dorado platform guidance](https://github.com/nanoporetech/dorado#platforms), [NVIDIA Blackwell Ultra](https://nvidianews.nvidia.com/news/nvidia-blackwell-ultra-ai-factory-platform-paves-way-for-age-of-ai-reasoning).

The implementation pairs pinned basecalling models with checksummed executable builds, preserves modification annotations, and records processing settings in downloadable provenance files. B300 support remains experimental and is specific to the tested configurations.

## MinION sequencing without a local basecalling GPU

**MinION users can capture POD5 files on an affordable laptop without a dedicated basecalling GPU, then upload those files to Archaions for remote processing.**

The laptop must still meet the data-acquisition requirements for the particular MinION device and MinKNOW version, including CPU, memory, USB connectivity, and storage. Oxford Nanopore explicitly permits MinION Mk1D data acquisition using its minimum specifications excluding the GPU when basecalling is performed elsewhere. [MinION data-acquisition requirements](https://nanoporetech.com/document/requirements/minion-mk1d-it-reqs).

1. Connect your MinION to a compatible laptop and start sequencing in MinKNOW.
2. Enable POD5 output and switch off local basecalling.
3. Open the Archaions website and sign in.
4. Select the sequencing kit, HAC or SUP, and any supported modification, demultiplexing, or QC options.
5. Upload completed POD5 files, or connect a sequencing folder in a supported desktop browser.
6. Download the resulting BAM, compressed FASTQ, run details, and requested QC or barcode outputs.

**Your laptop records the signal. The remote B300 server performs the basecalling.**

Live-folder mode uploads completed files in batches. Keep the browser open and the computer awake. Internet upload speed and GPU startup time contribute to the time before results arrive.

## Experimental result: approximately 58% less processing time

An experimental B300 HAC workflow completed the benchmark file in **23.208 seconds**, compared with a **55.67-second historical H100 workflow measurement** — a **58.3% reduction in measured processing time**, rounded to **58%**.

| Measurement | Result |
| --- | --- |
| Historical H100 workflow | 55.67 seconds |
| Experimental B300 workflow | 23.208 seconds |
| Processing-time reduction | 58.3% |
| Basecalling model | DNA HAC v6.0.0 |
| B300 batch size in this experiment | 8,192 chunks |
| Output | 1,003 reads; 16,846,352 bases |
| Sequence and quality comparison | Matched the recorded H100 reference digest |

Calculation: `(55.67 − 23.208) / 55.67 × 100 = 58.3%`.

This is a single-file, historical workflow comparison, including software configuration and processing overhead. Upload, download, queueing, and GPU allocation are excluded. It does not isolate GPU inference speed or establish a universal B300-over-H100 speedup. The subsequently deployed HAC profile uses a batch size of 4,096; the 58% figure refers specifically to the experiment above. See `benchmark-summary.json` for the recorded configuration and source-report identifiers.

## Archaions hosted workflow capabilities

| Capability | Available options |
| --- | --- |
| Basecalling mode | HAC — high accuracy; SUP — super accuracy |
| DNA adenine modification | 6mA in all contexts |
| DNA cytosine modifications | 4mC + 5mC in all contexts; 5mC + 5hmC in CG contexts; or 5mC + 5hmC in all contexts |
| RNA004 modifications | Mode-specific RNA models, including m6A in DRACH contexts, inosine/m6A combinations, cytosine modifications, and pseudouridine combinations |
| Demultiplexing | Supported native and rapid barcode kits; per-barcode BAM files in a ZIP, including unclassified reads |
| Barcode stringency | Optional matching at both ends for supported native barcode kits |
| Trimming | Configurable adapter, primer, and barcode trimming where supported |
| Quality filtering | Minimum read Q-score and minimum read length |
| QC report | Read/base counts, pass/fail counts, read N50, mean read length and quality, quality histogram, and barcode counts |
| Input | POD5 uploads or live-folder monitoring |
| Downloads | BAM, compressed FASTQ, provenance JSON, optional QC JSON, and optional barcode BAM ZIP |

DNA adenine calling can run alongside one cytosine modification model. Kit and mode selection determine the available models; incompatible combinations are rejected. Modification annotations are retained in BAM output.

Supported kit selections currently include **SQK-LSK114, SQK-LSK114-XL, SQK-RAD114, SQK-NBD114-24/96, SQK-RBK114-24/96, SQK-RNA004, and SQK-RNA004-XL**. Input metadata must match the selected kit and an accepted flow-cell/sample-rate combination. The current workflow covers supported Kit 14 DNA at 5 kHz and direct RNA004 at 4 kHz.

## Website and repository access

**For sequencing users:** visit `archaions.com`, open Basecalling, and sign in. The hosted service provides the configured B300 backend; a local CUDA installation or GPU is unnecessary. The current browser upload limit is 2 GiB per POD5 file. Live-folder access requires desktop Chrome or Edge.

**For developers and research teams:** this fork contains the B300 engine changes and Archaions integration. See the [integration guide](archaions/README.md) for source layout, local tests, deployment prerequisites, and build-history limitations. The capabilities above describe the deployed Archaions service; independent deployments require compatible build and model assets. Running the B300 backend requires GPU infrastructure, compatible executable builds, model assets, and service configuration. Cloning source code alone does not provision GPU access. The prototype token-based connector and the hosted account-based service use different authentication methods.

## Validation scope

Functional testing covered 11 GPU cases: six DNA modification combinations across HAC/SUP, four RNA modification combinations, and barcode demultiplexing. Additional checks covered modification-tag preservation, QC counts, barcode read conservation, account isolation, desktop/mobile controls, and an authenticated upload with result downloads.

These checks establish operation on the tested fixtures. They do not establish modification-detection accuracy across biological samples. The RNA functional test used an Oxford Nanopore RNA004 signal fixture with test-only kit/flow-cell metadata corrected to match the explicit RNA004 model used by the upstream test. User POD5 metadata is validated and is never rewritten by the service.

## Upstream Dorado

The original project documentation is preserved in [README.upstream.md](README.upstream.md). Upstream installation instructions describe official Dorado releases; they do not install the custom Archaions B300 build.

Dorado supplies the basecalling and modification models interface, barcode classification, and core processing engine. Archaions supplies the experimental B300 integration and remote workflow described above. Preserve the upstream [LICENCE.txt](LICENCE.txt), copyright notices, and contributor history when maintaining this fork.

Built around Oxford Nanopore Dorado and POD5, with experimental Archaions B300 integration.
