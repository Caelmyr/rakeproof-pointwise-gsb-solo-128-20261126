"use strict";

const $ = (id) => document.getElementById(id);
const state = {
    card: null, draft: null, report: null, photos: null, mode: "probe",
    drag: null, point: null, busy: false, pointRequest: 0,
    proposal: null, grooveCheck: { signature: "", left: false, right: false },
};
const fields = ["dx", "dy", "scan_y", "threshold"];
const canvas = $("photo");
const profileCanvas = $("profile");

function status(message, error = false) {
    $("status").textContent = message;
    $("status").classList.toggle("error", error);
}

async function api(path, options = {}) {
    const response = await fetch(path, options);
    const value = await response.json().catch(() => ({}));
    if (!response.ok) {
        throw new Error(value.error || `请求失败（${response.status}）`);
    }
    return value;
}

function asJson(method, payload) {
    return { method, headers: { "Content-Type": "application/json" }, body: JSON.stringify(payload) };
}

function cardPath(card, path = "") {
    return `/rake/cards/${encodeURIComponent(card.id)}${path}`;
}

function adoptedPath(card, path, query = {}) {
    return cardPath(card, `${path}?${new URLSearchParams({ rev: card.revision, ...query })}`);
}

function image(path) {
    return new Promise((resolve, reject) => {
        const photo = new Image();
        photo.onload = () => resolve(photo);
        photo.onerror = () => reject(new Error("预览加载失败，请重开核验卡"));
        photo.src = path;
    });
}

async function doWork(fn) {
    if (state.busy) return;
    state.busy = true;
    document.body.setAttribute("aria-busy", "true");
    $("apply").disabled = true;
    try {
        await fn();
    } catch (error) {
        status(error.message, true);
    } finally {
        state.busy = false;
        document.body.setAttribute("aria-busy", "false");
        $("apply").disabled = false;
    }
}

async function listCards() {
    const list = await api("/rake/cards");
    const current = state.card?.id || $("cards").value;
    const options = list.cards.map((row) => new Option(`${row.name} · ${row.revision}版`, row.id));
    $("cards").replaceChildren(new Option("选择卡片", ""), ...options);
    if ([...$("cards").options].some((option) => option.value === current)) $("cards").value = current;
}

function boxText(box) {
    return `X ${box.left}–${box.right - 1} / Y ${box.top}–${box.bottom - 1}`;
}

function updateBoxText() {
    $("zone-read").textContent = `量区 ${boxText(state.draft.zone)}`;
    $("flat-read").textContent = `平面 ${boxText(state.draft.flat)}`;
}

function setMode(mode) {
    state.mode = mode;
    state.drag = null;
    for (const button of document.querySelectorAll("[data-mode]")) {
        button.setAttribute("aria-pressed", String(button.dataset.mode === mode));
    }
    draw();
}

