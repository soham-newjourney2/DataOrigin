# DataOrigin

Provenance and reproducibility for research datasets and ML experiments.

You upload a dataset, run a quality analysis, and save each change as an immutable version. DataOrigin then traces the lineage and runs the ML experiments. It registers the data and experiment hashes on chain, so anyone can verify them later, and produces a reproducibility report.

Every dataset version is stored with its SHA-256 hash, and every action goes into a hash-chained audit log. Each experiment records its data hash, configuration, seed, metrics and environment. Hashes are registered on a local Ethereum node (Hardhat), and when the chain is unavailable, verification falls back to the local hashes.

The web UI has two themes, switchable from the sidebar: a light "Liquid Glass" theme and a dark console theme.

## Requirements

- Python 3.11+ (built and tested on 3.13)
- Node.js 22+ (for the local Hardhat chain only; the frontend needs no Node)

Commands below are for Windows PowerShell. On macOS/Linux, use `.venv/bin/python` instead of `.venv/Scripts/python` and `cp` instead of `copy`.

## One-time setup

```powershell
cd backend
python -m venv .venv
.venv/Scripts/python -m pip install -r requirements.txt
copy .env.example .env

cd ../contracts
npm install
```

## Run (3 terminals)

| Terminal | Directory | Command | Notes |
|---|---|---|---|
| 1. Chain node | `contracts/` | `npx hardhat node` | Leave it running. The node is **in-memory**. |
| 2. Deploy | `contracts/` | `npx hardhat run scripts/deploy.js --network localhost` | Run this again after **every** node restart. It writes `backend/app/chain_deployment.json`. |
| 3. Backend + UI | `backend/` | `.venv/Scripts/python -m uvicorn app.main:app --port 8000` | Then open **http://localhost:8000**. |

Optional: run `.venv/Scripts/python scripts/seed_demo.py` in `backend/`. It loads a ready-made demo story (see below) and you can run it while the server is up.

No chain? Set `CHAIN_ENABLED=false` in `backend/.env`, or just don't start the node. Registrations are then saved as `pending`, and verification reports `LOCAL_ONLY`. Once the node is up and deployed, click **Retry pending** on the Verification page.

## Demo (about 3 minutes)

The story: *"A teammate quietly overwrote our dataset, and now nobody can explain why the model changed."*

1. **Seed** (before the demo): run `.venv/Scripts/python scripts/seed_demo.py` with the node deployed. This creates a synthetic student-performance dataset with planted issues (missing values, duplicates, impossible attendance values, a constant column). It then builds the following:
   - **v1**, the raw upload, with a quality score of about 76
   - **v2**, the cleaned version, with a quality score of about 92
   - a random-forest experiment on v2 and a reproduction that matches it
   - on-chain registrations of v2 and the experiment

   It also writes the teammate's version to `demo_data/student_performance_teammate.csv`.
2. **Datasets → student-performance (demo):** open v1's **Quality** tab and show the findings and the "Apply suggestion" buttons. Click v2 to show the higher score.
3. **Experiments → pass/fail random forest:** show the metrics, the seed and the environment, the reproducibility score of 100/100, and **Open report ↗**, which is a printable audit report.
4. **The teammate overwrites the dataset:** on the dataset page, click **Upload new raw version** and choose `demo_data/student_performance_teammate.csv`.
   - The new v3 is hashed, versioned and scored. Nothing is silently replaced.
   - On the **Drift** tab (v3 against v2), `attendance_pct` shows **major** drift, because the teammate rescaled it to 0–1. `parental_education` also drifts, because of a new `unknown` category. Under schema changes, the tab shows `sleep_hours` renamed to `sleep` and the `school` column, which v2 had dropped, back again.
5. **Lineage:** the graph shows v1 → v2 → v3. Click v1 to see the **downstream impact** list, which includes v3 and the experiments.
6. **Verification:** pick v2 and click **Verify** to get **VERIFIED**. Then click **Simulate tamper** (demo mode only) and verify again to get **TAMPERED**.
7. **Audit:** click **Verify log integrity**. The log is intact, and the tamper itself is recorded as an event.

## Tests

```powershell
cd backend
.venv/Scripts/python -m pytest -q --tb=short   # the 3 on-chain tests skip unless the node is running and deployed
cd ../contracts
npx hardhat test
```

All frontend JS passes `node --check` (this is part of the pytest suite).

## Configuration (`backend/.env`)

| Variable | Default | Meaning |
|---|---|---|
| `DATABASE_URL` | `sqlite:///./dataorigin.db` | SQLite file. Relative paths resolve against `backend/`. |
| `STORAGE_DIR` | `../storage` | Immutable dataset files and saved models |
| `MAX_UPLOAD_MB` | `20` | Upload size limit |
| `CHAIN_ENABLED` | `true` | `false` turns on the local-only fallback |
| `CHAIN_RPC_URL` | `http://127.0.0.1:8545` | Hardhat node |
| `CHAIN_DEPLOYMENT_FILE` | `app/chain_deployment.json` | Written by the deploy script |
| `DEMO_MODE` | `true` | Enables `POST /api/dev/tamper/{id}` and the "Simulate tamper" button |
| `CORS_ORIGINS` | `http://localhost:8000` | Comma-separated list |
| `FRONTEND_DIR` | `../frontend` | Static UI served at `/` |

To start from a clean slate, stop the backend and delete `backend/dataorigin.db` and `storage/`.

## Known limitations

- **Local, single-user prototype.** There is no authentication, no roles and no sharing. SQLite and local files only.
- **The chain is a local Hardhat node and lives in memory.** Restarting it wipes on-chain records. Redeploy afterwards, and the contract comes back at the same address. DataOrigin recognises the reset because the registration transactions are gone too, and it never reports a reset as `TAMPERED`. When the backend starts, it re-registers ("re-anchors") those records automatically. If only the node restarted, click **Retry pending** on the Verification page instead. The re-anchored on-chain timestamp is the new registration time.
- **Experiments run synchronously** inside the request. There are four predefined scikit-learn algorithms, and you can't upload your own code or models.
- **Model files aren't byte-reproducible.** Experiments are therefore verified through a *commitment hash* over the data hash, configuration, metrics and environment, not through the model file itself.
- **The git commit in `env_json`** is the repository's `HEAD` when the run happened. Uncommitted changes aren't captured.
- **Drift is per column** (PSI with 10 quantile bins, plus category sets). There is no multivariate drift or significance testing.
- **Files are limited to 20 MB**, CSV, XLSX or JSON. Transforms always save their output as CSV.
- **Tamper simulation** is a demo feature. Set `DEMO_MODE=false` for anything beyond a demo.
- **The UI has no automated browser tests.** Its pages are checked with `node --check` and through API-level journey tests.

## How it's built

[docs/structure.md](docs/structure.md) walks through the architecture: the big picture, the repository layout, how each flow works, the smart contract and the trade-offs.
