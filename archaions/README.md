# Archaions B300 integration

This directory contains the remote basecalling workflow, historical build scripts, and aggregate experimental evidence for this fork. The hosted service is available through the Archaions website, `archaions.com`.

## What is included

| Path | Purpose |
| --- | --- |
| `modal_b300_basecall.py` | B300 GPU worker with pinned model/build identities and BAM/FASTQ export |
| `service/server.py` | Account-scoped resumable uploads, POD5 validation, job status, and downloads |
| `service/remote_worker.py` | Persistent dispatcher between the API and Modal GPU worker |
| `service/job_queue.py` | SQLite claims, heartbeats, and expired-lease recovery |
| `service/run_options.py` | Shared kit, modification, barcode, and QC validation |
| `service/run_outputs.py` | BAM filtering and QC report generation |
| `service/web/` | Browser upload, live-folder monitoring, run settings, and downloads |
| `build_tools/` | Build scripts and patches from the B300 compatibility experiments |
| `evidence/` | Aggregate GPU checks, kernel preservation audit, and timing results |

The root source changes add an optional FlashAttention disable switch, preserve explicit CUDA architecture choices, select the cuBLAS library bundled with external LibTorch, and apply model-specific B300 batch profiles. Explicit batch sizes and other GPU architectures keep their existing behavior.

## Local tests

Use Python 3.12 for the service checks:

```bash
python3 -m venv .venv
. .venv/bin/activate
python -m pip install -r archaions/requirements.txt
cd archaions/service
python -m unittest -v test_public test_options test_migration
```

These tests use temporary databases and a mocked account identity. They exercise access control, settings validation and persistence, duplicate identity, database migration, QC filtering, and modification-tag preservation. They do not allocate a GPU or contact the production account service.

`check_options.py` additionally tests the browser controls with Playwright and mocked API responses. Install Playwright and its Chromium browser in a development environment to run it. GPU evidence supplied in `evidence/` records prior tests of the hosted builds; it is not a claim that a newly compiled fork binary has already passed those tests.

## Deployment prerequisites

The service is an integration for an existing account system. It requires an account endpoint that accepts an `archaions_session` cookie and returns an authenticated user's email as JSON, or an adaptation of `resolve_account()` to your own identity provider.

Configure the API and dispatcher using environment variables:

| Variable | Meaning |
| --- | --- |
| `BASECALL_DATA` | Shared writable directory for SQLite, uploads, results, and GPU readiness |
| `BASECALL_TOKEN` | Required legacy startup setting of at least 24 characters; it does not authenticate public API requests |
| `BASECALL_ACCOUNT_URL` | Account-validation URL; defaults to the Archaions loopback account endpoint |
| `BASECALL_ALLOWED_ORIGINS` | Comma-separated browser origins authorized to submit jobs |
| `BASECALL_MAX_FILE_BYTES` | Per-file upload limit, default 2 GiB |
| `BASECALL_QUOTA_BYTES` | Aggregate raw-input reservation limit |

The account service must issue the session cookie. Serve the web files over HTTPS and proxy `/basecalling-public-api/` to this API. No credentials, account database, sequencing data, private endpoints, GPU binary archives, or model weights are supplied in this repository.

After provisioning the build/model assets described below and authenticating Modal in your own account:

```bash
modal deploy archaions/modal_b300_basecall.py
cd archaions/service
python -m uvicorn server:app --host 127.0.0.1 --port 8093 --workers 1
# In another terminal, with the same environment:
python remote_worker.py
```

The API advertises availability from `BASECALL_DATA/gpu-status.json`. Set B300 availability only after validating the deployed binaries and supported models in your environment. The expected structure is `{"B300":{"available":true,"models":["hac","sup"]}}`.

The worker uses Modal volumes named `archaions-basecalling-test` for temporary jobs/model cache and `archaions-dorado-b300-build` for executables and libraries. These names refer to resources in the deploying account. Preload the pinned DNA models and validated executable paths declared in `modal_b300_basecall.py`; matching modification models and RNA004 models are downloaded when needed. The dispatcher and GPU worker must use the same Modal account and volume names.

## Build history and reproducibility boundaries

The build scripts document the tested CUDA 13.1.1 / shared LibTorch 2.9.0+cu130 port based on upstream commit `8b8fc5d36a9c0baab262a743cb175b5878e38ca6`.

The historical sequence was:

1. `build_port.py`: create the upstream CMake build and dependency cache.
2. `build_official_torch.py`: use the checksummed official shared LibTorch archive with optional prebuilt FlashAttention disabled.
3. `build_koi_b300_full.py`: reassemble the embedded Koi PTX to obtain SM103 images and intermediate link inputs.
4. `build_tuned4096_core.py`: produce the historical HAC batch-profile build.
5. `build_multimodel_core_v2.py`: link the model-profile build against LibTorch's bundled cuBLAS.
6. `build_sup_preserved.py`: add SM103 images while verifying that the original native Koi images are preserved.

These are historical, stateful scripts, with `/build` dependencies supplied by a Modal volume. The initial scripts deliberately fetch the pinned upstream commit; later compilation steps load the fork's `CudaCaller.cpp`. They are not a fresh-clone, one-command build system. Intermediate archives from the full-Koi experiment must not be treated as a generally validated release: replacing native H100 images produced incorrect SUP output during the experiments. The native-preserved build is the worker path for SUP and extended modification/RNA calls.

The service checks exact executable hashes before running. A rebuilt binary can have a different identity; compare its sequence/quality output and modification behavior against controls before updating the expected hashes. Copying build scripts or changing a checksum alone does not establish GPU compatibility. Preserve the upstream and dependency licenses when distributing any derived artifacts.

## Benchmark and validation evidence

The root `benchmark-summary.json` records the experimental 23.208-second B300 workflow versus the historical 55.67-second H100 workflow: a 58.3% reduction for that comparison. The 8,192-chunk benchmark is distinct from the subsequently deployed 4,096-chunk HAC profile. This is workflow timing rather than an isolated GPU inference comparison.

`evidence/gpu-options.json` records 11 successful functional cases: DNA modification combinations under HAC/SUP, RNA modification combinations, and barcode splitting. RNA uses a fixed batch size of 128. The RNA fixture's test-only metadata correction and biological-validation limits are described in the root README.
