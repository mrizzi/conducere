(function () {
    function esc(str) {
        var d = document.createElement("div");
        d.textContent = str;
        return d.innerHTML;
    }

    if (typeof marked !== "undefined") {
        marked.use({
            renderer: {
                html: function (token) { return esc(token.text); },
                link: function (ref) {
                    var href = ref.href, title = ref.title, tokens = ref.tokens;
                    if (href && /^(javascript|data|vbscript):/i.test(href.replace(/\s/g, ""))) {
                        return esc(tokens.map(function (t) { return t.raw; }).join(""));
                    }
                    var titleAttr = title ? ' title="' + esc(title) + '"' : "";
                    return '<a href="' + esc(href) + '"' + titleAttr + ' rel="noopener noreferrer">' + marked.Parser.parseInline(tokens) + "</a>";
                },
            },
        });
    }

    function renderMarkdown(text) {
        if (typeof marked !== "undefined") {
            return marked.parse(text);
        }
        return esc(text);
    }

    var params = new URLSearchParams(window.location.search);
    var sessionId = window.location.pathname.split("/session/")[1];

    if (!sessionId) return;

    var token = params.get("token");
    if (token) {
        localStorage.setItem("dc-token-" + sessionId, token);
        history.replaceState(null, "", window.location.pathname);
    } else {
        token = localStorage.getItem("dc-token-" + sessionId);
    }

    var ALLOWED_TYPES = { question: 1, proposal: 1, summary: 1, info: 1, ai: 1, human: 1 };
    var AGENT_STATES = { agent_listening: "listening", agent_processing: "processing", agent_disconnected: "disconnected" };

    var messagesEl = document.getElementById("messages");
    var inputEl = document.getElementById("comment-input");
    var sendBtn = document.getElementById("send-btn");
    var titleEl = document.getElementById("session-title");
    var participantsEl = document.getElementById("participants");
    var catchupBanner = document.getElementById("catchup-banner");
    var catchupText = document.getElementById("catchup-text");
    var catchupYes = document.getElementById("catchup-yes");
    var catchupDismiss = document.getElementById("catchup-dismiss");
    var agentDot = document.getElementById("agent-dot");
    var joinOverlay = document.getElementById("join-overlay");
    var joinTitle = document.getElementById("join-title");
    var joinForm = document.getElementById("join-form");
    var joinNameInput = document.getElementById("join-name");
    var joinError = document.getElementById("join-error");
    var joinEnded = document.getElementById("join-ended");

    var ws = null;
    var currentUser = null;

    async function init() {
        var resp = await fetch(
            "/api/sessions/" + sessionId + (token ? "?token=" + token : "")
        );
        if (!resp.ok) {
            messagesEl.textContent = "Session not found.";
            return;
        }
        var session = await resp.json();
        currentUser = session.current_user || null;
        titleEl.textContent = session.title;

        if (!currentUser) {
            showJoinOverlay(session);
            return;
        }

        renderParticipants(session.participants);

        var msgResp = await fetch("/api/sessions/" + sessionId + "/messages" + (token ? "?token=" + token : ""));
        var messages = await msgResp.json();
        messages.forEach(renderMessage);
        scrollToBottom();

        if (session.last_seen && messages.length > 0) {
            var lastSeen = new Date(session.last_seen);
            var newCount = messages.filter(
                function (m) { return new Date(m.timestamp) > lastSeen; }
            ).length;
            if (newCount > 0) {
                catchupText.textContent =
                    newCount + " new message" + (newCount > 1 ? "s" : "") +
                    " since you were last here. Want a summary?";
                catchupBanner.classList.remove("hidden");
            }
        }

        inputEl.disabled = false;
        sendBtn.disabled = false;
        inputEl.placeholder = "Type your message...";
        connectWebSocket();
    }

    function showJoinOverlay(session) {
        joinTitle.textContent = session.title;
        if (session.status === "complete") {
            joinForm.classList.add("hidden");
            joinEnded.classList.remove("hidden");
        }
        joinOverlay.classList.remove("hidden");
        inputEl.disabled = true;
        sendBtn.disabled = true;
    }

    function badgeClass(name, extra) {
        var cls = "participant-badge";
        if (extra) cls += " " + extra;
        if (name === currentUser) cls += " current-user";
        return cls;
    }

    function renderParticipants(participants) {
        participantsEl.textContent = "";
        participants.forEach(function (p) {
            var badge = document.createElement("span");
            badge.className = badgeClass(p.name);
            badge.textContent = p.name;
            participantsEl.appendChild(badge);
        });
    }

    function renderMessage(msg) {
        var div = document.createElement("div");
        var meta = msg.metadata || {};
        var isAI = msg.author === "ai";
        var rawType = meta.type || (isAI ? "ai" : "human");
        var typeClass = ALLOWED_TYPES[rawType] ? rawType : (isAI ? "ai" : "human");
        div.className = "message " + typeClass;

        var authorEl = document.createElement("div");
        authorEl.className = "author";
        authorEl.textContent = msg.author;
        if (meta.section) {
            var tag = document.createElement("span");
            tag.className = "section-tag";
            tag.textContent = meta.section;
            authorEl.appendChild(tag);
        }
        div.appendChild(authorEl);

        var textEl = document.createElement("div");
        textEl.className = "text";
        textEl.innerHTML = renderMarkdown(msg.text);
        div.appendChild(textEl);

        var timeEl = document.createElement("div");
        timeEl.className = "timestamp";
        timeEl.textContent = formatTime(msg.timestamp);
        div.appendChild(timeEl);

        messagesEl.appendChild(div);
    }

    function scrollToBottom() {
        messagesEl.scrollTop = messagesEl.scrollHeight;
    }

    function isNearBottom() {
        var threshold = 100;
        return messagesEl.scrollHeight - messagesEl.scrollTop - messagesEl.clientHeight < threshold;
    }

    function setAgentState(state) {
        agentDot.className = "agent-dot " + state;
        sendBtn.disabled = state !== "listening";
        if (state === "processing") {
            showTypingIndicator();
        } else {
            removeTypingIndicator();
        }
    }

    function showTypingIndicator() {
        if (document.getElementById("agent-typing")) return;
        var el = document.createElement("div");
        el.id = "agent-typing";
        var bar = document.createElement("div");
        bar.className = "bar";
        el.appendChild(bar);
        var label = document.createElement("span");
        label.textContent = "thinking…";
        el.appendChild(label);
        messagesEl.appendChild(el);
        if (isNearBottom()) scrollToBottom();
    }

    function removeTypingIndicator() {
        var el = document.getElementById("agent-typing");
        if (el) el.remove();
    }

    function formatTime(ts) {
        return new Date(ts).toLocaleTimeString();
    }

    function connectWebSocket() {
        var proto = window.location.protocol === "https:" ? "wss:" : "ws:";
        var url = proto + "//" + window.location.host +
            "/ws/sessions/" + sessionId + "?token=" + (token || "");
        ws = new WebSocket(url);

        ws.onmessage = function (event) {
            var data = JSON.parse(event.data);
            if (data.type === "message_added") {
                removeTypingIndicator();
                renderMessage(data.message);
                scrollToBottom();
            } else if (data.type === "participant_joined") {
                var existing = Array.from(participantsEl.children).some(
                    function (el) { return el.textContent === data.user; }
                );
                if (!existing) {
                    var badge = document.createElement("span");
                    badge.className = badgeClass(data.user, "online");
                    badge.textContent = data.user;
                    participantsEl.appendChild(badge);
                }
            } else if (AGENT_STATES[data.type]) {
                setAgentState(AGENT_STATES[data.type]);
            }
        };

        ws.onclose = function () {
            setTimeout(connectWebSocket, 3000);
        };
    }

    async function sendMessage() {
        var text = inputEl.value.trim();
        if (!text || !token) return;

        var resp = await fetch(
            "/api/sessions/" + sessionId + "/messages?token=" + token,
            {
                method: "POST",
                headers: { "Content-Type": "application/json" },
                body: JSON.stringify({
                    text: text,
                }),
            }
        );

        if (resp.ok) {
            inputEl.value = "";
        }
    }

    sendBtn.addEventListener("click", sendMessage);
    inputEl.addEventListener("keydown", function (e) {
        if (e.key === "Enter" && !e.shiftKey && !sendBtn.disabled) {
            e.preventDefault();
            sendMessage();
        }
    });

    catchupYes.addEventListener("click", async function () {
        catchupBanner.classList.add("hidden");
        await fetch(
            "/api/sessions/" + sessionId + "/messages?token=" + token,
            {
                method: "POST",
                headers: { "Content-Type": "application/json" },
                body: JSON.stringify({
                    text: "/catchup",
                }),
            }
        );
    });

    catchupDismiss.addEventListener("click", function () {
        catchupBanner.classList.add("hidden");
    });

    joinForm.addEventListener("submit", async function (e) {
        e.preventDefault();
        var name = joinNameInput.value.trim();
        if (!name) return;

        joinError.classList.add("hidden");
        var resp = await fetch("/api/sessions/" + sessionId + "/join", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ name: name }),
        });

        if (!resp.ok) {
            var err = await resp.json();
            joinError.textContent = err.detail || "Join failed";
            joinError.classList.remove("hidden");
            return;
        }

        var data = await resp.json();
        token = data.token;
        localStorage.setItem("dc-token-" + sessionId, token);
        joinOverlay.classList.add("hidden");
        init();
    });

    init();
})();
