"use strict";

const $ = (id) => document.getElementById(id);
const state = {
    card: null, draft: null, report: null, photos: null, mode: "probe",
    drag: null, point: null, busy: false, pointRequest: 0,
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
    renderChannelPlan();
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
    if (state.point) {
        const [x, y] = state.point;
        ctx.strokeStyle = "#fff";
        ctx.beginPath();
        ctx.arc(x + 0.5, y + 0.5, Math.max(4, canvas.width / 150), 0, Math.PI * 2);
        ctx.stroke();
    }
}

function caliberLabel(caliber) {
    if (!caliber || caliber === "gray") return "原灰度";
    const names = { R: "红", G: "绿", B: "蓝" };
    return caliber.split("+").map((part) => names[part] || part).join("+") + "通道";
}

function renderChannelPlan() {
    const channel = state.report?.channel_section;
    const recommendation = channel?.recommendation;
    const summary = $("channel-summary");
    const detail = $("channel-detail");
    const chips = $("channel-switches");
    const adopt = $("adopt-channel");
    if (!summary || !detail || !chips || !adopt) return;
    chips.replaceChildren();
    if (!channel?.length || !recommendation?.counts?.adopted) {
        summary.textContent = "当前横切线没有可由同名通道补读的原列。";
        detail.textContent = "";
        adopt.disabled = true;
        return;
    }
    adopt.disabled = false;
    const c = channel.counts;
    const p = recommendation.counts;
    const adopted = state.card.channel_adoption;
    const adoptedText = adopted ? "此卡已保存一版采用口径；再次点击可按当前设置重算并采用。" : "建议优先保住全部可补读列，并在同长度方案中最少换口径。";
    summary.textContent = `建议路线读 ${p.adopted}/${c.columns} 列：原灰度 ${c.original_valid} 列，断点中保住长度 ${c.recovered}px（${c.recovered} 列）；最长连续 ${p.longest_columns ?? 0}px（X=${p.longest_start_x ?? "—"}–${p.longest_end_x ?? "—"}），换口径 ${p.switches} 次、断开 ${p.breaks} 次。`;
    detail.textContent = adoptedText;
    adopt.textContent = adopted ? "重新采用通道建议" : "采用通道建议";

    const switchButtons = recommendation.switches.map((item) => {
        const button = document.createElement("button");
        button.type = "button";
        button.className = "channel-switch";
        button.textContent = `X=${item.bridge_x} 同列桥接：${caliberLabel(item.from)} → ${caliberLabel(item.to)}（${item.from_value}/${item.to_value}%，差 ${item.difference}）；X=${item.at_x} 起换口径`;
        button.title = "点击后在该桥接原列读点";
        button.addEventListener("click", () => readPoint(item.bridge_x, channel.y));
        return button;
    });
    const breakButtons = recommendation.breaks.map((item) => {
        const button = document.createElement("button");
        button.type = "button";
        button.className = "channel-break";
        button.textContent = `曲线断开：X=${item.after_x} → ${item.at_x}（${caliberLabel(item.from)} / ${caliberLabel(item.to)}，${item.reason}）`;
        button.addEventListener("click", () => readPoint(item.at_x, channel.y));
        return button;
    });
    chips.replaceChildren(...switchButtons, ...breakButtons);
}

