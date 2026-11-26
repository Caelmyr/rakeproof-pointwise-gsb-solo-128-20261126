"""Serve the paired-image desk and send either immutable native photo for preparation."""

import os
from io import BytesIO
from pathlib import Path

from flask import Blueprint, jsonify, render_template, request, send_file, url_for

from image_lab import create_app as preparation_app
from image_lab.validation import Conflict, Invalid, choice, integer, object_value

from . import analysis, demo, exports, lighting, photo, probe
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
