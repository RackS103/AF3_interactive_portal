# AlphaFold3 Submitter Applet

A small local web app for preparing AlphaFold3 protein-only JSON inputs and submitting them to SLURM. This version is customized for the [MIT Engaging/ORCD cluster](https://orcd-docs.mit.edu/recipes/af3/) for now. I will later work on making a more universal/customizable version for other server systems.

The app creates one timestamped run folder per submission:

```text
inputs/YYMMDD_JobName_HHMM/
outputs/YYMMDD_JobName_HHMM/
```

It also provides a history panel, delete/rename actions, and a completed-job viewer with pLDDT coloring, ipTM, PAE heatmap, and chain-pair ipTM scores.

## Repository Layout

```text
.
├── README.MD
├── path_to_model.txt
└── submitter_app/
    ├── run_server.sh
    ├── server.py
    └── static/
```

The server creates `inputs/` and `outputs/` automatically when it starts.

## Requesting and Configuring AF3 weights

Follow the instructions on the [AlphaFold3 GitHub page](https://github.com/google-deepmind/alphafold3?tab=readme-ov-file#obtaining-model-parameters) to request/download the model weights. You should obtain a file titled `af3.bin.zst`. Upload this file to the server cluster, then run the following commands in the cluster.

```
cd $WORKDIR
zstd -d af3.bin.zst
mkdir models
mv af3.bin models
rm af3.bin.zst
```

Place the resulting `af3.bin` weights file in some nice folder location. You will need to provide this package the path to your AF3 weights directory as instructed below.

## Configure

Edit `path_to_model.txt` so its first non-empty line is the path to your AlphaFold3 model weights directory:

```text
/absolute/path/to/af3_weights
```

Lines starting with `#` are ignored.

The SLURM template inside `server.py` assumes the same AF3 database/container setup used on ORCD:

```bash
DATABASES_DIR=/orcd/datasets/001/alphafold3
IMAGE_PATH=/orcd/software/community/001/container_images/alphafold3/20260318/alphafold3.sif
```

If your cluster uses different AF3 database or container paths, edit those constants in `submitter_app/server.py`.

## Run

From the cloned repository root:

```bash
submitter_app/run_server.sh
```

By default the app listens at:

```text
http://127.0.0.1:8766
```

You can override host or port:

```bash
AF3_SUBMITTER_HOST=0.0.0.0 AF3_SUBMITTER_PORT=9000 submitter_app/run_server.sh
```

If using VS Code Remote SSH, forward the chosen port and open it in your browser.
You can do this by going into the "PORTS" tab at the bottom of VSCode and adding in
the port number chosen (8766 by default or whatever you choose) in the list of
forwarded ports.

If you are unable to find the "PORTS" tab, hit Fn+F1 and search for "Ports: Focus on Port View"

## Submit Jobs

On the main page:

1. Enter a job name.
2. Enter one or more integer seeds.
3. Add protein chains and sequences.
4. Set copy counts for repeated chains.
5. Press `Submit / Run`.

The app validates protein sequences using the canonical one-letter amino acid alphabet:

```text
ACDEFGHIKLMNPQRSTVWY
```

For each copy, the generated AF3 JSON receives a separate chain ID.

## Outputs

Each run stores submission files in:

```text
inputs/YYMMDD_JobName_HHMM/
```

That folder contains:

```text
fold_input.json
metadata.json
run_alphafold3.slurm
slurm-<jobid>.out
slurm-<jobid>.err
```

AF3 results go to:

```text
outputs/YYMMDD_JobName_HHMM/
```

Completed jobs become clickable in the history panel.

## Rename And Delete

History rows and viewer pages include rename and delete buttons.

Rename preserves the existing date/time stamp while replacing the job-name segment. It also recursively renames AF3 output files/folders and updates text contents that contain the old job name.

Delete removes the matching `inputs/<job_id>/` and `outputs/<job_id>/` folders.