function showCard() {
    const { card, report } = state;
    $("workspace").hidden = false;
    $("card-title").textContent = card.name;
    function photoLabel(side) {
        const field = side === "left" ? "左照" : "右照";
        return `${field} ${card[`${side}_name`]} · ${card[`${side}_size`].join(" × ")}`;
    }
    $("card-meta").textContent = `${card.synthetic ? "合成练习 / " : ""}${photoLabel("left")} / ${photoLabel("right")}`;
    $("revision").textContent = `已应用第 ${card.revision} 版`;
    for (const key of fields) $(key).value = state.draft[key];
    $("dx").min = 1 - card.right_size[0];
    $("dx").max = card.left_size[0] - 1;
    $("dy").min = 1 - card.right_size[1];
    $("dy").max = card.left_size[1] - 1;
    $("scan_y").min = 0;
    $("scan_y").max = card.left_size[1] - 1;
    $("point-x").max = card.left_size[0] - 1;
    $("point-y").max = card.left_size[1] - 1;
    $("groove-x").max = card.left_size[0] - 1;
    $("groove-y").max = card.left_size[1] - 1;
    $("groove-x").value = report.groove?.seed?.x ?? "";
    $("groove-y").value = report.groove?.seed?.y ?? state.draft.scan_y;
    $("balance").checked = state.draft.balance;
    updateBoxText();
    const { calibration, counts, section } = report;
    const gainLabel = {
        applied: `右照平衡倍率 ${calibration.gain.toFixed(3)} · ${calibration.support.toLocaleString()} 对平面点`,
        off: "平面亮度平衡未启用",
        insufficient: "共同平面上的可用点不足，沿用原照亮度",
        outside_range: "平衡倍率超出 0.4–2.5，沿用原照亮度",
    };
    $("gain").textContent = gainLabel[calibration.state];
    $("paired-count").textContent = `${counts.paired.toLocaleString()} / ${counts.zone.toLocaleString()}`;
    $("valid-count").textContent = counts.valid.toLocaleString();
    $("positive-count").textContent = counts.positive.toLocaleString();
    $("negative-count").textContent = counts.negative.toLocaleString();
    $("run-count").textContent = section.runs.length;
    $("section-range").textContent = section.samples.length
        ? `左照 Y=${section.y} · X=${section.origin} 至 ${section.samples.at(-1).x}`
        : `左照 Y=${section.y} · 量区内没有双照覆盖`;
    const buttons = section.runs.map((run) => {
        const button = document.createElement("button");
        button.type = "button";
        button.dataset.sign = run.direction;
        button.textContent = `${run.direction > 0 ? "左亮" : "右亮"} ${run.start}–${run.end - 1} · 峰值 ${run.peak_value}%`;
        button.addEventListener("click", () => readPoint(run.peak_x, section.y));
        return button;
    });
    $("runs").replaceChildren(...buttons);
    renderGroove();
    for (const link of document.querySelectorAll("[data-export]")) {
        link.href = adoptedPath(card, "/" + link.dataset.export);
    }
    $("readout").textContent = "点原片或下方的横切线，也可输入左照坐标。";
    $("point-x").value = "";
    $("point-y").value = "";
    canvas.width = card.left_size[0];
    canvas.height = card.left_size[1];
    draw();
    drawProfile();
}

async function openCard(card) {
    const reusable = card.id === state.card?.id;
    const [report, left, right, map] = await Promise.all([
        api(adoptedPath(card, "/report")),
        reusable ? state.photos.left : image(cardPath(card, "/source/left.png")),
        reusable ? state.photos.right : image(cardPath(card, "/source/right.png")),
        image(adoptedPath(card, "/map.png")),
    ]);
    state.card = card;
    state.draft = structuredClone(card.settings);
    state.report = report;
    state.photos = { left, right, map };
    state.point = null;
    state.proposal = null;
    state.grooveCheck = { signature: "", left: false, right: false };
    state.pointRequest += 1;
    setMode("probe");
    showCard();
    await listCards();
}

function drawBox(ctx, box, color, preview = false) {
    ctx.strokeStyle = color;
    ctx.setLineDash(preview ? [5, 4] : []);
    ctx.strokeRect(box.left + 0.5, box.top + 0.5, Math.max(0, box.right - box.left - 1), Math.max(0, box.bottom - box.top - 1));
    ctx.setLineDash([]);
}

function drawTrackSide(ctx, rows, side, color, activeY) {
    const xKey = side === "left" ? "left_x" : "right_x";
    ctx.strokeStyle = color;
    ctx.fillStyle = color;
    ctx.lineWidth = Math.max(1, canvas.width / 850);
    for (let index = 1; index < rows.length; index += 1) {
        const prev = rows[index - 1];
        const row = rows[index];
        if (!prev.measured || !row.measured) continue;
        ctx.globalAlpha = row.y === prev.y + 1 ? 0.72 : 0.38;
        ctx.setLineDash(row.y === prev.y + 1 ? [] : [4, 4]);
        ctx.beginPath();
        ctx.moveTo(prev[xKey] + 0.5, prev.y + 0.5);
        ctx.lineTo(row[xKey] + 0.5, row.y + 0.5);
        ctx.stroke();
    }
    ctx.setLineDash([]);
    const smallRadius = Math.max(1.8, canvas.width / 260);
    for (const row of rows) {
        if (!row.measured) continue;
        ctx.globalAlpha = row.y === activeY ? 1 : 0.55;
        ctx.beginPath();
        ctx.arc(row[xKey] + 0.5, row.y + 0.5, row.y === activeY ? smallRadius * 1.7 : smallRadius, 0, Math.PI * 2);
        ctx.fill();
    }
    ctx.globalAlpha = 1;
}

