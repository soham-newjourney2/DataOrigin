# DataOrigin

**Know where your data came from, and prove nobody changed it.**

DataOrigin keeps track of research datasets and the machine-learning results built on them. Every version is fingerprinted, every change is traced, every experiment can be re-run, and the important fingerprints are anchored on a blockchain. When someone asks *"which data was this model trained on, and has it changed since?"*, you have an answer you can show, not just one you can say.

---

## Why we built it

It happens on almost every student or research project: a teammate "cleans up" the shared dataset, overwrites the old file, and a week later the model's accuracy has changed and nobody can explain why. Was it the data, the code, or the random seed? The original file is gone, and nothing records what happened in between.

Tools for this exist, but they're spread across version control, experiment trackers and spreadsheets, and they take real setup. We wanted one place that answers three questions without any extra effort:

1. **What is this data, and where did it come from?**
2. **What was done to it, and what depends on it?**
3. **Can we prove that nothing was changed?**

## What it does

- **Never loses a version.** Uploads are stored as immutable files with a SHA-256 fingerprint. A change creates a new version; nothing is ever overwritten.
- **Checks data quality.** Missing values, duplicates, outliers, constant columns and ML-based anomaly detection, rolled into a 0–100 score with plain-English advice ("column `age` has 3 missing values, consider filling with the median").
- **Fixes problems in one click.** Each finding comes with a suggested cleaning step. Applying it creates a new version and keeps the old one.
- **Spots drift.** Compare any two versions to see which columns shifted, which categories appeared or vanished, and which columns were added or removed.
- **Draws the lineage.** An interactive graph shows how every version was derived and which experiments were trained on it. Click any node to see everything downstream that a change would affect.
- **Makes ML reproducible.** Train scikit-learn models with a recorded seed, settings and environment, then press **Reproduce** to show the result comes out the same.
- **Verifies on chain.** Register a dataset version or an experiment, and its fingerprint is written to an Ethereum smart contract. Later, **Verify** tells you whether it's `VERIFIED`, `LOCAL_ONLY`, `TAMPERED` or `NOT_REGISTERED`.
- **Keeps an honest audit log.** Every action is recorded in a hash-chained log, so editing history afterwards is detectable.
- **Writes the report for you.** One click produces a printable audit report with a reproducibility score and a list of anything missing.

The web app comes in two looks, a light **Liquid Glass** theme and a dark **console** theme, switchable from the sidebar.

## How it works, briefly

```
upload ──► quality check ──► versions & lineage ──► ML experiment ──► on-chain fingerprint ──► audit report
```

A FastAPI backend stores metadata in SQLite and files on disk, and it serves the web interface itself, so there's only one app to run. For experiments, DataOrigin hashes the whole setup (data fingerprint, settings, seed, metrics and environment) into a single *commitment*. That commitment, rather than the model file, goes on chain, because saved models aren't byte-for-byte identical even when the model is.

If the blockchain node is down, nothing breaks: registrations wait as `pending` and verification falls back to local fingerprints.

For the full tour of the code, see **[docs/structure.md](docs/structure.md)**.

## Built with

| Part | Technology |
|---|---|
| Backend | Python, FastAPI, SQLAlchemy, SQLite |
| Data and ML | pandas, NumPy, scikit-learn |
| Blockchain | Solidity smart contract, Hardhat (local Ethereum node), web3.py |
| Frontend | Plain HTML, CSS and JavaScript: no framework and no build step |
| Tests | pytest (about 110 tests), Hardhat + Mocha for the contract |

## Getting started

You'll need **Python 3.11+** and **Node.js 22+**. Node is only for the local blockchain; the web app itself doesn't need it.

The commands below are for Windows (PowerShell or Git Bash). On macOS/Linux, use `.venv/bin/python` instead of `.venv/Scripts/python`, and `cp` instead of `copy`.

**1. Install (once)**

```powershell
cd backend
python -m venv .venv
.venv/Scripts/python -m pip install -r requirements.txt
copy .env.example .env

cd ../contracts
npm install
```

**2. Run (three terminals)**

```powershell
# Terminal 1: start the local blockchain (leave it running)
cd contracts
npx hardhat node

# Terminal 2: deploy the contract (repeat after every node restart)
cd contracts
npx hardhat run scripts/deploy.js --network localhost

# Terminal 3: start the app
cd backend
.venv/Scripts/python -m uvicorn app.main:app --port 8000
```

Open **http://localhost:8000**.

**3. Load the demo data (optional, recommended)**

```powershell
cd backend
.venv/Scripts/python scripts/seed_demo.py
```

This builds a complete example. A synthetic student-performance dataset with deliberately planted problems is cleaned into a second version, a model is trained on it and reproduced, and both are registered on chain. The dashboard will then have a real story to show.

> **No blockchain handy?** Just skip terminals 1 and 2. Everything still works; registrations stay `pending` until a node is available, and **Retry pending** on the Verification page catches them up later.

## Take the 3-minute tour

Run the demo seed first, then follow the story: *a teammate quietly overwrote our dataset.*

