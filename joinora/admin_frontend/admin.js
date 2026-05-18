(function () {
    function esc(str) {
        var d = document.createElement("div");
        d.textContent = str;
        return d.innerHTML;
    }

    var renderer = new marked.Renderer();
    renderer.html = function (token) {
        return esc(typeof token === "string" ? token : token.text);
    };
    renderer.link = function (ref) {
        var href = ref.href;
        if (href && /^(javascript|data|vbscript):/i.test(href.replace(/\s/g, ""))) {
            var span = document.createElement("span");
            span.textContent = ref.text;
            return span.outerHTML;
        }
        var a = document.createElement("a");
        a.href = href;
        a.textContent = ref.text;
        a.target = "_blank";
        a.rel = "noopener noreferrer";
        return a.outerHTML;
    };
    marked.setOptions({ breaks: true, renderer: renderer });

    var currentUser = null;
    var currentRole = null;

    function init() {
        var cookie = document.cookie
            .split("; ")
            .find(function (c) { return c.startsWith("joinora_admin="); });
        if (cookie) {
            try {
                var token = cookie.split("=")[1];
                var payload = JSON.parse(atob(token.split(".")[1]));
                currentUser = payload.sub;
                currentRole = payload.role;
            } catch (e) {
                // ignore parse errors — enforcement is server-side
            }
        }

        var navUser = document.getElementById("nav-user");
        if (currentUser) {
            navUser.textContent = currentUser + " (" + currentRole + ")";
        }

        document.getElementById("logout-btn").addEventListener("click", function () {
            fetch("/admin/logout", { method: "POST" }).then(function () {
                window.location.href = "/admin/login";
            });
        });

        var tabs = document.querySelectorAll(".nav-tab");
        tabs.forEach(function (tab) {
            tab.addEventListener("click", function (e) {
                e.preventDefault();
                window.location.hash = tab.getAttribute("data-tab");
            });
        });

        window.addEventListener("hashchange", route);
        route();
    }

    function route() {
        var hash = window.location.hash.replace(/^#/, "") || "dashboard";
        var parts = hash.split("/");
        var tab = parts[0];
        var subId = parts[1] || null;

        var tabs = document.querySelectorAll(".nav-tab");
        tabs.forEach(function (t) {
            if (t.getAttribute("data-tab") === tab) {
                t.classList.add("active");
            } else {
                t.classList.remove("active");
            }
        });

        var views = document.querySelectorAll(".view");
        views.forEach(function (v) { v.classList.add("hidden"); });

        var viewId = "view-" + tab;
        var view = document.getElementById(viewId);
        if (view) {
            view.classList.remove("hidden");
        }

        if (tab === "dashboard") {
            loadDashboard();
        } else if (tab === "sessions") {
            loadSessions(subId);
        } else if (tab === "settings") {
            loadSettings();
        }
    }

    function loadDashboard() {
        fetch("/admin/api/stats")
            .then(function (r) { return r.json(); })
            .then(function (stats) {
                var container = document.getElementById("stats-cards");
                container.textContent = "";

                var cards = [
                    { label: "Active Sessions", value: stats.active_sessions, color: "blue" },
                    { label: "Completed", value: stats.completed_sessions, color: "green" },
                    { label: "Total Messages", value: stats.total_messages, color: "purple" },
                    { label: "Participants", value: stats.total_participants, color: "amber" },
                ];

                cards.forEach(function (c) {
                    var card = document.createElement("div");
                    card.className = "stat-card";

                    var label = document.createElement("div");
                    label.className = "stat-label";
                    label.textContent = c.label;
                    card.appendChild(label);

                    var value = document.createElement("div");
                    value.className = "stat-value " + c.color;
                    value.textContent = c.value;
                    card.appendChild(value);

                    container.appendChild(card);
                });
            });

        fetch("/admin/api/sessions")
            .then(function (r) { return r.json(); })
            .then(function (data) {
                var container = document.getElementById("recent-sessions");
                container.textContent = "";

                var table = document.createElement("table");
                table.className = "sessions-table";

                var thead = document.createElement("thead");
                var headerRow = document.createElement("tr");
                ["Session", "Status", "Participants", "Messages"].forEach(function (h) {
                    var th = document.createElement("th");
                    th.textContent = h;
                    headerRow.appendChild(th);
                });
                thead.appendChild(headerRow);
                table.appendChild(thead);

                var tbody = document.createElement("tbody");
                data.sessions.forEach(function (s) {
                    var tr = document.createElement("tr");

                    tr.addEventListener("click", function () {
                        window.location.hash = "sessions/" + s.id;
                    });

                    var tdTitle = document.createElement("td");
                    tdTitle.textContent = s.title;
                    tr.appendChild(tdTitle);

                    var tdStatus = document.createElement("td");
                    var dot = document.createElement("span");
                    dot.className = "status-dot " + s.status;
                    tdStatus.appendChild(dot);
                    var statusText = document.createTextNode(s.status);
                    tdStatus.appendChild(statusText);
                    tr.appendChild(tdStatus);

                    var tdParticipants = document.createElement("td");
                    tdParticipants.textContent = s.participant_count;
                    tr.appendChild(tdParticipants);

                    var tdMessages = document.createElement("td");
                    tdMessages.textContent = s.message_count;
                    tr.appendChild(tdMessages);

                    tbody.appendChild(tr);
                });
                table.appendChild(tbody);
                container.appendChild(table);
            });
    }

    function loadSessions(selectId) {
        var searchInput = document.getElementById("session-search");
        var filterBtns = document.querySelectorAll(".filter-btn");
        var activeFilter = "";

        filterBtns.forEach(function (btn) {
            if (btn.classList.contains("active")) {
                activeFilter = btn.getAttribute("data-status");
            }
        });

        var q = searchInput.value || "";
        var url = "/admin/api/sessions?status=" + encodeURIComponent(activeFilter) + "&q=" + encodeURIComponent(q);

        fetch(url)
            .then(function (r) { return r.json(); })
            .then(function (data) {
                var list = document.getElementById("session-list");
                list.textContent = "";

                data.sessions.forEach(function (s) {
                    var item = document.createElement("div");
                    item.className = "session-item";
                    if (selectId && s.id === selectId) {
                        item.classList.add("active");
                    }
                    item.addEventListener("click", function () {
                        window.location.hash = "sessions/" + s.id;
                    });

                    var title = document.createElement("div");
                    title.className = "session-item-title";
                    title.textContent = s.title;
                    item.appendChild(title);

                    var meta = document.createElement("div");
                    meta.className = "session-item-meta";

                    var dot = document.createElement("span");
                    dot.className = "status-dot " + s.status;
                    meta.appendChild(dot);

                    var statusText = document.createTextNode(
                        s.status + " · " + s.participant_count + " participants · " + s.message_count + " messages"
                    );
                    meta.appendChild(statusText);

                    item.appendChild(meta);
                    list.appendChild(item);
                });
            });

        if (!searchInput.dataset.bound) {
            searchInput.dataset.bound = "1";
            searchInput.addEventListener("input", function () {
                var hash = window.location.hash.replace(/^#/, "");
                var parts = hash.split("/");
                loadSessions(parts[1] || null);
            });
        }

        if (!filterBtns[0].dataset.bound) {
            filterBtns.forEach(function (btn) {
                btn.dataset.bound = "1";
                btn.addEventListener("click", function () {
                    filterBtns.forEach(function (b) { b.classList.remove("active"); });
                    btn.classList.add("active");
                    var hash = window.location.hash.replace(/^#/, "");
                    var parts = hash.split("/");
                    loadSessions(parts[1] || null);
                });
            });
        }

        if (selectId) {
            loadSessionDetail(selectId);
        }
    }

    function loadSessionDetail(sessionId) {
        fetch("/admin/api/sessions/" + sessionId)
            .then(function (r) {
                if (!r.ok) throw new Error("Not found");
                return r.json();
            })
            .then(function (session) {
                var detail = document.getElementById("session-detail");
                detail.textContent = "";

                var header = document.createElement("div");
                header.className = "detail-header";

                var titleEl = document.createElement("div");
                titleEl.className = "detail-title";
                titleEl.textContent = session.title;
                header.appendChild(titleEl);

                var metaEl = document.createElement("div");
                metaEl.className = "detail-meta";

                var dot = document.createElement("span");
                dot.className = "status-dot " + session.status;
                metaEl.appendChild(dot);

                var statusText = document.createTextNode(
                    session.status + " · Created " + new Date(session.created_at).toLocaleString()
                );
                metaEl.appendChild(statusText);
                header.appendChild(metaEl);

                if (currentRole === "admin") {
                    var actions = document.createElement("div");
                    actions.className = "detail-actions";

                    if (session.status === "active") {
                        var endBtn = document.createElement("button");
                        endBtn.className = "btn btn-danger";
                        endBtn.textContent = "End Session";
                        endBtn.addEventListener("click", function () {
                            fetch("/admin/api/sessions/" + sessionId + "/end", { method: "POST" })
                                .then(function () {
                                    loadSessions(sessionId);
                                });
                        });
                        actions.appendChild(endBtn);
                    } else {
                        var reopenBtn = document.createElement("button");
                        reopenBtn.className = "btn btn-success";
                        reopenBtn.textContent = "Reopen Session";
                        reopenBtn.addEventListener("click", function () {
                            fetch("/admin/api/sessions/" + sessionId + "/reopen", { method: "POST" })
                                .then(function () {
                                    loadSessions(sessionId);
                                });
                        });
                        actions.appendChild(reopenBtn);
                    }

                    header.appendChild(actions);
                }

                detail.appendChild(header);

                var sessionUrl = window.location.origin + "/session/" + sessionId;
                var urlRow = document.createElement("div");
                urlRow.className = "session-url-row";

                var link = document.createElement("a");
                link.href = sessionUrl;
                link.target = "_blank";
                link.textContent = sessionUrl;
                urlRow.appendChild(link);

                var copyBtn = document.createElement("button");
                copyBtn.className = "copy-btn";
                copyBtn.textContent = "Copy";
                copyBtn.addEventListener("click", function () {
                    navigator.clipboard.writeText(sessionUrl).then(function () {
                        copyBtn.textContent = "Copied!";
                        setTimeout(function () {
                            copyBtn.textContent = "Copy";
                        }, 2000);
                    });
                });
                urlRow.appendChild(copyBtn);
                detail.appendChild(urlRow);

                var participantLabel = document.createElement("div");
                participantLabel.className = "section-label";
                participantLabel.textContent = "PARTICIPANTS (" + session.participants.length + ")";
                detail.appendChild(participantLabel);

                var participantList = document.createElement("div");
                participantList.className = "participant-list";

                var now = new Date();
                session.participants.forEach(function (p) {
                    var chip = document.createElement("div");
                    chip.className = "participant-chip";

                    var pDot = document.createElement("span");
                    pDot.className = "participant-dot";
                    if (p.last_seen) {
                        var lastSeen = new Date(p.last_seen);
                        var diffMin = (now - lastSeen) / 60000;
                        pDot.classList.add(diffMin <= 5 ? "online" : "offline");
                    } else {
                        pDot.classList.add("offline");
                    }
                    chip.appendChild(pDot);

                    var nameEl = document.createElement("span");
                    nameEl.textContent = p.name;
                    chip.appendChild(nameEl);

                    participantList.appendChild(chip);
                });
                detail.appendChild(participantList);

                var messageLabel = document.createElement("div");
                messageLabel.className = "section-label";
                messageLabel.textContent = "MESSAGES (" + session.messages.length + ")";
                detail.appendChild(messageLabel);

                var messagesList = document.createElement("div");
                messagesList.className = "messages-list";

                session.messages.forEach(function (m) {
                    var msgDiv = document.createElement("div");
                    msgDiv.className = "admin-message";

                    var authorEl = document.createElement("div");
                    authorEl.className = "msg-author " + (m.author === "ai" ? "ai" : "human");
                    authorEl.textContent = m.author;

                    if (m.metadata) {
                        Object.keys(m.metadata).forEach(function (key) {
                            var tag = document.createElement("span");
                            tag.className = "msg-tag";
                            tag.textContent = key + ": " + m.metadata[key];
                            authorEl.appendChild(tag);
                        });
                    }

                    msgDiv.appendChild(authorEl);

                    var textEl = document.createElement("div");
                    textEl.className = "msg-text";
                    textEl.innerHTML = marked.parse(m.text);
                    msgDiv.appendChild(textEl);

                    var timeEl = document.createElement("div");
                    timeEl.className = "msg-time";
                    timeEl.textContent = new Date(m.timestamp).toLocaleString();
                    msgDiv.appendChild(timeEl);

                    messagesList.appendChild(msgDiv);
                });
                detail.appendChild(messagesList);
            })
            .catch(function () {
                var detail = document.getElementById("session-detail");
                detail.textContent = "";
                var p = document.createElement("p");
                p.className = "placeholder-text";
                p.textContent = "Session not found";
                detail.appendChild(p);
            });
    }

    function loadSettings() {
        var content = document.getElementById("settings-content");
        content.textContent = "";

        fetch("/admin/api/roles")
            .then(function (r) { return r.json(); })
            .then(function (roles) {
                var section = document.createElement("div");
                section.className = "settings-section";

                var heading = document.createElement("div");
                heading.className = "section-label";
                heading.textContent = "ROLES";
                section.appendChild(heading);

                var table = document.createElement("table");
                table.className = "roles-table";

                var thead = document.createElement("thead");
                var headerRow = document.createElement("tr");
                var headers = ["GitHub Username", "Role"];
                if (currentRole === "admin") {
                    headers.push("Actions");
                }
                headers.forEach(function (h) {
                    var th = document.createElement("th");
                    th.textContent = h;
                    headerRow.appendChild(th);
                });
                thead.appendChild(headerRow);
                table.appendChild(thead);

                var tbody = document.createElement("tbody");
                var allUsers = [];
                Object.keys(roles).forEach(function (role) {
                    roles[role].forEach(function (user) {
                        allUsers.push({ username: user, role: role });
                    });
                });

                allUsers.forEach(function (u) {
                    var tr = document.createElement("tr");

                    var tdUser = document.createElement("td");
                    tdUser.textContent = u.username;
                    tr.appendChild(tdUser);

                    var tdRole = document.createElement("td");
                    tdRole.textContent = u.role;
                    tr.appendChild(tdRole);

                    if (currentRole === "admin") {
                        var tdAction = document.createElement("td");
                        var removeBtn = document.createElement("button");
                        removeBtn.className = "btn btn-danger";
                        removeBtn.textContent = "Remove";
                        removeBtn.addEventListener("click", function () {
                            var updated = {};
                            Object.keys(roles).forEach(function (r) {
                                updated[r] = roles[r].filter(function (name) {
                                    return name !== u.username;
                                });
                            });
                            fetch("/admin/api/roles", {
                                method: "PUT",
                                headers: { "Content-Type": "application/json" },
                                body: JSON.stringify(updated),
                            }).then(function () { loadSettings(); });
                        });
                        tdAction.appendChild(removeBtn);
                        tr.appendChild(tdAction);
                    }

                    tbody.appendChild(tr);
                });
                table.appendChild(tbody);
                section.appendChild(table);

                if (currentRole === "admin") {
                    var addRow = document.createElement("div");
                    addRow.className = "add-role-row";

                    var input = document.createElement("input");
                    input.type = "text";
                    input.placeholder = "GitHub username";
                    addRow.appendChild(input);

                    var select = document.createElement("select");
                    ["admin", "viewer"].forEach(function (r) {
                        var opt = document.createElement("option");
                        opt.value = r;
                        opt.textContent = r;
                        select.appendChild(opt);
                    });
                    addRow.appendChild(select);

                    var addBtn = document.createElement("button");
                    addBtn.className = "btn btn-success";
                    addBtn.textContent = "Add";
                    addBtn.addEventListener("click", function () {
                        var username = input.value.trim();
                        if (!username) return;
                        var role = select.value;
                        var updated = {};
                        Object.keys(roles).forEach(function (r) {
                            updated[r] = roles[r].slice();
                        });
                        if (!updated[role]) {
                            updated[role] = [];
                        }
                        updated[role].push(username);
                        fetch("/admin/api/roles", {
                            method: "PUT",
                            headers: { "Content-Type": "application/json" },
                            body: JSON.stringify(updated),
                        }).then(function () { loadSettings(); });
                    });
                    addRow.appendChild(addBtn);
                    section.appendChild(addRow);
                }

                content.appendChild(section);

                if (currentRole === "admin") {
                    fetch("/admin/api/oauth-status")
                        .then(function (r) { return r.json(); })
                        .then(function (oauth) {
                            var oauthSection = document.createElement("div");
                            oauthSection.className = "settings-section";

                            var oauthHeading = document.createElement("div");
                            oauthHeading.className = "section-label";
                            oauthHeading.textContent = "OAUTH CONFIGURATION";
                            oauthSection.appendChild(oauthHeading);

                            var dl = document.createElement("dl");
                            dl.className = "oauth-info";

                            var items = [
                                { label: "Status", value: oauth.configured ? "Configured" : "Not configured" },
                                { label: "Client ID", value: oauth.client_id },
                                { label: "Callback URL", value: oauth.callback_url },
                            ];

                            items.forEach(function (item) {
                                var dt = document.createElement("dt");
                                dt.textContent = item.label;
                                dl.appendChild(dt);

                                var dd = document.createElement("dd");
                                dd.textContent = item.value;
                                dl.appendChild(dd);
                            });

                            oauthSection.appendChild(dl);
                            content.appendChild(oauthSection);
                        });
                }
            });
    }

    document.addEventListener("DOMContentLoaded", init);
})();