function drawGroove(ctx) {
    const activeY = grooveY();
    const adopted = adoptedGroove();
    if (adopted?.rows?.length) {
        const rows = adopted.rows;
        drawTrackSide(ctx, rows, "left", "#73b5e4", activeY);
        drawTrackSide(ctx, rows, "right", "#f0a474", activeY);
        const row = activeGrooveRow();
        if (row?.measured) {
            const y = row.y + 0.5;
            ctx.strokeStyle = "rgba(229, 213, 148, 0.95)";
            ctx.lineWidth = Math.max(1.4, canvas.width / 430);
            ctx.beginPath();
            ctx.moveTo(row.left_x + 0.5, y);
            ctx.lineTo(row.right_x + 0.5, y);
            ctx.stroke();
            const radius = Math.max(5, canvas.width / 130);
            for (const [x, color] of [[row.left_x, "#73b5e4"], [row.right_x, "#f0a474"]]) {
                ctx.beginPath();
                ctx.arc(x + 0.5, row.y + 0.5, radius, 0, Math.PI * 2);
                ctx.fillStyle = color;
                ctx.fill();
                ctx.lineWidth = Math.max(1.5, canvas.width / 500);
                ctx.strokeStyle = "#ffffff";
                ctx.stroke();
            }
            const label = `${row.width} px`;
            ctx.font = `650 ${Math.max(11, canvas.width / 55)}px "Noto Sans SC", sans-serif`;
            const textWidth = ctx.measureText(label).width;
            const labelX = Math.max(2, Math.min(canvas.width - textWidth - 10, (row.left_x + row.right_x) / 2 - textWidth / 2));
            const labelY = Math.max(14, row.y - 9);
            ctx.fillStyle = "rgba(17,20,20,.82)";
            ctx.fillRect(labelX - 4, labelY - 12, textWidth + 8, 17);
            ctx.fillStyle = "#f0eee2";
            ctx.fillText(label, labelX, labelY + 1);
        }
    }
    if (state.proposal?.adoption?.rows?.length) {
        ctx.save();
        ctx.globalAlpha = adopted ? 0.35 : 0.8;
        ctx.setLineDash([6, 4]);
        ctx.strokeStyle = "#fff1a2";
        ctx.lineWidth = Math.max(1.2, canvas.width / 650);
        for (const side of ["left_x", "right_x"]) {
            ctx.beginPath();
            let started = false;
            for (const row of state.proposal.adoption.rows) {
                if (!row.measured) { started = false; continue; }
                if (!started) ctx.moveTo(row[side] + .5, row.y + .5);
                else ctx.lineTo(row[side] + .5, row.y + .5);
                started = true;
            }
            ctx.stroke();
        }
        ctx.restore();
    }
}

