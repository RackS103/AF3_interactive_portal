const VALID_AA = new Set("ACDEFGHIKLMNPQRSTVWY".split(""));
const chainsNode = document.getElementById("chains");
const chainTemplate = document.getElementById("chainTemplate");
const errorBox = document.getElementById("errorBox");
const successBox = document.getElementById("successBox");
const submitButton = document.getElementById("submitButton");

function showError(message) {
  errorBox.textContent = message;
  errorBox.hidden = !message;
}

function showSuccess(message) {
  successBox.textContent = message;
  successBox.hidden = !message;
}

function cleanSequence(value) {
  return value.replace(/\s+/g, "").toUpperCase();
}

function invalidCharacters(sequence) {
  return [...new Set(sequence.split("").filter((char) => !VALID_AA.has(char)))].sort();
}

function addChain(data = {}) {
  const node = chainTemplate.content.firstElementChild.cloneNode(true);
  const label = node.querySelector(".chain-label");
  const copies = node.querySelector(".chain-copies");
  const sequence = node.querySelector(".chain-sequence");
  const chainError = node.querySelector(".chain-error");

  label.value = data.label || `Chain ${chainsNode.children.length + 1}`;
  copies.value = data.copies || 1;
  sequence.value = data.sequence || "";

  function validateChain() {
    const bad = invalidCharacters(cleanSequence(sequence.value));
    if (bad.length) {
      chainError.textContent = `Invalid amino acid character(s): ${bad.join(", ")}`;
      chainError.hidden = false;
      sequence.classList.add("invalid-input");
    } else {
      chainError.hidden = true;
      sequence.classList.remove("invalid-input");
    }
  }

  sequence.addEventListener("input", validateChain);
  node.querySelector(".remove-chain").addEventListener("click", () => {
    node.remove();
    if (!chainsNode.children.length) addChain();
  });
  chainsNode.appendChild(node);
}

function collectPayload() {
  const chains = [...chainsNode.querySelectorAll(".chain-card")].map((card) => ({
    label: card.querySelector(".chain-label").value.trim(),
    copies: Number(card.querySelector(".chain-copies").value || 1),
    sequence: cleanSequence(card.querySelector(".chain-sequence").value),
  }));

  for (const chain of chains) {
    const bad = invalidCharacters(chain.sequence);
    if (bad.length) {
      throw new Error(`${chain.label || "Chain"} has invalid amino acid character(s): ${bad.join(", ")}`);
    }
  }

  return {
    job_name: document.getElementById("jobName").value.trim(),
    seeds: document.getElementById("seeds").value.trim(),
    chains,
  };
}

function statusBadge(job) {
  const status = (job.status || "unknown").toLowerCase();
  if (["running", "submitted", "pending", "configuring", "completing"].includes(status)) {
    return `<span class="spinner" aria-label="Running"></span>`;
  }
  if (status === "completed") {
    return `<span class="check" aria-label="Completed">&check;</span>`;
  }
  if (status.includes("failed") || status === "failed") {
    return `<span class="fail" aria-label="Failed">!</span>`;
  }
  return `<span class="neutral-dot" aria-label="${status}"></span>`;
}

function formatDate(value) {
  if (!value) return "";
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return value;
  return date.toLocaleString();
}

function escapeHtml(value) {
  return String(value ?? "").replace(/[&<>"']/g, (char) => ({
    "&": "&amp;",
    "<": "&lt;",
    ">": "&gt;",
    '"': "&quot;",
    "'": "&#39;",
  })[char]);
}

