const log = document.getElementById("log");
const emptyState = document.getElementById("empty-state");
const form = document.getElementById("ask-form");
const questionEl = document.getElementById("question");
const submitBtn = document.getElementById("submit-btn");

let history = []; // {role: 'user'|'assistant', content: string}
let entryCount = 0;

const CHART_PALETTE = ["#00f5ff", "#ff2ea8", "#7cffb2", "#ffd166", "#b18cff", "#ff8a5b", "#5ac8fa", "#f76e6e"];

questionEl.addEventListener("input", () => {
  questionEl.style.height = "auto";
  questionEl.style.height = Math.min(questionEl.scrollHeight, 104) + "px";
});

questionEl.addEventListener("keydown", (e) => {
  if (e.key === "Enter" && !e.shiftKey) {
    e.preventDefault();
    form.requestSubmit();
  }
});

function withAlpha(hex, alpha) {
  return hex + alpha;
}

function renderChartBlock(container, spec) {
  const wrap = document.createElement("div");
  wrap.className = "chart-block";
  const canvas = document.createElement("canvas");
  wrap.appendChild(canvas);
  container.appendChild(wrap);

  const type = spec.chart_type;
  let data;

  if (type === "scatter") {
    data = {
      datasets: (spec.series || []).map((s, i) => {
        const color = CHART_PALETTE[i % CHART_PALETTE.length];
        return {
          label: s.name || `Series ${i + 1}`,
          data: (s.data || []).map((pt) => ({ x: pt.x, y: pt.y })),
          backgroundColor: color,
          borderColor: color,
        };
      }),
    };
  } else {
    data = {
      labels: spec.labels || [],
      datasets: (spec.series || []).map((s, i) => {
        const color = CHART_PALETTE[i % CHART_PALETTE.length];
        return {
          label: s.name || `Series ${i + 1}`,
          data: s.data || [],
          backgroundColor: type === "pie" ? CHART_PALETTE : withAlpha(color, "33"),
          borderColor: type === "pie" ? "#0d0b22" : color,
          borderWidth: type === "pie" ? 2 : 2,
          tension: 0.3,
          fill: type === "line",
        };
      }),
    };
  }

  new Chart(canvas.getContext("2d"), {
    type: type,
    data: data,
    options: {
      responsive: true,
      maintainAspectRatio: false,
      plugins: {
        legend: {
          labels: { color: "#edf0ff", font: { family: "'JetBrains Mono', monospace", size: 11 } },
        },
        title: spec.title
          ? {
              display: true,
              text: spec.title,
              color: "#00f5ff",
              font: { family: "'Orbitron', sans-serif", size: 13, weight: "700" },
              padding: { bottom: 12 },
            }
          : { display: false },
      },
      scales:
        type === "pie"
          ? {}
          : {
              x: { ticks: { color: "#8d8fc0" }, grid: { color: "rgba(0,245,255,0.08)" } },
              y: { ticks: { color: "#8d8fc0" }, grid: { color: "rgba(0,245,255,0.08)" } },
            },
    },
  });
}

function renderTableBlock(container, spec) {
  const wrap = document.createElement("div");
  wrap.className = "table-block";

  if (spec.title) {
    const cap = document.createElement("div");
    cap.className = "table-title";
    cap.textContent = spec.title;
    wrap.appendChild(cap);
  }

  const scroller = document.createElement("div");
  scroller.className = "table-scroll";
  const table = document.createElement("table");

  const thead = document.createElement("thead");
  const headRow = document.createElement("tr");
  (spec.columns || []).forEach((col) => {
    const th = document.createElement("th");
    th.textContent = col;
    headRow.appendChild(th);
  });
  thead.appendChild(headRow);
  table.appendChild(thead);

  const tbody = document.createElement("tbody");
  (spec.rows || []).forEach((row) => {
    const tr = document.createElement("tr");
    row.forEach((cell) => {
      const td = document.createElement("td");
      td.textContent = cell === null || cell === undefined || cell === "" ? "—" : String(cell);
      tr.appendChild(td);
    });
    tbody.appendChild(tr);
  });
  table.appendChild(tbody);

  scroller.appendChild(table);
  wrap.appendChild(scroller);
  container.appendChild(wrap);
}