function drawProfile() {
    if (!state.report) return;
    const { samples } = state.report.section;
    const channelSection = state.report.channel_section;
    const channelSamples = channelSection?.samples || [];
    const recommendation = channelSection?.recommendation;
    const width = Math.max(1, Math.round(profileCanvas.clientWidth * window.devicePixelRatio));
    const height = Math.round(180 * window.devicePixelRatio);
    profileCanvas.width = width;
    profileCanvas.height = height;
    const ctx = profileCanvas.getContext("2d");
    ctx.strokeStyle = "#667771";
    ctx.beginPath();
    ctx.moveTo(0, height / 2);
    ctx.lineTo(width, height / 2);
    ctx.stroke();
    const channelMax = channelSamples.reduce((result, item) => Math.max(result, Math.abs(item.recommended_value ?? 0)), 0);
    const max = samples.reduce((result, item) => Math.max(result, Math.abs(item.value ?? 0)), Math.max(state.card.settings.threshold, channelMax));
    const denom = Math.max(1, samples.length);
    const yFor = (value) => height * (0.5 - value / (max * 2.3));
    const xFor = (index) => (index + .5) * width / denom;

    // Existing luminance curve: invalid/exposure columns stay broken.
    ctx.lineWidth = Math.max(1, window.devicePixelRatio);
    ctx.strokeStyle = "#e3d79e";
    ctx.beginPath();
    let connected = false;
    samples.forEach((sample, index) => {
        if (sample.value === null) {
            connected = false;
            return;
        }
        const x = xFor(index);
        const y = yFor(sample.value);
        if (connected) ctx.lineTo(x, y);
        else ctx.moveTo(x, y);
        connected = true;
    });
    ctx.stroke();

    // Channel supplement/adopted route. Legal bridges remain connected; every
    // planned break or missing channel evidence lifts the pen.
    if (channelSamples.length && recommendation) {
        const breakAt = new Set((recommendation.breaks || []).map((item) => item.at_x));
        ctx.lineWidth = Math.max(2, 1.8 * window.devicePixelRatio);
        ctx.strokeStyle = "#72e7d5";
        ctx.beginPath();
        connected = false;
        channelSamples.forEach((sample, index) => {
            const caliber = sample.recommended_caliber;
            if (!caliber || caliber === "gray" || sample.recommended_value === null || breakAt.has(sample.x)) {
                connected = false;
                return;
            }
            const x = xFor(index);
            const y = yFor(sample.recommended_value);
            if (connected) ctx.lineTo(x, y);
            else ctx.moveTo(x, y);
            connected = true;
        });
        ctx.stroke();

        ctx.lineWidth = Math.max(1, window.devicePixelRatio);
        for (const item of recommendation.switches || []) {
            const index = channelSamples.findIndex((sample) => sample.x === item.bridge_x);
            if (index < 0) continue;
            const x = xFor(index);
            ctx.strokeStyle = "rgba(114,231,213,.55)";
            ctx.setLineDash([4, 4]);
            ctx.beginPath();
            ctx.moveTo(x, 6 * window.devicePixelRatio);
            ctx.lineTo(x, height - 6 * window.devicePixelRatio);
            ctx.stroke();
            ctx.setLineDash([]);
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

async function readPoint(x, y) {
    if (!state.card || state.busy) return;
    const request = ++state.pointRequest;
    try {
        const result = await api(adoptedPath(state.card, "/probe", { x, y }));
        if (request !== state.pointRequest) return;
        state.point = [x, y];
        $("point-x").value = x;
        $("point-y").value = y;
        const describe = (title, data) => data
            ? `${title} (${data.x}, ${data.y}) · RGB ${data.rgb.join(", ")} · 灰度 ${data.gray}`
            : `${title} 无对应像素`;
        const note = {
            outside_zone: "点在量区外，保留原像读数",
            exposure: "原灰度曝光不可用",
            unpaired: "两张原照片未覆盖同一位置",
            positive: "原灰度：左照相对亮", negative: "原灰度：右照相对亮", level: "原灰度坡向反差在阈值内",
        };
        const grayStatus = result.gray_status || result.status;
        const grayResponse = result.gray_response === undefined ? result.response : result.gray_response;
        const lines = [
            describe("左照", result.left), describe("右照", result.right),
            note[grayStatus] + (grayResponse === null ? " · 原灰度反差留空" : ` · ${grayResponse}%`),
        ];
        const channel = result.channel;
        if (channel) {
            const name = caliberLabel(channel.caliber);
            const channelNote = {
                no_adoption: "尚未采用通道口径；不自动换通道补数",
                no_adopted_column: "该原列不在已采用路线中，补读反差留空，不临时换口径",
                channel_exposure: `已采用的 ${name} 在该点缺曝光证据（${(channel.missing_channels || []).join("、")}）；保留原 RGB，补读反差留空，不临时换通道`,
                channel_support: `已采用的 ${name} 缺双方平面校正支持；保留原 RGB，补读反差留空，不临时换通道`,
                outside_zone: "点在量区外，通道补读未计算",
                unpaired: "右照对应坐标不存在，通道补读留空",
                exposure: "原像曝光证据不足，补读反差留空",
                positive: `${name}：左照相对亮`,
                negative: `${name}：右照相对亮`,
                level: `${name}：坡向反差在阈值内`,
            }[channel.status] || "通道补读留空";
            lines.push(channelNote + (channel.response === null || channel.response === undefined ? "" : ` · 补读 ${channel.response}%`));
        }
        $("readout").textContent = lines.join("\n");
        draw();
    } catch (error) {
        if (request === state.pointRequest) $("readout").textContent = error.message;
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
        const settings = { ...state.draft };
        delete settings.channel_adoption;
        delete settings.adopted_channel;
        const next = await api(cardPath(state.card), asJson("PATCH", { revision: state.card.revision, ...settings }));
        await openCard(next);
        status("读数设置已应用并保存。");
    });
});

$("adopt-channel").addEventListener("click", (event) => {
    event.preventDefault();
    if (!state.card || state.busy || $("adopt-channel").disabled) return;
    doWork(async () => {
        const updated = await api(cardPath(state.card, "/channel-adoption"),
            asJson("POST", { revision: state.card.revision, strategy: "recommended" }));
        await openCard(updated);
        status("已采用并保存通道补读口径；点附近另一原行时会沿用该原列的同一口径。");
    });
});

for (const key of fields) {
    $(key).addEventListener("input", () => {
        if (!state.card || !$(key).checkValidity()) return;
        state.draft[key] = $(key).valueAsNumber;
        pendingSettings();
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

$("view").addEventListener("change", draw);
for (const button of document.querySelectorAll("[data-mode]")) {
    button.addEventListener("click", () => setMode(button.dataset.mode));
}
canvas.addEventListener("pointerdown", (event) => {
    if (!state.card || state.busy || !event.isPrimary || event.button !== 0) return;
    const [x, y] = pointerPoint(event);
    if (state.mode === "probe") {
        readPoint(x, y);
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
    readPoint(section.samples[index].x, section.y);
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
listCards().catch((error) => status(error.message, true));