async function loadJobs() {
  const list = document.getElementById("jobsList");
  const response = await fetch("/api/jobs");
  const data = await response.json();
  if (!data.jobs.length) {
    list.innerHTML = `<p class="empty">No AF3 submissions yet.</p>`;
    return;
  }

  list.innerHTML = data.jobs
    .map((job) => {
      const href = job.has_viewer ? `/viewer?job=${encodeURIComponent(job.job_id)}` : "#";
      const disabled = job.has_viewer ? "" : "disabled";
      const jobName = job.job_name || job.job_id;
      return `
        <div class="job-entry">
          <a class="job-row ${disabled}" href="${href}" aria-disabled="${job.has_viewer ? "false" : "true"}">
            <span class="status-cell">${statusBadge(job)}</span>
            <span>
              <strong>${escapeHtml(jobName)}</strong>
              <small>${escapeHtml(formatDate(job.created_at))}</small>
            </span>
          </a>
          <button
            class="rename-job icon-button"
            type="button"
            title="Rename job"
            aria-label="Rename ${escapeHtml(jobName)}"
            data-job-id="${escapeHtml(job.job_id)}"
            data-job-name="${escapeHtml(jobName)}"
            data-input-dir="${escapeHtml(job.input_dir)}"
            data-output-dir="${escapeHtml(job.output_dir)}"
          >
            <span aria-hidden="true">&#9998;</span>
          </button>
          <button
            class="delete-job icon-button"
            type="button"
            title="Delete job"
            aria-label="Delete ${escapeHtml(jobName)}"
            data-job-id="${escapeHtml(job.job_id)}"
            data-job-name="${escapeHtml(jobName)}"
            data-input-dir="${escapeHtml(job.input_dir)}"
            data-output-dir="${escapeHtml(job.output_dir)}"
          >
            <span aria-hidden="true">&times;</span>
          </button>
        </div>
      `;
    })
    .join("");

  list.querySelectorAll(".rename-job").forEach((button) => {
    button.addEventListener("click", async () => {
      const jobId = button.dataset.jobId;
      const jobName = button.dataset.jobName || jobId;
      const inputDir = button.dataset.inputDir;
      const outputDir = button.dataset.outputDir;
      const newJobName = window.prompt("Rename job to:", jobName);
      if (newJobName === null) return;
      const trimmedName = newJobName.trim();
      if (!trimmedName || trimmedName === jobName) return;

      const confirmed = window.confirm(
        `Rename "${jobName}" to "${trimmedName}"?\n\nThis will rename the input/output folders and all AF3 output files/folders that contain the old job name.`
      );
      if (!confirmed) return;

      showError("");
      showSuccess("");
      button.disabled = true;
      try {
        const response = await fetch(`/api/jobs/${encodeURIComponent(jobId)}/rename`, {
          method: "PATCH",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({
            new_job_name: trimmedName,
            input_dir: inputDir,
            output_dir: outputDir,
          }),
        });
        const result = await response.json();
        if (!response.ok) {
          throw new Error(result.error || "Could not rename job.");
        }
        showSuccess(`Renamed ${jobName} to ${result.job.job_name}.`);
        await loadJobs();
      } catch (error) {
        showError(error.message);
        button.disabled = false;
      }
    });
  });

  list.querySelectorAll(".delete-job").forEach((button) => {
    button.addEventListener("click", async () => {
      const jobId = button.dataset.jobId;
      const jobName = button.dataset.jobName || jobId;
      const inputDir = button.dataset.inputDir;
      const outputDir = button.dataset.outputDir;
      const confirmed = window.confirm(
        `Delete "${jobName}"?\n\nThis will permanently remove its inputs and outputs folders.`
      );
      if (!confirmed) return;

      showError("");
      showSuccess("");
      button.disabled = true;
      try {
        const response = await fetch(`/api/jobs/${encodeURIComponent(jobId)}`, {
          method: "DELETE",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ input_dir: inputDir, output_dir: outputDir }),
        });
        const result = await response.json();
        if (!response.ok) {
          throw new Error(result.error || "Could not delete job.");
        }
        showSuccess(`Deleted ${jobName}.`);
        await loadJobs();
      } catch (error) {
        showError(error.message);
        button.disabled = false;
      }
    });
  });
}

document.getElementById("addChain").addEventListener("click", () => addChain());
document.getElementById("refreshJobs").addEventListener("click", loadJobs);

document.getElementById("submitForm").addEventListener("submit", async (event) => {
  event.preventDefault();
  showError("");
  showSuccess("");
  submitButton.disabled = true;
  submitButton.textContent = "Submitting...";

  try {
    const response = await fetch("/api/submit", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(collectPayload()),
    });
    const data = await response.json();
    if (!response.ok) {
      throw new Error(data.error || data.job?.submission_stderr || "Submission failed.");
    }
    showSuccess(`Submitted ${data.job.job_id}${data.job.slurm_job_id ? ` as SLURM job ${data.job.slurm_job_id}` : ""}.`);
    await loadJobs();
  } catch (error) {
    showError(error.message);
    await loadJobs();
  } finally {
    submitButton.disabled = false;
    submitButton.textContent = "Submit / Run";
  }
});

addChain({ label: "Chain 1", copies: 1 });
loadJobs();
setInterval(loadJobs, 15000);
