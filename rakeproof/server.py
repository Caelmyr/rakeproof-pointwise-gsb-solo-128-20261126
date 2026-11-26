"""Serve the paired-image desk and send either immutable native photo for preparation."""

import os
from io import BytesIO
from pathlib import Path

from flask import Blueprint, jsonify, render_template, request, send_file, url_for

from image_lab import create_app as preparation_app
from image_lab.validation import Conflict, Invalid, choice, integer, object_value

from . import analysis, channels, demo, exports, lighting, photo, probe
from .geometry import common_bounds
from .store import Cards


def download(data, mime, name=None):
    return send_file(BytesIO(data), mimetype=mime, max_age=0,
                     as_attachment=name is not None, download_name=name)


def uploaded(field):
    upload = request.files.get(field)
    if not upload or not upload.filename:
        raise Invalid("请分别选择左、右两向斜照原片")
    size, content = photo.decode(upload.stream.read(photo.MAX_IMAGE_BYTES + 1))
    name = Path(upload.filename.replace(chr(92), "/")).name[:120]
    return name, size, content


def create_app(data=None):
    directory = Path(data or os.environ.get("RAKEPROOF_DATA", ".rakeproof-data")).resolve()
    app = preparation_app(directory / "prepare")
    cards = Cards(directory / "inspections")
    desk = Blueprint("rake", __name__, url_prefix="/rake")

    @app.get("/")
    def home():
        return render_template("rakeproof.html")

    @desk.get("/health")
    def health():
        return jsonify(status="ok", app="rakeproof")

    @desk.route("/cards", methods=["GET", "POST"])
    def collection():
        if request.method == "GET":
            return jsonify(cards=cards.list())
        request.max_content_length = 29 * 1024 * 1024
        return jsonify(cards.create(request.form.get("name"), uploaded("left"), uploaded("right"))), 201

    @desk.post("/practice")
    def practice():
        payload = object_value(request.get_json(silent=True), "练习选项")
        style = choice(payload.get("sample"), "练习", tuple(demo.EXERCISES))
        name, left, right, settings = demo.make(style)
        return jsonify(cards.create(name, left, right, settings, synthetic=True)), 201

    @desk.route("/cards/<key>", methods=["GET", "PATCH"])
    def card(key):
        if request.method == "PATCH":
            return jsonify(cards.save(key, object_value(request.get_json(silent=True), "核验设置")))
        return jsonify(cards.read(key))

    @desk.get("/cards/<key>/source/<side>.png")
    def source(key, side):
        side = choice(side, "斜照方向", ("left", "right"))
        return download(cards.photos(key, (side,))[side], "image/png")

    def pair(key):
        current = cards.read(key)
        revision = request.args.get("rev")
        if revision is not None and integer(revision, "核验卡版本", 1) != current["revision"]:
            raise Conflict("卡片版本已经更新，请重新打开")
        sources = cards.photos(key)
        return current, photo.pixels(BytesIO(sources["left"])), photo.pixels(BytesIO(sources["right"]))

    def measure(key):
        current, left, right = pair(key)
        return current, left, right, lighting.sample_zone(left, right, current["settings"])

    @desk.get("/cards/<key>/report")
    def report(key):
        current, left, right, evidence = measure(key)
        channel_report = channels.channel_section(
            left, right, current["settings"], evidence.common, evidence.bounds, evidence=evidence
        )
        return jsonify(
            revision=current["revision"],
            **analysis.summarize(current, evidence, channel_section=channel_report),
        )

    @desk.get("/cards/<key>/probe")
    def point(key):
        current, left, right = pair(key)
        x = integer(request.args.get("x"), "左照原列", 0, current["left_size"][0] - 1)
        y = integer(request.args.get("y"), "左照原行", 0, current["left_size"][1] - 1)
        settings = current["settings"]
        common = common_bounds(current["left_size"], current["right_size"], settings)
        calibration = lighting.flat_gain(left, right, settings, common)
        channel_calibration = channels.channel_flat_calibration(left, right, settings, common)
        forced = request.args.get("caliber") or request.args.get("channel") or request.args.get("channels")
        if forced:
            forced = channels.caliber_key(forced)
        anchor_x = request.args.get("anchor_x") or request.args.get("caliber_x") or request.args.get("column_x")
        if anchor_x is not None:
            anchor_x = integer(anchor_x, "口径原列", 0, current["left_size"][0] - 1)
        result = probe.inspect(
            left, right, settings, calibration, x, y,
            channel_calibration=channel_calibration,
            adoption=current.get("channel_adoption"),
            forced_caliber=forced, anchor_x=anchor_x,
        )
        return jsonify(revision=current["revision"], **result)

    def channel_report_for(key):
        current, left, right, evidence = measure(key)
        report = channels.channel_section(
            left, right, current["settings"], evidence.common, evidence.bounds, evidence=evidence
        )
        return current, report

    @desk.get("/cards/<key>/channel-section")
    @desk.get("/cards/<key>/channel-reread")
    @desk.get("/cards/<key>/channel-plan")
    @desk.get("/cards/<key>/channel-profile")
    @desk.get("/cards/<key>/channel-line")
    @desk.get("/cards/<key>/profile/channel")
    @desk.get("/cards/<key>/line/channel")
    @desk.get("/cards/<key>/channel-transect")
    @desk.get("/cards/<key>/channel-read")
    @desk.get("/cards/<key>/reread")
    @desk.get("/cards/<key>/transect")
    @desk.get("/cards/<key>/supplement")
    @desk.get("/cards/<key>/channels")
    @desk.get("/cards/<key>/section/channel")
    def channel_section_route(key):
        current, report = channel_report_for(key)
        return jsonify(
            revision=current["revision"],
            adoption=current.get("channel_adoption"),
            adopted=channels.adoption_matches(current.get("channel_adoption"), current["settings"]),
            **report,
        )

    @desk.get("/cards/<key>/channel-advice")
    def channel_advice_route(key):
        current, report = channel_report_for(key)
        return jsonify(revision=current["revision"], advice=report.get("advice"),
                       recommendation=report.get("recommendation"),
                       adoption=current.get("channel_adoption"))

    @desk.get("/cards/<key>/channel-section.csv")
    @desk.get("/cards/<key>/channel-reread.csv")
    def channel_csv_route(key):
        _, report = channel_report_for(key)
        return download(exports.channel_section_csv(report), "text/csv", "rakeproof-channel-section.csv")

    @desk.post("/cards/<key>/channel-adoption")
    @desk.post("/cards/<key>/channel-section")
    @desk.post("/cards/<key>/channel-reread")
    @desk.post("/cards/<key>/channel-plan")
    @desk.post("/cards/<key>/channel-profile")
    @desk.post("/cards/<key>/channel-line")
    @desk.post("/cards/<key>/channel")
    @desk.post("/cards/<key>/channels")
    @desk.post("/cards/<key>/channel-section/adopt")
    @desk.post("/cards/<key>/channel-reread/adopt")
    @desk.post("/cards/<key>/channel-transect/adopt")
    @desk.post("/cards/<key>/channel-read/adopt")
    @desk.post("/cards/<key>/adopt-channel")
    @desk.post("/cards/<key>/adopt")
    @desk.post("/cards/<key>/channels/adopt")
    @desk.post("/cards/<key>/section/channel/adopt")
    def adopt_channel_route(key):
        body = object_value(request.get_json(silent=True) or {}, "通道采用方案")
        current, report = channel_report_for(key)
        revision = integer(body.get("revision", current["revision"]), "核验卡版本", 1)
        if revision != current["revision"]:
            raise Conflict("卡片版本已经更新，请重新打开")
        strategy = body.get("strategy", "recommended")
        if strategy in {"single", "channel", "channel_only"}:
            plan = report["channel_recommendation"]
        elif strategy in {"hybrid", "gray", "gray_bridge"}:
            plan = report["hybrid_recommendation"]
        elif strategy in {"set", "sets", "blended", "recommended"}:
            plan = report["set_recommendation"] if strategy != "recommended" else report["recommendation"]
        else:
            plan = report["recommendation"]
        if not plan or not plan.get("counts", {}).get("adopted"):
            raise Invalid("当前横切线没有可采用的同名通道证据")
        adoption = channels.adoption_from_plan(plan, current["settings"], strategy=f"{strategy}_v1")
        # Allow an explicit narrow adoption (one column/global channel) while
        # still stamping it against the current settings.
        explicit = body.get("channel_adoption", body.get("adoption"))
        if isinstance(explicit, dict):
            adoption = channels.normalize_adoption(explicit, current["settings"])
        elif any(field in body for field in ("channel", "channels", "caliber", "caliber_by_x", "columns", "adopted_channel")):
            adoption = channels.normalize_adoption(body, current["settings"])
        updated = cards.save_adoption(key, revision, adoption)
        return jsonify(updated), 200

    @desk.get("/cards/<key>/channel-probe")
    @desk.get("/cards/<key>/channel-point")
    @desk.get("/cards/<key>/channel-read-point")
    @desk.get("/cards/<key>/probe/channel")
    def channel_point(key):
        current, left, right = pair(key)
        x = integer(request.args.get("x"), "左照原列", 0, current["left_size"][0] - 1)
        y = integer(request.args.get("y"), "左照原行", 0, current["left_size"][1] - 1)
        settings = current["settings"]
        common = common_bounds(current["left_size"], current["right_size"], settings)
        calibration = lighting.flat_gain(left, right, settings, common)
        channel_calibration = channels.channel_flat_calibration(left, right, settings, common)
        forced = request.args.get("caliber") or request.args.get("channel") or request.args.get("channels")
        if forced:
            forced = channels.caliber_key(forced)
        anchor_x = request.args.get("anchor_x") or request.args.get("caliber_x") or request.args.get("column_x")
        if anchor_x is not None:
            anchor_x = integer(anchor_x, "口径原列", 0, current["left_size"][0] - 1)
        result = probe.inspect(
            left, right, settings, calibration, x, y,
            channel_calibration=channel_calibration,
            adoption=current.get("channel_adoption"),
            forced_caliber=forced, anchor_x=anchor_x,
        )
        review = result["channel"]
        # The dedicated endpoint makes the adopted caliber the primary contrast
        # while retaining both original RGB readings in result.left/right.
        response = dict(result)
        response["response"] = review.get("response")
        response["status"] = review.get("status")
        response["caliber"] = review.get("caliber")
        return jsonify(revision=current["revision"], **response)

    @desk.get("/cards/<key>/map.png")
    def response_map(key):
        current, _, _, evidence = measure(key)
        return download(photo.encode(analysis.map_image(current["left_size"], evidence, current["settings"]["threshold"])),
                        "image/png")

    @desk.get("/cards/<key>/proof.png")
    def proof(key):
        current, left, _, evidence = measure(key)
        return download(exports.proof(current, left, evidence), "image/png", "rakeproof-zone.png")

    @desk.get("/cards/<key>/section.csv")
    def csv(key):
        current, _, _, evidence = measure(key)
        return download(exports.section_csv(analysis.summarize(current, evidence)), "text/csv", "rakeproof-section.csv")

    @desk.get("/cards/<key>/evidence.zip")
    def bundle(key):
        current, left, right, evidence = measure(key)
        channel_report = channels.channel_section(
            left, right, current["settings"], evidence.common, evidence.bounds, evidence=evidence
        )
        summary = analysis.summarize(current, evidence, channel_section=channel_report)
        return download(exports.bundle(current, left, right, evidence, summary),
                        "application/zip", f"rakeproof-r{current['revision']}.zip")

    @desk.post("/cards/<key>/prepare/<side>")
    def prepare(key, side):
        side = choice(side, "斜照方向", ("left", "right"))
        current = cards.read(key)
        original = photo.pixels(BytesIO(cards.photos(key, (side,))[side])).astype("float32") / 255
        services = app.extensions
        asset = services["assets"].put(original, current[f"{side}_name"])
        project = services["store"].create(f"{current['name']} · {'左照' if side == 'left' else '右照'}准备")
        services["frames"].add(project["id"], project["revision"], [asset["id"]])
        return jsonify(url=url_for("index", project=project["id"])), 201

    app.register_blueprint(desk)
    return app