form.addEventListener("submit", async (e) => {
  e.preventDefault();
  const question = questionEl.value.trim();
  if (!question || submitBtn.disabled) return;

  questionEl.value = "";
  questionEl.style.height = "auto";
  submitBtn.disabled = true;

  if (emptyState) emptyState.remove();

  entryCount += 1;
  const entry = document.createElement("div");
  entry.className = "entry";
  entry.setAttribute("data-num", String(entryCount).padStart(3, "0"));
  entry.innerHTML = `
    <div class="row">
      <span class="label">Question</span>
      <div class="q-text"></div>
    </div>
    <div class="row">
      <span class="label">Plush Buddy</span>
      <div class="a-body"></div>
      <div class="a-status pending"></div>
    </div>
  `;
  entry.querySelector(".q-text").textContent = question;
  log.appendChild(entry);
  entry.scrollIntoView({ behavior: "smooth", block: "start" });

  const answerBody = entry.querySelector(".a-body");
  const statusEl = entry.querySelector(".a-status");
  history.push({ role: "user", content: question });

  let fullAnswerText = ""; // plain-text portion only, for conversation history
  let currentTextEl = null; // the open text block, or null if the last thing rendered wasn't text

  function appendText(chunk) {
    statusEl.textContent = "";
    if (!currentTextEl) {
      currentTextEl = document.createElement("div");
      currentTextEl.className = "a-text";
      answerBody.appendChild(currentTextEl);
    }
    currentTextEl.textContent += chunk;
    fullAnswerText += chunk;
  }

  try {
    const response = await fetch("/ask", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ messages: history }),
    });

    if (!response.ok || !response.body) {
      const err = await response.json().catch(() => ({}));
      throw new Error(err.error || `Request failed (${response.status})`);
    }

    const reader = response.body.getReader();
    const decoder = new TextDecoder();
    let buffer = "";

    while (true) {
      const { value, done } = await reader.read();
      if (done) break;
      buffer += decoder.decode(value, { stream: true });

      const chunks = buffer.split("\n\n");
      buffer = chunks.pop(); // keep any incomplete event for next read

      for (const raw of chunks) {
        if (!raw.trim()) continue;

        let eventType = "message";
        let data = "";
        for (const line of raw.split("\n")) {
          if (line.startsWith("event:")) eventType = line.slice(6).trim();
          if (line.startsWith("data:")) data += line.slice(5).trim();
        }

        if (eventType === "chart" || eventType === "table") {
          // JSON payload — sent raw by the server, do NOT run the text decode below on it.
          currentTextEl = null; // next text chunk (if any) starts a fresh block below this
          statusEl.textContent = "";
          const spec = JSON.parse(data);
          if (eventType === "chart") {
            renderChartBlock(answerBody, spec);
          } else {
            renderTableBlock(answerBody, spec);
          }
          continue;
        }

        const decoded = data.replace(/\\n/g, "\n").replace(/\\\\/g, "\\");

        if (eventType === "error") {
          throw new Error(decoded || "Something went wrong.");
        } else if (eventType === "status") {
          // Transient progress line, shown below whatever's rendered so far.
          statusEl.textContent = decoded;
        } else if (eventType !== "done") {
          appendText(decoded);
        }
      }
    }

    statusEl.classList.remove("pending");
    statusEl.textContent = "";
    history.push({ role: "assistant", content: fullAnswerText || "(see chart/table above)" });
  } catch (err) {
    statusEl.classList.remove("pending");
    statusEl.textContent = "";
    const errEl = document.createElement("div");
    errEl.className = "a-text error-text";
    errEl.textContent = `Could not reach Plush Buddy — ${err.message}`;
    answerBody.appendChild(errEl);
    history.pop(); // don't keep a failed turn in context
  } finally {
    submitBtn.disabled = false;
    questionEl.focus();
  }
});
