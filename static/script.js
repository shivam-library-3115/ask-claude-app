const log = document.getElementById("log");
const emptyState = document.getElementById("empty-state");
const form = document.getElementById("ask-form");
const questionEl = document.getElementById("question");
const submitBtn = document.getElementById("submit-btn");

let history = []; // {role: 'user'|'assistant', content: string}
let entryCount = 0;

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
      <div class="a-text pending"></div>
    </div>
  `;
  entry.querySelector(".q-text").textContent = question;
  log.appendChild(entry);
  entry.scrollIntoView({ behavior: "smooth", block: "start" });

  const answerEl = entry.querySelector(".a-text");
  history.push({ role: "user", content: question });

  let fullAnswer = "";
  let answerStarted = false;

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
        const decoded = data.replace(/\\n/g, "\n").replace(/\\\\/g, "\\");

        if (eventType === "error") {
          throw new Error(decoded || "Something went wrong.");
        } else if (eventType === "status") {
          // Transient progress line (e.g. "Thinking…", "Fetching data…").
          // Gets replaced by the next status, and wiped once real answer text starts.
          answerEl.textContent = decoded;
        } else if (eventType !== "done") {
          if (!answerStarted) {
            fullAnswer = "";
            answerStarted = true;
          }
          fullAnswer += decoded;
          answerEl.textContent = fullAnswer;
        }
      }
    }

    answerEl.classList.remove("pending");
    history.push({ role: "assistant", content: fullAnswer });
  } catch (err) {
    answerEl.textContent = `Could not reach Claude — ${err.message}`;
    answerEl.classList.remove("pending");
    answerEl.classList.add("error-text");
    history.pop(); // don't keep a failed turn in context
  } finally {
    submitBtn.disabled = false;
    questionEl.focus();
  }
});
