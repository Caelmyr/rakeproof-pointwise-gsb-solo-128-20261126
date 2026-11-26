"""Serve the paired-image desk and send either immutable native photo for preparation."""

import os
from io import BytesIO
from pathlib import Path

from flask import Blueprint, jsonify, render_template, request, send_file, url_for

from image_lab import create_app as preparation_app
from image_lab.validation import Conflict, Invalid, choice, integer, object_value

from . import analysis, demo, exports, groove as groove_mod, lighting, photo, probe
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
        current = cards.read(key)
        if request.method == "PATCH":
            payload = object_value(request.get_json(silent=True), "核验设置")
            if "groove" in payload or "track" in payload:
                raw_groove = payload.get("groove", payload.get("track"))
                adopted = None if raw_groove is None else groove_mod.validate_groove(raw_groove, current["left_size"])
                return jsonify(cards.update(key, payload, adopted, True))
            setting_keys = {"zone", "flat", "dx", "dy", "scan_y", "balance", "threshold"}
            if setting_keys & set(payload):
                return jsonify(cards.update(key, payload))
            return jsonify(cards.save(key, payload))
        return jsonify(current)

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
        current, _, _, evidence = measure(key)
        return jsonify(revision=current["revision"], **analysis.summarize(current, evidence))

    @desk.get("/cards/<key>/probe")
    def point(key):
        current, left, right = pair(key)
        x = integer(request.args.get("x"), "左照原列", 0, current["left_size"][0] - 1)
        y = integer(request.args.get("y"), "左照原行", 0, current["left_size"][1] - 1)
        settings = current["settings"]
        common = common_bounds(current["left_size"], current["right_size"], settings)
        calibration = lighting.flat_gain(left, right, settings, common)
        return jsonify(revision=current["revision"], **probe.inspect(left, right, settings, calibration, x, y))

    def json_body():
        value = request.get_json(silent=True)
        return value if isinstance(value, dict) else {}

    def trace_result(key):
        current, _, _, evidence = measure(key)
        body = json_body() if request.method in ("POST", "PUT", "PATCH") else {}
        raw_x = body.get("x", request.args.get("x"))
        raw_y = body.get("y", request.args.get("y", current["settings"]["scan_y"]))
        if raw_x is None:
            raise Invalid("请在横切线上点选一条沟")
        x = integer(raw_x, "左照点选列", 0, current["left_size"][0] - 1)
        y = integer(raw_y, "左照点选行", 0, current["left_size"][1] - 1)
        track_choice = body.get("choice", request.args.get("choice", "recommended"))
        direction = body.get("direction", request.args.get("direction", "down"))
        result = groove_mod.trace_groove(
            evidence, current["settings"], x, y,
            direction=direction, choice=track_choice,
        )
        return current, result, body

    def save_adoption(key, current, adopted, body):
        version = body.get("revision", request.args.get("rev", current["revision"]))
        return cards.save_groove(key, integer(version, "核验卡版本", 1), adopted)

    @desk.route("/cards/<key>/groove/proposal", methods=["GET", "POST"])
    def groove_proposal(key):
        _, result, _ = trace_result(key)
        return jsonify(result)

    # Vocabulary aliases for the same coupled groove/width proposal.
    desk.add_url_rule("/cards/<key>/trace", view_func=groove_proposal, methods=["GET", "POST"])
    desk.add_url_rule("/cards/<key>/track", view_func=groove_proposal, methods=["GET", "POST"])
    desk.add_url_rule("/cards/<key>/width", view_func=groove_proposal, methods=["GET", "POST"])
    desk.add_url_rule("/cards/<key>/widths", view_func=groove_proposal, methods=["GET", "POST"])
    desk.add_url_rule("/cards/<key>/grooves", view_func=groove_proposal, methods=["GET", "POST"])
    desk.add_url_rule("/cards/<key>/groove-width", view_func=groove_proposal, methods=["GET", "POST"])
    desk.add_url_rule("/cards/<key>/section/groove", view_func=groove_proposal, methods=["GET", "POST"])

    @desk.route("/cards/<key>/groove", methods=["GET", "POST", "PUT", "DELETE"])
    def groove_collection(key):
        current = cards.read(key)
        if request.method == "DELETE":
            body = json_body()
            saved = save_adoption(key, current, None, body)
            return jsonify(saved)
        if request.method == "GET":
            if request.args.get("x") is not None:
                _, result, _ = trace_result(key)
                return jsonify(result)
            adopted = current.get("groove")
            return jsonify(groove=adopted, track=adopted, revision=current["revision"])

        body = json_body()
        raw_adopted = body.get("groove") or body.get("adoption") or body.get("track")
        result = None
        if raw_adopted is None and ("x" in body or request.args.get("x") is not None):
            current, result, body = trace_result(key)
            confirmed = body.get("adopt", body.get("confirm", body.get("confirmed", body.get("save", False))))
            query_confirmed = request.args.get("adopt") in ("1", "true", "yes")
            if not (confirmed is True or confirmed == "true" or query_confirmed or request.method == "PUT"):
                return jsonify(result)
            raw_adopted = result["adoption"]
        if raw_adopted is None:
            raise Invalid("请先在横切线上点选一条沟，或提交确认后的追踪结果")
        adopted = groove_mod.validate_groove(raw_adopted, current["left_size"])
        saved = save_adoption(key, current, adopted, body)
        response = {"card": saved, "groove": adopted, "track": adopted}
        if result is not None:
            response["proposal"] = result
        return jsonify(response), 201

    @desk.post("/cards/<key>/groove/adopt")
    def groove_adopt(key):
        current = cards.read(key)
        body = json_body()
        raw_adopted = body.get("groove") or body.get("adoption") or body.get("track")
        result = None
        if raw_adopted is None:
            current, result, body = trace_result(key)
            raw_adopted = result["adoption"]
        adopted = groove_mod.validate_groove(raw_adopted, current["left_size"])
        saved = save_adoption(key, current, adopted, body)
        response = {"card": saved, "groove": adopted, "track": adopted}
        if result is not None:
            response["proposal"] = result
        return jsonify(response), 201

    desk.add_url_rule("/cards/<key>/trace/adopt", view_func=groove_adopt, methods=["POST"])
    desk.add_url_rule("/cards/<key>/track/adopt", view_func=groove_adopt, methods=["POST"])
    desk.add_url_rule("/cards/<key>/width/adopt", view_func=groove_adopt, methods=["POST"])

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

    @desk.get("/cards/<key>/groove.csv")
    def groove_csv_route(key):
        current, _, _, evidence = measure(key)
        summary = analysis.summarize(current, evidence)
        return download(exports.groove_csv(summary), "text/csv", "rakeproof-groove.csv")

    desk.add_url_rule("/cards/<key>/width.csv", view_func=groove_csv_route, methods=["GET"])

    @desk.get("/cards/<key>/evidence.zip")
    def bundle(key):
        current, left, right, evidence = measure(key)
        return download(exports.bundle(current, left, right, evidence, analysis.summarize(current, evidence)),
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