function draw() {
    if (!state.photos) return;
    const ctx = canvas.getContext("2d");
    const { left, right, map } = state.photos;
    const { dx, dy, scan_y, zone, flat } = state.card.settings;
    const view = $("view").value;
    ctx.clearRect(0, 0, canvas.width, canvas.height);
    ctx.fillStyle = "#111515";
    ctx.fillRect(0, 0, canvas.width, canvas.height);
    if (view !== "right") ctx.drawImage(left, 0, 0);
    if (view === "map") ctx.drawImage(map, 0, 0);
    if (view === "right" || view === "blend") {
        ctx.globalAlpha = view === "blend" ? 0.56 : 1;
        ctx.drawImage(right, state.draft.dx, state.draft.dy);
        ctx.globalAlpha = 1;
    }
    ctx.lineWidth = Math.max(1, canvas.width / 730);
    drawBox(ctx, zone, "#fff1a2");
    drawBox(ctx, flat, "#8ef0bc");
    for (const [name, color] of [["zone", "#fff1a2"], ["flat", "#8ef0bc"]]) {
        if (boxText(state.draft[name]) !== boxText(state.card.settings[name])) {
            drawBox(ctx, state.draft[name], color, true);
        }
    }
    function scan(y, dashed) {
        ctx.strokeStyle = "#fff1a2";
        ctx.setLineDash(dashed ? [7, 5] : []);
        ctx.beginPath();
        ctx.moveTo(0, y + 0.5);
        ctx.lineTo(canvas.width, y + 0.5);
        ctx.stroke();
        ctx.setLineDash([]);
    }
    scan(scan_y, true);
    if (state.draft.scan_y !== scan_y) scan(state.draft.scan_y, false);
    if (state.drag) {
        const color = state.mode === "zone" ? "#fff1a2" : "#8ef0bc";
        drawBox(ctx, dragBox(state.drag.start, state.drag.end), color, true);
    }
    drawGroove(ctx);
    if (state.point) {
        const [x, y] = state.point;
        ctx.strokeStyle = "#fff";
        ctx.beginPath();
        ctx.arc(x + 0.5, y + 0.5, Math.max(4, canvas.width / 150), 0, Math.PI * 2);
        ctx.stroke();
    }
}

function drawProfile() {
    if (!state.report) return;
    const section = state.report.section;
    const { samples } = section;
    const width = Math.max(1, Math.round(profileCanvas.clientWidth * window.devicePixelRatio));
    const height = Math.round(160 * window.devicePixelRatio);
    profileCanvas.width = width;
    profileCanvas.height = height;
    const ctx = profileCanvas.getContext("2d");
    ctx.strokeStyle = "#667771";
    ctx.beginPath();
    ctx.moveTo(0, height / 2);
    ctx.lineTo(width, height / 2);
    ctx.stroke();
    const max = samples.reduce((result, item) => Math.max(result, Math.abs(item.value ?? 0)), state.card.settings.threshold);
    const denom = Math.max(1, samples.length);
    ctx.lineWidth = Math.max(1, window.devicePixelRatio);
    ctx.strokeStyle = "#e3d79e";
    ctx.beginPath();
    let connected = false;
    samples.forEach((sample, index) => {
        if (sample.value === null) {
            connected = false;
            return;
        }
        const x = (index + .5) * width / denom;
        const y = height * (0.5 - sample.value / (max * 2.3));
        if (connected) ctx.lineTo(x, y);
        else ctx.moveTo(x, y);
        connected = true;
    });
    ctx.stroke();
    const grooveRow = grooveRowAt(section.y);
    if (grooveRow?.measured) {
        const origin = section.samples[0]?.x ?? section.origin;
        for (const [xValue, color] of [[grooveRow.left_x, "#73b5e4"], [grooveRow.right_x, "#f0a474"]]) {
            const x = (xValue - origin + .5) * width / denom;
            ctx.strokeStyle = color;
            ctx.lineWidth = Math.max(1.5, window.devicePixelRatio * 1.4);
            ctx.beginPath();
            ctx.moveTo(x, 0);
            ctx.lineTo(x, height);
            ctx.stroke();
            ctx.fillStyle = color;
            ctx.beginPath();
            ctx.arc(x, height / 2, Math.max(3, window.devicePixelRatio * 3), 0, Math.PI * 2);
            ctx.fill();
        }
    }
}

function pointerPoint(event) {
    const rect = canvas.getBoundingClientRect();
    const x = Math.floor((event.clientX - rect.left) * canvas.width / rect.width);
    const y = Math.floor((event.clientY - rect.top) * canvas.height / rect.height);
    return [Math.max(0, Math.min(x, canvas.width - 1)), Math.max(0, Math.min(y, canvas.height - 1))];
}

function dragBox(start, end) {
    return {
        left: Math.min(start[0], end[0]), top: Math.min(start[1], end[1]),
        right: Math.max(start[0], end[0]) + 1, bottom: Math.max(start[1], end[1]) + 1,
    };
}

function pendingSettings() {
    status("当前调整待应用。原像复查和下载仍使用已应用设置。");
}

function adoptedGroove() {
    return state.report?.groove || state.card?.groove || null;
}

