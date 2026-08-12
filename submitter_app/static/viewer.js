const params = new URLSearchParams(window.location.search);
const jobId = params.get("job");
const errorNode = document.getElementById("viewerError");
const renameButton = document.getElementById("viewerRename");
const deleteButton = document.getElementById("viewerDelete");
let currentJob = null;

function setError(message) {
  errorNode.textContent = message;
  errorNode.hidden = !message;
}

function formatMetric(value) {
  if (value === null || value === undefined || value === "") return "--";
  const number = Number(value);
  return Number.isFinite(number) ? number.toFixed(3) : String(value);
}

function jobName(job) {
  return job?.job_name || job?.job_id || "job";
}

async function renameCurrentJob() {
  if (!currentJob) return;
  const oldName = jobName(currentJob);
  const newJobName = window.prompt("Rename job to:", oldName);
  if (newJobName === null) return;
  const trimmedName = newJobName.trim();
  if (!trimmedName || trimmedName === oldName) return;

  const confirmed = window.confirm(
    `Rename "${oldName}" to "${trimmedName}"?\n\nThis will rename the input/output folders and all AF3 output files/folders that contain the old job name.`
  );
  if (!confirmed) return;

  setError("");
  renameButton.disabled = true;
  try {
    const response = await fetch(`/api/jobs/${encodeURIComponent(currentJob.job_id)}/rename`, {
      method: "PATCH",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        new_job_name: trimmedName,
        input_dir: currentJob.input_dir,
        output_dir: currentJob.output_dir,
      }),
    });
    const result = await response.json();
    if (!response.ok) {
      throw new Error(result.error || "Could not rename job.");
    }
    window.location.href = `/viewer?job=${encodeURIComponent(result.job.job_id)}`;
  } catch (error) {
    setError(error.message);
    renameButton.disabled = false;
  }
}

async function deleteCurrentJob() {
  if (!currentJob) return;
  const name = jobName(currentJob);
  const confirmed = window.confirm(
    `Delete "${name}"?\n\nThis will permanently remove its inputs and outputs folders.`
  );
  if (!confirmed) return;

  setError("");
  deleteButton.disabled = true;
  try {
    const response = await fetch(`/api/jobs/${encodeURIComponent(currentJob.job_id)}`, {
      method: "DELETE",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        input_dir: currentJob.input_dir,
        output_dir: currentJob.output_dir,
      }),
    });
    const result = await response.json();
    if (!response.ok) {
      throw new Error(result.error || "Could not delete job.");
    }
    window.location.href = "/";
  } catch (error) {
    setError(error.message);
    deleteButton.disabled = false;
  }
}

async function loadStructure(data) {
  if (!data.structure_url) {
    setError("No structure file was found for this completed job.");
    return;
  }
  if (!window.$3Dmol) {
    setError("3Dmol.js did not load. The viewer needs browser access to https://3Dmol.org.");
    return;
  }

  const response = await fetch(data.structure_url);
  const structureText = await response.text();
  const ext = (data.structure_name || "").toLowerCase().endsWith(".pdb") ? "pdb" : "cif";
  const viewer = $3Dmol.createViewer("structureViewer", { backgroundColor: "#f8fafc" });
  viewer.addModel(structureText, ext);
  viewer.setStyle({}, {
    cartoon: {
      colorscheme: { prop: "b", gradient: "roygb", min: 0, max: 100 },
    },
  });
  viewer.zoomTo();
  viewer.render();
  window.addEventListener("resize", () => viewer.resize());
}

function renderPae(pae) {
  const node = document.getElementById("paeHeatmap");
  if (!pae || !Array.isArray(pae) || !pae.length) {
    node.innerHTML = `<p class="empty">No PAE matrix was found.</p>`;
    return;
  }
  if (!window.Plotly) {
    node.innerHTML = `<p class="empty">Plotly did not load.</p>`;
    return;
  }
  Plotly.newPlot(
    node,
    [{
      z: pae,
      type: "heatmap",
      colorscale: "Viridis",
      zmin: 0,
      zmax: 30,
      hovertemplate: "Residue x %{x}<br>Residue y %{y}<br>PAE %{z:.2f}<extra></extra>",
      colorbar: { title: "PAE" },
    }],
    {
      margin: { l: 48, r: 12, t: 8, b: 44 },
      xaxis: { title: "Residue" },
      yaxis: { title: "Residue", autorange: "reversed" },
      paper_bgcolor: "rgba(0,0,0,0)",
      plot_bgcolor: "rgba(0,0,0,0)",
    },
    { responsive: true, displayModeBar: false }
  );
}

function renderChainPairScores(matrix, chains) {
  const node = document.getElementById("chainPairTable");
  if (!matrix || !Array.isArray(matrix) || !matrix.length) {
    node.innerHTML = `<p class="empty">No chain-pair ipTM scores were found.</p>`;
    return;
  }
  const chainIds = (chains || []).map((chain) => chain.id);
  let html = `<table><thead><tr><th></th>`;
  for (let index = 0; index < matrix.length; index += 1) {
    html += `<th>${chainIds[index] || index + 1}</th>`;
  }
  html += `</tr></thead><tbody>`;
  for (let row = 0; row < matrix.length; row += 1) {
    html += `<tr><th>${chainIds[row] || row + 1}</th>`;
    for (let col = 0; col < matrix[row].length; col += 1) {
      html += `<td>${formatMetric(matrix[row][col])}</td>`;
    }
    html += `</tr>`;
  }
  html += `</tbody></table>`;
  node.innerHTML = html;
}

async function main() {
  if (!jobId) {
    setError("No job id was provided.");
    return;
  }
  const response = await fetch(`/api/jobs/${encodeURIComponent(jobId)}/viewer-data`);
  if (!response.ok) {
    setError("Could not load this job.");
    return;
  }
  const data = await response.json();
  currentJob = data.job;
  document.getElementById("viewerTitle").textContent = data.job.job_name || data.job.job_id;
  document.getElementById("iptmValue").textContent = formatMetric(data.iptm);
  renderPae(data.pae);
  renderChainPairScores(data.chain_pair_iptm, data.job.chains);
  await loadStructure(data);
}

renameButton.addEventListener("click", renameCurrentJob);
deleteButton.addEventListener("click", deleteCurrentJob);
main().catch((error) => setError(error.message));
