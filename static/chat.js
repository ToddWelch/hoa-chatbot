// HOA Chatbot v1: minimal vanilla JS, no build step.
// Posts user messages to /api/chat and renders the response.
// Reads the CSRF token from the meta tag rendered by base.html and sends
// it as the X-CSRF-Token header on every POST.

(function () {
  const transcript = document.getElementById("chat-transcript");
  const form = document.getElementById("chat-form");
  const input = document.getElementById("chat-input");
  const submitBtn = document.getElementById("chat-submit");
  const csrfToken = (document.querySelector('meta[name="csrf-token"]') || {}).content || "";

  function appendBubble(role, text, citations) {
    const wrap = document.createElement("div");
    wrap.className = "chat-bubble " + (role === "user" ? "user" : "assistant");
    wrap.textContent = text;

    if (citations && citations.length) {
      const cite = document.createElement("div");
      cite.className = "chat-citations";
      cite.textContent = "Sources: " + citations.join(", ");
      wrap.appendChild(cite);
    }

    transcript.appendChild(wrap);
    transcript.scrollTop = transcript.scrollHeight;
  }

  function appendFallback(text, mailtoUrl) {
    const wrap = document.createElement("div");
    wrap.className = "chat-bubble assistant";
    wrap.textContent = text;

    if (mailtoUrl) {
      const a = document.createElement("a");
      a.href = mailtoUrl;
      a.textContent = "Email the HOA";
      a.className = "button-link";
      a.style.marginTop = "8px";
      a.style.display = "inline-block";

      const wrapLink = document.createElement("div");
      wrapLink.style.marginTop = "8px";
      wrapLink.appendChild(a);
      wrap.appendChild(wrapLink);
    }

    transcript.appendChild(wrap);
    transcript.scrollTop = transcript.scrollHeight;
  }

  function setSending(state) {
    submitBtn.disabled = state;
    input.disabled = state;
    submitBtn.textContent = state ? "Sending..." : "Send";
  }

  async function sendMessage(message) {
    setSending(true);
    appendBubble("user", message);

    try {
      const resp = await fetch("/api/chat", {
        method: "POST",
        headers: {
          "Content-Type": "application/json",
          "X-CSRF-Token": csrfToken,
        },
        body: JSON.stringify({ message: message }),
      });

      const data = await resp.json().catch(function () { return null; });

      if (!resp.ok) {
        appendFallback(
          (data && data.error) ? "Error: " + data.error : "Request failed.",
          null
        );
        return;
      }

      if (!data) {
        appendFallback("The chatbot returned an empty response.", null);
        return;
      }

      if (data.kind === "ok") {
        appendBubble("assistant", data.response, data.cited_documents || []);
      } else {
        appendFallback(data.message || "Something went wrong.", data.mailto || null);
      }
    } catch (err) {
      appendFallback(
        "Could not reach the chatbot. Please try again or email the HOA.",
        null
      );
    } finally {
      setSending(false);
      input.focus();
    }
  }

  form.addEventListener("submit", function (ev) {
    ev.preventDefault();
    const message = (input.value || "").trim();
    if (!message) return;
    input.value = "";
    sendMessage(message);
  });
})();