function grooveY() {
    return Number.isFinite(state.draft?.scan_y) ? state.draft.scan_y : state.report?.section?.y;
}

function grooveRowAt(y) {
    return adoptedGroove()?.rows?.find((row) => row.y === y) || null;
}

function activeGrooveRow() {
    const y = grooveY();
    return Number.isInteger(y) ? grooveRowAt(y) : null;
}

function syncGrooveCheck(row) {
    const signature = row?.measured ? `${row.y}:${row.left_x}:${row.right_x}` : "";
    if (state.grooveCheck.signature !== signature) {
        state.grooveCheck = { signature, left: false, right: false };
    }
}

function renderGrooveTable(groove, activeY) {
    const tbody = $("groove-rows");
    if (!tbody) return;
    if (!groove?.rows?.length) {
        tbody.replaceChildren();
        return;
    }
    const fragment = document.createDocumentFragment();
    for (const row of groove.rows) {
        const tr = document.createElement("tr");
        tr.dataset.y = row.y;
        tr.className = [
            row.y === activeY ? "active" : "",
            row.measured ? "" : "blank",
        ].filter(Boolean).join(" ");
        const status = row.measured ? "已量" : row.reason === "dark_spot" ? "暗斑，宽度空" : "缺证，宽度空";
        for (const value of [row.y, row.width ?? "", row.left_x ?? "", row.right_x ?? "", status]) {
            const td = document.createElement("td");
            td.textContent = String(value);
            tr.appendChild(td);
        }
        fragment.appendChild(tr);
    }
    tbody.replaceChildren(fragment);
}

function renderGroove() {
    const proposalBox = $("groove-proposal");
    const proposalActions = $("groove-proposal-actions");
    if (state.proposal?.recommendation) {
        const p = state.proposal;
        const s = p.adoption?.summary || p.recommendation?.summary || p.summary;
        proposalBox.hidden = false;
        proposalBox.textContent = [
            p.recommendation.message,
            s ? `建议范围 Y ${s.start_y}–${s.end_y} · 可量 ${s.measured_rows} 行 · 暗斑留空 ${s.gap_rows} 行 · 平均峰距 ${s.mean_width ?? "—"} px` : "",
        ].filter(Boolean).join("\n");
        proposalActions.hidden = false;
    } else {
        proposalBox.hidden = true;
        proposalBox.textContent = "";
        proposalActions.hidden = true;
    }

    const groove = adoptedGroove();
    $("groove-sheet").dataset.adopted = groove ? "true" : "false";
    const y = grooveY();
    const row = Number.isInteger(y) ? grooveRowAt(y) : null;
    syncGrooveCheck(row);
    const active = $("groove-active");
    const edgeActions = $("groove-edge-actions");
    const metric = $("groove-width-metric");
    edgeActions.hidden = !row?.measured;

    if (!groove) {
        active.textContent = "尚未采用追踪。选择「追踪浅沟」并点中横切线上的一条沟。";
        metric.textContent = "—";
        metric.dataset.state = "none";
        $("groove-check").textContent = "";
        renderGrooveTable(null, y);
        return;
    }
    renderGrooveTable(groove, y);

    if (row?.measured) {
        metric.textContent = `${row.width} px`;
        metric.dataset.state = "measured";
        const ls = row.left_source || {};
        const rs = row.right_source || {};
        active.textContent = [
            `已采用本沟 Y ${row.y}：左坡 X ${row.left_x}（右照原像 ${ls.right_x ?? "?"}, ${ls.right_y ?? "?"}，相对右亮 ${row.left_contrast ?? "?"}%）`,
            `右坡 X ${row.right_x}（右照原像 ${rs.right_x ?? "?"}, ${rs.right_y ?? "?"}，相对左亮 ${row.right_contrast ?? "?"}%）`,
            `同一原行两坡峰距 ${row.width} px；较弱坡峰反差 ${row.weak_contrast ?? "?"}%。`,
        ].join("\n");
    } else if (row?.gap) {
        metric.textContent = "留空";
        metric.dataset.state = "blank";
        active.textContent = `Y ${y} 是${row.reason === "dark_spot" ? "短暗斑或缺证" : "未能对接"}行：本行宽度留空；前后坡源能对上时已接续，不用这里的坡替量。`;
    } else if (Number.isInteger(y) && y >= groove.start_y && y <= groove.end_y) {
        metric.textContent = "留空";
        metric.dataset.state = "blank";
        active.textContent = `Y ${y} 没有已采用的成对坡源，宽度留空；不会用邻沟坡峰替这道沟量宽。`;
    } else {
        metric.textContent = "—";
        metric.dataset.state = "outside";
        active.textContent = `Y ${y} 不在本沟已采用范围 Y ${groove.start_y}–${groove.end_y}。此处可做点读复查，但不会改动已采用沟宽。`;
    }

    if (row?.measured) {
        const checked = [];
        if (state.grooveCheck.left) checked.push("左坡");
        if (state.grooveCheck.right) checked.push("右坡");
        if (state.grooveCheck.left && state.grooveCheck.right) {
            $("groove-check").textContent = `两段来源均已点中；两照原像都保留读数，坐标峰距 ${row.width} px，与采用宽度一致。`;
        } else if (checked.length) {
            const remaining = state.grooveCheck.left ? "右坡" : "左坡";
            $("groove-check").textContent = `已复查${checked.join("、")}；请再点中${remaining}来源以核对 ${row.width} px 峰距。`;
        } else {
            $("groove-check").textContent = "可点图上放大的蓝/橙坡峰圆点，或用右侧按钮点中本行采用的两段来源。";
        }
    } else {
        $("groove-check").textContent = "";
    }
}

