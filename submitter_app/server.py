#!/usr/bin/env python3
import argparse
import json
import mimetypes
import os
import re
import shutil
import subprocess
from datetime import datetime, timezone
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, unquote, urlparse


APP_DIR = Path(__file__).resolve().parent
STATIC_DIR = APP_DIR / "static"
AF3_ROOT = APP_DIR.parent
INPUTS_DIR = AF3_ROOT / "inputs"
OUTPUTS_DIR = AF3_ROOT / "outputs"
MODEL_PATH_FILE = AF3_ROOT / "path_to_model.txt"

CANONICAL_AA = set("ACDEFGHIKLMNPQRSTVWY")


def utc_now_iso():
    return datetime.now(timezone.utc).isoformat()


def read_json(path, default=None):
    try:
        with path.open("r", encoding="utf-8") as handle:
            return json.load(handle)
    except FileNotFoundError:
        return default


def write_json(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as handle:
        json.dump(data, handle, indent=2)
        handle.write("\n")


def read_model_dir():
    try:
        lines = MODEL_PATH_FILE.read_text(encoding="utf-8").splitlines()
    except FileNotFoundError as exc:
        raise ValueError(
            f"Missing {MODEL_PATH_FILE}. Create it with the absolute path to your AF3 model weights."
        ) from exc

    for line in lines:
        value = line.strip()
        if value and not value.startswith("#"):
            return Path(os.path.expandvars(value)).expanduser()
    raise ValueError(
        f"{MODEL_PATH_FILE} is empty. Add the absolute path to your AF3 model weights."
    )


def sanitize_job_name(name):
    name = (name or "").strip()
    if not name:
        raise ValueError("Job name is required.")
    cleaned = re.sub(r"[^A-Za-z0-9_.-]+", "_", name).strip("_.-")
    if not cleaned:
        raise ValueError("Job name must contain at least one letter or number.")
    return cleaned[:80]


def chain_id(index):
    letters = "ABCDEFGHIJKLMNOPQRSTUVWXYZ"
    result = ""
    index += 1
    while index:
        index, remainder = divmod(index - 1, 26)
        result = letters[remainder] + result
    return result


def make_job_id(job_name):
    base_name = sanitize_job_name(job_name)
    stamp = datetime.now().strftime("%y%m%d")
    hour_minute = datetime.now().strftime("%H%M")
    base = f"{stamp}_{base_name}_{hour_minute}"
    candidate = base
    counter = 2
    while (
        (INPUTS_DIR / candidate).exists()
        or (OUTPUTS_DIR / candidate).exists()
        or (AF3_ROOT / candidate).exists()
    ):
        candidate = f"{base}_{counter:02d}"
        counter += 1
    return candidate


def parse_job_id(job_id):
    parts = (job_id or "").split("_")
    if len(parts) < 3:
        raise ValueError("Could not parse the timestamped job folder name.")
    stamp = parts[0]
    time_stamp = parts[-1]
    job_name = "_".join(parts[1:-1])
    if not re.fullmatch(r"\d{6}", stamp) or not re.fullmatch(r"\d{4}", time_stamp):
        raise ValueError("Could not parse the timestamped job folder name.")
    return stamp, job_name, time_stamp


def validate_and_build_af3_json(payload):
    job_name = (payload.get("job_name") or "").strip()
    sanitize_job_name(job_name)

    raw_seeds = payload.get("seeds")
    if isinstance(raw_seeds, str):
        seed_parts = [part for part in re.split(r"[\s,]+", raw_seeds.strip()) if part]
    elif isinstance(raw_seeds, list):
        seed_parts = raw_seeds
    else:
        seed_parts = []

    seeds = []
    for raw_seed in seed_parts:
        try:
            seed = int(raw_seed)
        except (TypeError, ValueError):
            raise ValueError("Seeds must be comma- or space-separated integers.")
        if seed < 0:
            raise ValueError("Seeds must be zero or positive integers.")
        seeds.append(seed)
    if not seeds:
        seeds = [1]

    chains = payload.get("chains") or []
    if not chains:
        raise ValueError("Add at least one protein chain.")

    sequence_entries = []
    expanded_chains = []
    invalid_messages = []
    next_chain = 0

    for chain_index, chain in enumerate(chains, start=1):
        label = (chain.get("label") or f"Chain {chain_index}").strip()
        sequence = re.sub(r"\s+", "", (chain.get("sequence") or "")).upper()
        try:
            copies = int(chain.get("copies") or 1)
        except (TypeError, ValueError):
            raise ValueError(f"{label}: copies must be a whole number.")
        if copies < 1:
            raise ValueError(f"{label}: copies must be at least 1.")
        if copies > 99:
            raise ValueError(f"{label}: copies must be 99 or fewer.")
        if not sequence:
            raise ValueError(f"{label}: sequence is required.")

        invalid_chars = sorted(set(sequence) - CANONICAL_AA)
        if invalid_chars:
            invalid_messages.append(
                f"{label}: invalid amino acid character(s): {', '.join(invalid_chars)}"
            )
            continue

        for copy_number in range(copies):
            cid = chain_id(next_chain)
            next_chain += 1
            sequence_entries.append({"protein": {"id": cid, "sequence": sequence}})
            expanded_chains.append(
                {
                    "id": cid,
                    "label": label,
                    "copy": copy_number + 1,
                    "sequence_length": len(sequence),
                }
            )

    if invalid_messages:
        raise ValueError(" ".join(invalid_messages))

    return {
        "af3_json": {
            "name": job_name,
            "sequences": sequence_entries,
            "modelSeeds": seeds,
            "dialect": "alphafold3",
            "version": 1,
        },
        "chains": expanded_chains,
        "seeds": seeds,
        "job_name": job_name,
    }


def slurm_script(job_id, job_name, input_dir, output_dir, job_dir):
    root = AF3_ROOT.resolve()
    weights = read_model_dir()
    safe_slurm_name = re.sub(r"[^A-Za-z0-9_-]+", "_", f"af3_{job_name}")[:120]
    return f"""#!/bin/bash
#SBATCH -N 1
#SBATCH -n 16
#SBATCH -p mit_normal_gpu
#SBATCH -G 1
#SBATCH --job-name={safe_slurm_name}
#SBATCH --output={job_dir.resolve()}/slurm-%j.out
#SBATCH --error={job_dir.resolve()}/slurm-%j.err

set -euo pipefail

module load apptainer

DATABASES_DIR=/orcd/datasets/001/alphafold3
IMAGE_PATH=/orcd/software/community/001/container_images/alphafold3/20260318/alphafold3.sif
MODEL_DIR={weights}
WORKDIR={root}
INPUT_DIR={input_dir.resolve()}
OUTPUT_DIR={output_dir.resolve()}

mkdir -p "$OUTPUT_DIR"

apptainer exec \\
    --bind "$DATABASES_DIR":/root/public_databases \\
    --bind "$MODEL_DIR":/root/models \\
    --bind "$INPUT_DIR":/root/af_input \\
    --bind "$OUTPUT_DIR":/root/af_output \\
    --nv \\
    "$IMAGE_PATH" \\
    python /app/alphafold/run_alphafold.py \\
    --input_dir=/root/af_input \\
    --model_dir=/root/models \\
    --output_dir=/root/af_output \\
    --db_dir=/root/public_databases
"""


def submit_job(payload):
    built = validate_and_build_af3_json(payload)
    job_id = make_job_id(built["job_name"])
    job_dir = INPUTS_DIR / job_id
    input_dir = job_dir
    output_dir = OUTPUTS_DIR / job_id
    json_path = input_dir / "fold_input.json"
    script_path = job_dir / "run_alphafold3.slurm"

    job_dir.mkdir(parents=True, exist_ok=False)
    output_dir.mkdir(parents=True, exist_ok=False)

    write_json(json_path, built["af3_json"])
    script_path.write_text(
        slurm_script(job_id, built["job_name"], input_dir, output_dir, job_dir),
        encoding="utf-8",
    )
    script_path.chmod(0o755)

    metadata = {
        "job_id": job_id,
        "job_name": built["job_name"],
        "created_at": utc_now_iso(),
        "updated_at": utc_now_iso(),
        "status": "created",
        "slurm_job_id": None,
        "input_json": str(json_path),
        "input_dir": str(input_dir),
        "output_dir": str(output_dir),
        "job_dir": str(job_dir),
        "slurm_script": str(script_path),
        "seeds": built["seeds"],
        "chains": built["chains"],
        "submission_stdout": "",
        "submission_stderr": "",
    }
    write_json(job_dir / "metadata.json", metadata)

    sbatch = shutil.which("sbatch")
    if not sbatch:
        metadata["status"] = "submission_failed"
        metadata["updated_at"] = utc_now_iso()
        metadata["submission_stderr"] = "Could not find sbatch on PATH."
        write_json(job_dir / "metadata.json", metadata)
        return metadata, HTTPStatus.SERVICE_UNAVAILABLE

    result = subprocess.run(
        [sbatch, str(script_path)],
        cwd=str(AF3_ROOT),
        text=True,
        capture_output=True,
        timeout=30,
        check=False,
    )
    metadata["submission_stdout"] = result.stdout.strip()
    metadata["submission_stderr"] = result.stderr.strip()
    metadata["updated_at"] = utc_now_iso()
    if result.returncode != 0:
        metadata["status"] = "submission_failed"
        write_json(job_dir / "metadata.json", metadata)
        return metadata, HTTPStatus.BAD_GATEWAY

    match = re.search(r"Submitted batch job\s+(\d+)", result.stdout)
    metadata["slurm_job_id"] = match.group(1) if match else None
    metadata["status"] = "submitted"
    write_json(job_dir / "metadata.json", metadata)
    return metadata, HTTPStatus.CREATED


def discover_output_files(output_dir):
    output = Path(output_dir)
    if not output.exists():
        return {"structure": None, "summary": None, "confidence": None}

    structures = []
    summaries = []
    confidences = []
    for path in output.rglob("*"):
        if not path.is_file():
            continue
        lower = path.name.lower()
        if lower.endswith((".cif", ".mmcif", ".pdb")):
            structures.append(path)
        elif lower.endswith(".json") and "summary" in lower and "confidence" in lower:
            summaries.append(path)
        elif lower.endswith(".json") and "confidence" in lower:
            confidences.append(path)

    def pick(paths):
        if not paths:
            return None
        paths.sort(key=lambda item: (len(item.parts), item.name))
        return str(paths[0])

    return {
        "structure": pick(structures),
        "summary": pick(summaries),
        "confidence": pick(confidences),
    }


def run_status_command(args):
    if not shutil.which(args[0]):
        return None
    try:
        return subprocess.run(
            args,
            text=True,
            capture_output=True,
            timeout=5,
            check=False,
        )
    except (subprocess.SubprocessError, OSError):
        return None


def status_from_slurm(slurm_job_id):
    if not slurm_job_id:
        return None

    squeue = run_status_command(["squeue", "-h", "-j", str(slurm_job_id), "-o", "%T"])
    if squeue and squeue.returncode == 0 and squeue.stdout.strip():
        state = squeue.stdout.strip().splitlines()[0].upper()
        if state in {"PENDING", "RUNNING", "CONFIGURING", "COMPLETING"}:
            return "running"
        return state.lower()

    sacct = run_status_command(
        ["sacct", "-n", "-j", str(slurm_job_id), "--format=State", "-P"]
    )
    if sacct and sacct.returncode == 0 and sacct.stdout.strip():
        states = [line.split("|")[0].strip().upper() for line in sacct.stdout.splitlines()]
        if any(state.startswith("COMPLETED") for state in states):
            return "completed"
        if any(
            state.startswith(("FAILED", "CANCELLED", "TIMEOUT", "OUT_OF_MEMORY", "PREEMPTED"))
            for state in states
        ):
            return "failed"
    return None


def load_job(job_dir):
    metadata_path = job_dir / "metadata.json"
    metadata = read_json(metadata_path)
    if not metadata:
        return None

    output_files = discover_output_files(metadata.get("output_dir", ""))
    slurm_status = status_from_slurm(metadata.get("slurm_job_id"))
    status = metadata.get("status", "unknown")

    if slurm_status:
        status = slurm_status
    elif output_files["structure"] and output_files["summary"]:
        status = "completed"
    elif status == "submitted":
        status = "running"

    if status != metadata.get("status"):
        metadata["status"] = status
        metadata["updated_at"] = utc_now_iso()
        write_json(metadata_path, metadata)

    metadata["output_files"] = output_files
    metadata["has_viewer"] = status == "completed" and bool(output_files["structure"])
    return metadata


def list_jobs():
    jobs = []
    if not INPUTS_DIR.exists():
        return jobs
    for item in INPUTS_DIR.iterdir():
        if not item.is_dir():
            continue
        job = load_job(item)
        if job:
            jobs.append(job)
    jobs.sort(key=lambda job: job.get("created_at", ""), reverse=True)
    return jobs


def safe_job(job_id):
    if not re.fullmatch(r"[A-Za-z0-9_.-]+", job_id or ""):
        return None
    job_dir = INPUTS_DIR / job_id
    if not job_dir.exists() or not job_dir.is_dir():
        return None
    return job_dir


def safe_existing_dir(path, root):
    path = Path(path)
    try:
        resolved = path.resolve()
        resolved.relative_to(root.resolve())
    except (OSError, ValueError):
        return None
    if not resolved.exists() or not resolved.is_dir():
        return None
    return resolved


def find_job_dir(identifier):
    exact = safe_job(identifier)
    if exact:
        return exact
    if not INPUTS_DIR.exists():
        return None
    for item in INPUTS_DIR.iterdir():
        if not item.is_dir():
            continue
        metadata = read_json(item / "metadata.json", {})
        if metadata.get("job_id") == identifier or metadata.get("job_name") == identifier:
            return item
    return None


def delete_job(identifier, payload=None):
    payload = payload or {}
    job_dir = None
    output_dir = None

    input_dir = payload.get("input_dir")
    if input_dir:
        job_dir = safe_existing_dir(input_dir, INPUTS_DIR)
    if not job_dir:
        job_dir = find_job_dir(identifier)
    if not job_dir:
        return None

    metadata = read_json(job_dir / "metadata.json", {})
    job_id = metadata.get("job_id") or job_dir.name

    requested_output_dir = payload.get("output_dir") or metadata.get("output_dir")
    if requested_output_dir:
        output_dir = safe_existing_dir(requested_output_dir, OUTPUTS_DIR)
    if not output_dir:
        output_dir = safe_existing_dir(OUTPUTS_DIR / job_dir.name, OUTPUTS_DIR)
    if not output_dir and job_id != job_dir.name:
        output_dir = safe_existing_dir(OUTPUTS_DIR / job_id, OUTPUTS_DIR)

    deleted = []
    if output_dir:
        shutil.rmtree(output_dir)
        deleted.append(str(output_dir))
    shutil.rmtree(job_dir)
    deleted.append(str(job_dir))
    return {"job_id": job_id, "deleted": deleted}


def replace_bytes_in_files(root, replacements):
    if not root or not root.exists():
        return
    byte_replacements = [
        (old.encode("utf-8"), new.encode("utf-8"))
        for old, new in replacements.items()
        if old and old != new
    ]
    if not byte_replacements:
        return
    for path in root.rglob("*"):
        if not path.is_file():
            continue
        try:
            data = path.read_bytes()
        except OSError:
            continue
        updated = data
        for old, new in byte_replacements:
            updated = updated.replace(old, new)
        if updated != data:
            path.write_bytes(updated)


def rename_paths_under(root, replacements):
    if not root or not root.exists():
        return
    paths = sorted(root.rglob("*"), key=lambda item: len(item.parts), reverse=True)
    for path in paths:
        new_name = path.name
        for old, new in replacements.items():
            if old and old != new:
                new_name = new_name.replace(old, new)
        if new_name == path.name:
            continue
        target = path.with_name(new_name)
        if target.exists():
            raise ValueError(f"Cannot rename because {target} already exists.")
        path.rename(target)


def rename_job(identifier, payload=None):
    payload = payload or {}
    new_job_name = sanitize_job_name(payload.get("new_job_name"))

    job_dir = None
    input_dir = payload.get("input_dir")
    if input_dir:
        job_dir = safe_existing_dir(input_dir, INPUTS_DIR)
    if not job_dir:
        job_dir = find_job_dir(identifier)
    if not job_dir:
        return None

    metadata = read_json(job_dir / "metadata.json", {})
    old_job_id = metadata.get("job_id") or job_dir.name
    old_job_name = metadata.get("job_name")
    try:
        stamp, parsed_job_name, time_stamp = parse_job_id(job_dir.name)
    except ValueError:
        stamp, parsed_job_name, time_stamp = parse_job_id(old_job_id)
    old_job_name = old_job_name or parsed_job_name

    if new_job_name == old_job_name:
        return {"job": load_job(job_dir), "renamed": []}

    new_job_id = f"{stamp}_{new_job_name}_{time_stamp}"
    new_input_dir = INPUTS_DIR / new_job_id
    new_output_dir = OUTPUTS_DIR / new_job_id
    if new_input_dir.exists() and new_input_dir != job_dir:
        raise ValueError(f"Input folder already exists for {new_job_id}.")
    if new_output_dir.exists():
        raise ValueError(f"Output folder already exists for {new_job_id}.")

    requested_output_dir = payload.get("output_dir") or metadata.get("output_dir")
    output_dir = safe_existing_dir(requested_output_dir, OUTPUTS_DIR) if requested_output_dir else None
    if not output_dir:
        output_dir = safe_existing_dir(OUTPUTS_DIR / job_dir.name, OUTPUTS_DIR)
    if not output_dir and old_job_id != job_dir.name:
        output_dir = safe_existing_dir(OUTPUTS_DIR / old_job_id, OUTPUTS_DIR)

    replacements = {
        old_job_id: new_job_id,
        old_job_name: new_job_name,
        str(job_dir): str(new_input_dir),
    }
    if output_dir:
        replacements[str(output_dir)] = str(new_output_dir)

    replace_bytes_in_files(job_dir, replacements)
    if output_dir:
        replace_bytes_in_files(output_dir, replacements)
        rename_paths_under(output_dir, replacements)
        output_dir.rename(new_output_dir)
    rename_paths_under(job_dir, replacements)
    job_dir.rename(new_input_dir)

    metadata_path = new_input_dir / "metadata.json"
    metadata = read_json(metadata_path, {})
    metadata["job_id"] = new_job_id
    metadata["job_name"] = new_job_name
    metadata["updated_at"] = utc_now_iso()
    metadata["input_json"] = str(new_input_dir / "fold_input.json")
    metadata["input_dir"] = str(new_input_dir)
    metadata["output_dir"] = str(new_output_dir)
    metadata["job_dir"] = str(new_input_dir)
    metadata["slurm_script"] = str(new_input_dir / "run_alphafold3.slurm")
    write_json(metadata_path, metadata)

    input_json_path = new_input_dir / "fold_input.json"
    af3_json = read_json(input_json_path)
    if af3_json:
        af3_json["name"] = new_job_name
        write_json(input_json_path, af3_json)

    return {
        "job": load_job(new_input_dir),
        "renamed": [str(job_dir), str(output_dir) if output_dir else None],
    }


def confidence_payload(job):
    files = job.get("output_files") or discover_output_files(job.get("output_dir", ""))
    summary = read_json(Path(files["summary"]), {}) if files.get("summary") else {}
    confidence = read_json(Path(files["confidence"]), {}) if files.get("confidence") else {}
    chain_pair_iptm = summary.get("chain_pair_iptm") or summary.get("chain_pair_iptm_scores")
    pae = confidence.get("pae") or summary.get("pae")
    chain_ids = confidence.get("token_chain_ids") or confidence.get("chain_ids")

    structure_url = None
    if files.get("structure"):
        rel_path = Path(files["structure"]).resolve().relative_to(Path(job["output_dir"]).resolve())
        structure_url = f"/api/jobs/{job['job_id']}/structure?path={rel_path.as_posix()}"

    return {
        "job": job,
        "structure_url": structure_url,
        "structure_name": Path(files["structure"]).name if files.get("structure") else None,
        "iptm": summary.get("iptm") or summary.get("iptm_score"),
        "ptm": summary.get("ptm") or summary.get("ptm_score"),
        "ranking_score": summary.get("ranking_score"),
        "chain_pair_iptm": chain_pair_iptm,
        "pae": pae,
        "token_chain_ids": chain_ids,
    }


class Handler(BaseHTTPRequestHandler):
    server_version = "AF3Submitter/1.0"

    def log_message(self, fmt, *args):
        print(f"[{self.log_date_time_string()}] {fmt % args}")

    def send_json(self, data, status=HTTPStatus.OK):
        encoded = json.dumps(data).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(encoded)))
        self.end_headers()
        self.wfile.write(encoded)

    def send_file(self, path):
        if not path.exists() or not path.is_file():
            self.send_error(HTTPStatus.NOT_FOUND)
            return
        mime, _ = mimetypes.guess_type(str(path))
        data = path.read_bytes()
        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", mime or "application/octet-stream")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self):
        parsed = urlparse(self.path)
        path = unquote(parsed.path)

        if path == "/sse":
            self.send_response(HTTPStatus.NO_CONTENT)
            self.end_headers()
            return
        if path == "/":
            self.send_file(STATIC_DIR / "index.html")
            return
        if path == "/viewer":
            self.send_file(STATIC_DIR / "viewer.html")
            return
        if path.startswith("/static/"):
            rel = Path(path.removeprefix("/static/"))
            if ".." in rel.parts:
                self.send_error(HTTPStatus.BAD_REQUEST)
                return
            self.send_file(STATIC_DIR / rel)
            return
        if path == "/api/jobs":
            self.send_json({"jobs": list_jobs()})
            return
        if path.startswith("/api/jobs/"):
            parts = path.split("/")
            if len(parts) < 4:
                self.send_error(HTTPStatus.NOT_FOUND)
                return
            job_id = parts[3]
            job_dir = safe_job(job_id)
            if not job_dir:
                self.send_error(HTTPStatus.NOT_FOUND)
                return
            job = load_job(job_dir)
            if len(parts) == 4:
                self.send_json(job)
                return
            if len(parts) == 5 and parts[4] == "viewer-data":
                self.send_json(confidence_payload(job))
                return
            if len(parts) == 5 and parts[4] == "structure":
                query = parse_qs(parsed.query)
                rel = Path((query.get("path") or [""])[0])
                if not rel.as_posix() or ".." in rel.parts or rel.is_absolute():
                    self.send_error(HTTPStatus.BAD_REQUEST)
                    return
                structure = Path(job["output_dir"]) / rel
                try:
                    structure.resolve().relative_to(Path(job["output_dir"]).resolve())
                except ValueError:
                    self.send_error(HTTPStatus.BAD_REQUEST)
                    return
                self.send_file(structure)
                return

        self.send_error(HTTPStatus.NOT_FOUND)

    def do_POST(self):
        parsed = urlparse(self.path)
        if parsed.path != "/api/submit":
            self.send_error(HTTPStatus.NOT_FOUND)
            return
        try:
            length = int(self.headers.get("Content-Length", "0"))
            payload = json.loads(self.rfile.read(length).decode("utf-8"))
            metadata, status = submit_job(payload)
            self.send_json({"job": metadata}, status)
        except ValueError as exc:
            self.send_json({"error": str(exc)}, HTTPStatus.BAD_REQUEST)
        except subprocess.TimeoutExpired:
            self.send_json({"error": "sbatch timed out."}, HTTPStatus.GATEWAY_TIMEOUT)
        except Exception as exc:
            self.send_json({"error": str(exc)}, HTTPStatus.INTERNAL_SERVER_ERROR)

    def do_DELETE(self):
        parsed = urlparse(self.path)
        path = unquote(parsed.path)
        if not path.startswith("/api/jobs/"):
            self.send_error(HTTPStatus.NOT_FOUND)
            return
        parts = path.split("/")
        if len(parts) != 4:
            self.send_error(HTTPStatus.NOT_FOUND)
            return
        try:
            length = int(self.headers.get("Content-Length", "0"))
            payload = {}
            if length:
                payload = json.loads(self.rfile.read(length).decode("utf-8"))
            result = delete_job(parts[3], payload)
            if not result:
                self.send_json({"error": "Job not found."}, HTTPStatus.NOT_FOUND)
                return
            self.send_json(result)
        except ValueError as exc:
            self.send_json({"error": str(exc)}, HTTPStatus.BAD_REQUEST)
        except Exception as exc:
            self.send_json({"error": str(exc)}, HTTPStatus.INTERNAL_SERVER_ERROR)

    def do_PATCH(self):
        parsed = urlparse(self.path)
        path = unquote(parsed.path)
        if not path.startswith("/api/jobs/"):
            self.send_error(HTTPStatus.NOT_FOUND)
            return
        parts = path.split("/")
        if len(parts) != 5 or parts[4] != "rename":
            self.send_error(HTTPStatus.NOT_FOUND)
            return
        try:
            length = int(self.headers.get("Content-Length", "0"))
            payload = json.loads(self.rfile.read(length).decode("utf-8")) if length else {}
            result = rename_job(parts[3], payload)
            if not result:
                self.send_json({"error": "Job not found."}, HTTPStatus.NOT_FOUND)
                return
            self.send_json(result)
        except ValueError as exc:
            self.send_json({"error": str(exc)}, HTTPStatus.BAD_REQUEST)
        except Exception as exc:
            self.send_json({"error": str(exc)}, HTTPStatus.INTERNAL_SERVER_ERROR)


def main():
    parser = argparse.ArgumentParser(description="Local AlphaFold3 submission applet")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8765)
    args = parser.parse_args()

    INPUTS_DIR.mkdir(parents=True, exist_ok=True)
    OUTPUTS_DIR.mkdir(parents=True, exist_ok=True)

    server = ThreadingHTTPServer((args.host, args.port), Handler)
    print(f"AF3 submitter running at http://{args.host}:{args.port}")
    print(f"AF3 root: {AF3_ROOT}")
    server.serve_forever()


if __name__ == "__main__":
    main()