1. **Dashboard:** integrity status, reproducibility score, quality trend and recent activity, all at a glance.
2. **Datasets → student-performance (demo) → v1 → Quality:** the problems found in the raw data, each with an "Apply suggestion" button. Then look at v2: same data, cleaned, with a much higher score.
3. **Experiments → pass/fail random forest:** metrics, seed and environment, a reproducibility score of 100/100, and **Open report ↗** for the printable audit report.
4. **The overwrite:** on the dataset page, click **Upload new raw version** and pick `demo_data/student_performance_teammate.csv`. Nothing is replaced; it becomes v3. On the **Drift** tab, `attendance_pct` lights up as major drift (the teammate rescaled it to 0–1), and a renamed column shows up as a schema change.
5. **Lineage:** v1 → v2 → v3 with the experiment attached. Click v1 to see everything downstream.
6. **Verification:** verify v2 and get a green **VERIFIED**. Click **Simulate tamper**, verify again, and watch it turn red: **TAMPERED**.
7. **Audit log:** **Verify log integrity** confirms the history is intact, and the tamper itself is right there in the log.

## Running the tests

```powershell
cd backend
.venv/Scripts/python -m pytest -q

cd ../contracts
npx hardhat test
```

The three on-chain tests run only when the Hardhat node is up and the contract is deployed; otherwise they're skipped. The suite also checks every frontend file with `node --check`.

<details>
<summary><strong>Configuration</strong> (<code>backend/.env</code>)</summary>

| Variable | Default | What it does |
|---|---|---|
| `DATABASE_URL` | `sqlite:///./dataorigin.db` | Database location (relative to `backend/`) |
| `STORAGE_DIR` | `../storage` | Where dataset files and trained models are kept |
| `MAX_UPLOAD_MB` | `20` | Upload size limit |
| `CHAIN_ENABLED` | `true` | Set to `false` to run without a blockchain |
| `CHAIN_RPC_URL` | `http://127.0.0.1:8545` | Address of the Hardhat node |
| `CHAIN_DEPLOYMENT_FILE` | `app/chain_deployment.json` | Written by the deploy script |
| `DEMO_MODE` | `true` | Enables the "Simulate tamper" button; turn off outside demos |
| `CORS_ORIGINS` | `http://localhost:8000` | Allowed origins, comma-separated |
| `FRONTEND_DIR` | `../frontend` | The web app served at `/` |

To start completely fresh, stop the app and delete `backend/dataorigin.db` and the `storage/` folder.

</details>

## Honest limitations

This is a hackathon-scale prototype, and we'd rather say so up front:

- **Single user, local only.** No accounts or permissions; data lives in SQLite and local files.
- **The blockchain is a local test chain.** It lives in memory, so restarting it wipes the on-chain records. DataOrigin notices this, doesn't mistake it for tampering, and re-registers everything when the backend starts. A public testnet would make the timestamps independently verifiable.
- **Training runs while you wait.** Four predefined scikit-learn models, small datasets, no custom code. Fine for a demo, not for big workloads.
- **Files up to 20 MB** (CSV, Excel or JSON). Drift analysis is per column.

## The team

<div align="center">
<table>
  <tr>
    <td align="center" width="25%">
      <a href="https://github.com/soham-newjourney2">
        <img src="https://github.com/soham-newjourney2.png?size=200" width="110" alt="Soham Chowdhury">
      </a>
      <h3>Soham Chowdhury</h3>
      <sub><b>Team Lead</b> · Backend &amp; AI support</sub><br><br>
      <a href="https://github.com/soham-newjourney2">
        <img src="https://img.shields.io/badge/GitHub-soham--newjourney2-181717?style=for-the-badge&logo=github&logoColor=white" alt="GitHub @soham-newjourney2" height="22">
      </a>
    </td>
    <td align="center" width="25%">
      <a href="https://github.com/Saswata427">
        <img src="https://github.com/Saswata427.png?size=200" width="110" alt="Saswata Biswas">
      </a>
      <h3>Saswata Biswas</h3>
      <sub>Blockchain</sub><br><br>
      <a href="https://github.com/Saswata427">
        <img src="https://img.shields.io/badge/GitHub-Saswata427-181717?style=for-the-badge&logo=github&logoColor=white" alt="GitHub @Saswata427" height="22">
      </a>
    </td>
    <td align="center" width="25%">
      <a href="https://github.com/srijoyeemisra">
        <img src="https://github.com/srijoyeemisra.png?size=200" width="110" alt="Srijoyee Misra">
      </a>
      <h3>Srijoyee Misra</h3>
      <sub>Frontend</sub><br><br>
      <a href="https://github.com/srijoyeemisra">
        <img src="https://img.shields.io/badge/GitHub-srijoyeemisra-181717?style=for-the-badge&logo=github&logoColor=white" alt="GitHub @srijoyeemisra" height="22">
      </a>
    </td>
    <td align="center" width="25%">
      <a href="https://github.com/blacksayan6">
        <img src="https://github.com/blacksayan6.png?size=200" width="110" alt="Sayan Roy Chowdhury">
      </a>
      <h3>Sayan Roy Chowdhury</h3>
      <sub>AI &amp; ML</sub><br><br>
      <a href="https://github.com/blacksayan6">
        <img src="https://img.shields.io/badge/GitHub-blacksayan6-181717?style=for-the-badge&logo=github&logoColor=white" alt="GitHub @blacksayan6" height="22">
      </a>
    </td>
  </tr>
</table>

<sub>Built with care for reproducible research. Ideas, issues and pull requests are welcome.</sub>
</div>