function grooveNoteForPoint(x, y) {
    const groove = adoptedGroove();
    if (!groove || !Number.isInteger(y)) return [];
    const row = grooveRowAt(y);
    if (row?.measured && (x === row.left_x || x === row.right_x)) {
        const edge = x === row.left_x ? "left" : "right";
        syncGrooveCheck(row);
        state.grooveCheck[edge] = true;
        renderGroove();
        const edgeName = edge === "left" ? "左坡（右照相对亮/蓝）" : "右坡（左照相对亮/橙）";
        return [
            `✓ 点中本沟本行采用的${edgeName}来源 (${x}, ${y})。`,
            `同一原行两坡峰距 = ${row.right_x} − ${row.left_x} = ${row.width} px；另一坡也复查后即可核对两段来源。`,
        ];
    }
    if (row?.gap) {
        return [`Y ${y} 是暗斑/缺证行：以上两照原像读数保留；本沟本行宽度留空，不拿这里的坡替量。`];
    }
    if (row?.measured) {
        return [`该点不是 Y ${y} 已采用的两段坡源；以上原像读数保留。本沟仍按 X ${row.left_x}/${row.right_x} 量 ${row.width} px，不按邻沟或其他坡段改宽。`];
    }
    return [`Y ${y} 不在本沟已采用范围 Y ${groove.start_y}–${groove.end_y}；以上原像读数保留，不拿邻沟坡峰替这道沟量宽。`];
}

function snapGroovePoint(x, y) {
    const row = activeGrooveRow();
    if (!row?.measured) return null;
    const yRadius = Math.max(4, canvas.width / 120);
    const xRadius = Math.max(7, canvas.width / 90);
    if (Math.abs(y - row.y) > yRadius) return null;
    for (const edge of ["left", "right"]) {
        const edgeX = edge === "left" ? row.left_x : row.right_x;
        if (Math.abs(x - edgeX) <= xRadius) return { x: edgeX, y: row.y, edge };
    }
    return null;
}

async function requestGroove(x, y) {
    if (!state.card || state.busy) return;
    $("groove-x").value = x;
    $("groove-y").value = y;
    await doWork(async () => {
        const result = await api(adoptedPath(state.card, "/groove/proposal", { x, y }));
        state.proposal = result;
        renderGroove();
        draw();
        status(result.recommendation?.message || "已给出浅沟追踪建议，请确认采用范围。");
    });
}

