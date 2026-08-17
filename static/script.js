const log = document.getElementById("log");
const emptyState = document.getElementById("empty-state");
const form = document.getElementById("ask-form");
const questionEl = document.getElementById("question");
const submitBtn = document.getElementById("submit-btn");

let history = []; // {role: 'user'|'assistant', content: string}
let entryCount = 0;

const CHART_PALETTE = ["#00f5ff", "#ff2ea8", "#7cffb2", "#ffd166", "#b18cff", "#ff8a5b", "#5ac8fa", "#f76e6e"];

const EMPTY_STATE_MESSAGES = [
  {
    hook: "Got a question brewing? Let's hear it.",
    trivia: "Ancient Egyptians used softened papyrus, and ancient Greeks used wrapped lint — people have been figuring this out creatively for thousands of years.",
  },
  {
    hook: "Go on, ask away.",
    trivia: "\u201cMenstruation\u201d comes from the Latin mensis, meaning month — same root as \u201cmoon.\u201d You've been running on lunar time since day one.",
  },
  {
    hook: "No questions yet — but stick around.",
    trivia: "WWI nurses noticed that leftover war-bandage material was incredibly absorbent. That happy accident is why Kotex launched in 1920, kicking off the modern disposable pad.",
  },
  {
    hook: "What's on your mind?",
    trivia: "Stick-on pads — no belt, no pins — only went mainstream in 1969. Before that, everyone was pinning pads in place like a tiny sewing project.",
  },
  {
    hook: "Type something, anything.",
    trivia: "Inventor Mary Kenner designed an adjustable sanitary belt with a built-in moisture-proof pocket back in the 1920s. Discrimination kept it off shelves for decades — brilliant, just delayed.",
  },
  {
    hook: "Ready when you are.",
    trivia: "On average, a person menstruates around 450 times across a lifetime — roughly seven years spent quietly doing something incredible.",
  },
  {
    hook: "Ask something to kick things off.",
    trivia: "A \u201cnormal\u201d cycle can run anywhere from 21 to 35 days. There's no single right rhythm — just yours.",
  },
  {
    hook: "This box won't fill itself.",
    trivia: "The menstrual cup isn't new — Leona Chalmers patented one back in 1937, decades ahead of its comeback.",
  },
  {
    hook: "Say the word.",
    trivia: "The first tampon applicator was patented in 1929 by Earle Haas — an idea that's still basically the blueprint today.",
  },
  {
    hook: "Your first question awaits.",
    trivia: "Plenty of ancient cultures treated menstruation as sacred and powerful, not something to whisper about. Just some history worth remembering.",
  },
];

if (emptyState) {
  const pick = EMPTY_STATE_MESSAGES[Math.floor(Math.random() * EMPTY_STATE_MESSAGES.length)];
  emptyState.innerHTML = `<span class="empty-hook">${pick.hook}</span><br><span class="empty-trivia">${pick.trivia}</span>`;
}

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

