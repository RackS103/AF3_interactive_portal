# AlphaFold3 submitter applet internals

The main project documentation lives in `../README.MD`.

Run from the cloned repository root:

```bash
submitter_app/run_server.sh
```

Then open:

```text
http://127.0.0.1:8766
```

Submissions create:

- `inputs/YYMMDD_JobName_HHMM/fold_input.json`
- `inputs/YYMMDD_JobName_HHMM/metadata.json`
- `inputs/YYMMDD_JobName_HHMM/run_alphafold3.slurm`
- `inputs/YYMMDD_JobName_HHMM/slurm-<jobid>.out`
- `outputs/YYMMDD_JobName_HHMM/`

The app calls `sbatch` when Submit / Run is pressed. Completed jobs become clickable when the output directory contains an AF3 structure file and confidence JSON files.