async function adoptProposal(choice) {
    const seed = state.proposal?.seed;
    if (!state.card || !seed || state.busy) return;
    await doWork(async () => {
        const response = await api(cardPath(state.card, "/groove/adopt"), asJson("POST", {
            revision: state.card.revision, x: seed.x, y: seed.y, choice,
        }));
        await openCard(response.card);
        status("已采用这道沟的成对坡峰追踪；移动横切线行或点蓝/橙坡源可复查宽度。");
    });
}

async function whenIdle(timeoutMs = 4000) {
    const start = Date.now();
    while (state.busy && Date.now() - start < timeoutMs) {
        await new Promise((resolve) => setTimeout(resolve, 40));
    }
}

async function reviewGrooveEdge(edge) {
    const row = activeGrooveRow();
    if (!row?.measured) return;
    await whenIdle();
    if (state.busy || !state.card) return;
    const x = edge === "left" ? row.left_x : row.right_x;
    syncGrooveCheck(row);
    state.grooveCheck[edge] = true;
    renderGroove();
    await readPoint(x, row.y, { grooveEdge: edge });
}

async function reviewBothGrooveEdges() {
    await reviewGrooveEdge("left");
    await reviewGrooveEdge("right");
}

async function readPoint(x, y, options = {}) {
    if (!state.card || state.busy) return null;
    const request = ++state.pointRequest;
    try {
        const result = await api(adoptedPath(state.card, "/probe", { x, y }));
        if (request !== state.pointRequest) return null;
        state.point = [x, y];
        $("point-x").value = x;
        $("point-y").value = y;
        const describe = (title, data) => data
            ? `${title} (${data.x}, ${data.y}) · RGB ${data.rgb.join(", ")} · 灰度 ${data.gray}`
            : `${title} 无对应像素`;
        const note = {
            outside_zone: "点在量区外，保留原像读数",
            exposure: "曝光不可用，坡向留空",
            unpaired: "两张原照片未覆盖同一位置，坡向留空",
            positive: "左照相对亮", negative: "右照相对亮", level: "坡向反差在阈值内",
        };
        const lines = [
            describe("左照", result.left), describe("右照", result.right),
            note[result.status] + (result.response === null ? "" : ` · ${result.response}%`),
            ...grooveNoteForPoint(x, y),
        ];
        $("readout").textContent = lines.join("\n");
        draw();
        return result;
    } catch (error) {
        if (request === state.pointRequest) $("readout").textContent = error.message;
        return null;
    }
}

$("practice").addEventListener("submit", (event) => {
    event.preventDefault();
    doWork(async () => {
        const card = await api("/rake/practice", asJson("POST", { sample: $("sample").value }));
        await openCard(card);
        status("已打开并保存合成核验卡。");
    });
});

$("upload").addEventListener("submit", (event) => {
    event.preventDefault();
    doWork(async () => {
        const card = await api("/rake/cards", { method: "POST", body: new FormData(event.currentTarget) });
        await openCard(card);
        $("upload").reset();
        $("upload-panel").open = false;
        status("两向斜照原片已导入。");
    });
});

$("reopen").addEventListener("submit", (event) => {
    event.preventDefault();
    const cardId = $("cards").value;
    if (cardId) doWork(async () => {
        await openCard(await api(cardPath({ id: cardId })));
        status("已重开核验卡及其上次应用的设置。");
    });
});

$("settings").addEventListener("submit", (event) => {
    event.preventDefault();
    if (!state.card) return;
    doWork(async () => {
        const next = await api(cardPath(state.card), asJson("PATCH", { revision: state.card.revision, ...state.draft }));
        await openCard(next);
        status("读数设置已应用并保存。");
    });
});

for (const key of fields) {
    $(key).addEventListener("input", () => {
        if (!state.card || !$(key).checkValidity()) return;
        state.draft[key] = $(key).valueAsNumber;
        pendingSettings();
        renderGroove();
        draw();
    });
}
$("balance").addEventListener("change", () => {
    state.draft.balance = $("balance").checked;
    pendingSettings();
});

$("point-form").addEventListener("submit", (event) => {
    event.preventDefault();
    readPoint($("point-x").valueAsNumber, $("point-y").valueAsNumber);
});