function csvEscape(value) {
  const s = value === null || value === undefined ? "" : String(value);
  if (/[",\n]/.test(s)) {
    return '"' + s.replace(/"/g, '""') + '"';
  }
  return s;
}

function slugify(text) {
  const s = (text || "").toLowerCase().replace(/[^a-z0-9]+/g, "-").replace(/(^-|-$)/g, "");
  return s || "plush-intelligence-data";
}

function triggerCSVDownload(csvString, filenameBase) {
  const blob = new Blob([csvString], { type: "text/csv;charset=utf-8;" });
  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = url;
  a.download = `${slugify(filenameBase)}.csv`;
  document.body.appendChild(a);
  a.click();
  document.body.removeChild(a);
  URL.revokeObjectURL(url);
}

function tableSpecToCSV(spec) {
  const lines = [(spec.columns || []).map(csvEscape).join(",")];
  (spec.rows || []).forEach((row) => lines.push(row.map(csvEscape).join(",")));
  return lines.join("\r\n");
}

function chartSpecToCSV(spec) {
  const lines = [];
  if (spec.chart_type === "scatter") {
    lines.push(["series", "x", "y"].map(csvEscape).join(","));
    (spec.series || []).forEach((s) => {
      (s.data || []).forEach((pt) => {
        lines.push([s.name || "", pt.x, pt.y].map(csvEscape).join(","));
      });
    });
  } else {
    const labels = spec.labels || [];
    const series = spec.series || [];
    lines.push(["label", ...series.map((s) => s.name || "")].map(csvEscape).join(","));
    labels.forEach((label, i) => {
      lines.push([label, ...series.map((s) => (s.data || [])[i])].map(csvEscape).join(","));
    });
  }
  return lines.join("\r\n");
}

function makeCSVButton(getCSV, filenameBase) {
  const btn = document.createElement("button");
  btn.type = "button";
  btn.className = "csv-btn";
  btn.textContent = "Download CSV";
  btn.addEventListener("click", () => triggerCSVDownload(getCSV(), filenameBase));
  return btn;
}

function renderChartBlock(container, spec) {
  const wrap = document.createElement("div");
  wrap.className = "chart-block";

  const toolbar = document.createElement("div");
  toolbar.className = "chart-toolbar";
  toolbar.appendChild(makeCSVButton(() => chartSpecToCSV(spec), spec.title || "chart-data"));
  wrap.appendChild(toolbar);

  const canvasWrap = document.createElement("div");
  canvasWrap.className = "chart-canvas-wrap";
  const canvas = document.createElement("canvas");
  canvasWrap.appendChild(canvas);
  wrap.appendChild(canvasWrap);
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

  const header = document.createElement("div");
  header.className = "table-header";
  if (spec.title) {
    const cap = document.createElement("div");
    cap.className = "table-title";
    cap.textContent = spec.title;
    header.appendChild(cap);
  }
  header.appendChild(makeCSVButton(() => tableSpecToCSV(spec), spec.title || "table-data"));
  wrap.appendChild(header);

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
  const highlighted = new Set(spec.highlight_rows || []);
  (spec.rows || []).forEach((row, i) => {
    const tr = document.createElement("tr");
    if (highlighted.has(i)) tr.className = "row-highlight";
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
      <div class="progress-row">
        <div class="progress-track"><div class="progress-fill"></div></div>
        <div class="query-timer">0.0s</div>
      </div>
      <div class="a-body"></div>
      <div class="a-status pending"></div>
    </div>
  `;
  entry.querySelector(".q-text").textContent = question;
  log.appendChild(entry);
  entry.scrollIntoView({ behavior: "smooth", block: "start" });

  const answerBody = entry.querySelector(".a-body");
  const statusEl = entry.querySelector(".a-status");
  const progressRow = entry.querySelector(".progress-row");
  const progressFill = entry.querySelector(".progress-fill");
  const timerEl = entry.querySelector(".query-timer");
  history.push({ role: "user", content: question });

  // The timer is exact — real elapsed time, updated live. The bar is
  // deliberately NOT a fake "estimated %" — we have no way to know how
  // many steps a question will actually need up front. Instead it eases
  // toward ~92% and only advances further on a genuine event (a new
  // status update = a real step actually completed), then snaps to 100%
  // only once the answer has truly finished.
  const startTime = performance.now();
  let progress = 8;
  progressFill.style.width = progress + "%";
  const timerInterval = setInterval(() => {
    timerEl.textContent = ((performance.now() - startTime) / 1000).toFixed(1) + "s";
  }, 100);

  function bumpProgress() {
    progress += (92 - progress) * 0.3;
    progressFill.style.width = progress + "%";
  }

  function finishProgress(succeeded) {
    clearInterval(timerInterval);
    const elapsed = ((performance.now() - startTime) / 1000).toFixed(1);
    if (succeeded) {
      progressFill.style.width = "100%";
      timerEl.textContent = elapsed + "s";
      setTimeout(() => {
        progressRow.style.opacity = "0";
        setTimeout(() => progressRow.remove(), 400);
      }, 500);
    } else {
      progressRow.style.opacity = "0";
      setTimeout(() => progressRow.remove(), 400);
    }
  }

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
      if (response.status === 401) {
        window.location.href = "/login";
        return;
      }
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
        if (raw.startsWith(":")) continue; // SSE comment line (heartbeat) — not a real event, ignore entirely

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
          bumpProgress();
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
          bumpProgress();
        } else if (eventType !== "done") {
          appendText(decoded);
        }
      }
    }

    statusEl.classList.remove("pending");
    statusEl.textContent = "";
    finishProgress(true);
    history.push({ role: "assistant", content: fullAnswerText || "(see chart/table above)" });
  } catch (err) {
    statusEl.classList.remove("pending");
    statusEl.textContent = "";
    finishProgress(false);
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