$("groove-form").addEventListener("submit", (event) => {
    event.preventDefault();
    const x = $("groove-x").valueAsNumber;
    const y = $("groove-y").valueAsNumber || grooveY();
    if (Number.isInteger(x) && Number.isInteger(y)) requestGroove(x, y);
});

$("groove-adopt").addEventListener("click", () => adoptProposal("recommended"));
$("groove-adopt-clear").addEventListener("click", () => adoptProposal("clearer"));
$("groove-adopt-long").addEventListener("click", () => adoptProposal("longest"));
$("groove-cancel").addEventListener("click", () => {
    state.proposal = null;
    renderGroove();
    draw();
    status("已取消建议；已采用的沟迹不变。");
});
$("review-left-edge").addEventListener("click", () => reviewGrooveEdge("left"));
$("review-right-edge").addEventListener("click", () => reviewGrooveEdge("right"));
$("review-both-edges").addEventListener("click", reviewBothGrooveEdges);
$("groove-rows").addEventListener("click", (event) => {
    const rowElement = event.target.closest("tr[data-y]");
    if (!rowElement || !state.card) return;
    const y = Number(rowElement.dataset.y);
    if (!Number.isInteger(y)) return;
    state.draft.scan_y = y;
    $("scan_y").value = y;
    pendingSettings();
    renderGroove();
    draw();
});

$("view").addEventListener("change", draw);
for (const button of document.querySelectorAll("[data-mode]")) {
    button.addEventListener("click", () => setMode(button.dataset.mode));
}
canvas.addEventListener("pointerdown", (event) => {
    if (!state.card || state.busy || !event.isPrimary || event.button !== 0) return;
    const [x, y] = pointerPoint(event);
    if (state.mode === "groove") {
        requestGroove(x, y);
    } else if (state.mode === "probe") {
        const snapped = snapGroovePoint(x, y);
        if (snapped) readPoint(snapped.x, snapped.y, { grooveEdge: snapped.edge });
        else readPoint(x, y);
    } else if (state.mode === "scan") {
        state.draft.scan_y = y;
        $("scan_y").value = y;
        pendingSettings();
        draw();
    } else {
        state.drag = { start: [x, y], end: [x, y] };
        canvas.setPointerCapture(event.pointerId);
        draw();
    }
});
canvas.addEventListener("pointermove", (event) => {
    if (!state.drag || !event.isPrimary) return;
    state.drag.end = pointerPoint(event);
    draw();
});
canvas.addEventListener("pointerup", (event) => {
    if (!state.drag || !event.isPrimary) return;
    state.draft[state.mode] = dragBox(state.drag.start, pointerPoint(event));
    state.drag = null;
    updateBoxText();
    pendingSettings();
    draw();
});
canvas.addEventListener("pointercancel", () => {
    state.drag = null;
    draw();
});
profileCanvas.addEventListener("click", (event) => {
    const section = state.report?.section;
    if (!section?.samples.length) return;
    const bounds = profileCanvas.getBoundingClientRect();
    const ratio = (event.clientX - bounds.left) / bounds.width;
    const index = Math.max(0, Math.min(section.samples.length - 1, Math.floor(ratio * section.samples.length)));
    let x = section.samples[index].x;
    const y = section.y;
    if (state.mode === "groove") {
        requestGroove(x, y);
        return;
    }
    const row = grooveRowAt(y);
    if (row?.measured) {
        if (Math.abs(x - row.left_x) <= 5) x = row.left_x;
        if (Math.abs(x - row.right_x) <= 5) x = row.right_x;
    }
    readPoint(x, y);
});
new ResizeObserver(drawProfile).observe(profileCanvas);

for (const side of ["left", "right"]) {
    $(`prepare-${side}`).addEventListener("click", (event) => {
        event.preventDefault();
        if (state.card) doWork(async () => {
            const result = await api(cardPath(state.card, `/prepare/${side}`), { method: "POST" });
            location.assign(result.url);
        });
    });
}
window.state = state;
window.RakeProof = { state, requestGroove, adoptProposal, reviewGrooveEdge, reviewBothGrooveEdges };
listCards().catch((error) => status(error.message, true));
