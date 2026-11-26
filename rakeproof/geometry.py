"""Left-photo reference grid and the half-open extent of opposite illumination."""

from dataclasses import dataclass

from image_lab.validation import Invalid, integer, object_value, number


@dataclass(frozen=True)
class Box:
    left: int
    top: int
    right: int
    bottom: int

    @property
    def width(self):
        return max(0, self.right - self.left)

    @property
    def height(self):
        return max(0, self.bottom - self.top)

    @property
    def area(self):
        return self.width * self.height

    @property
    def slices(self):
        return slice(self.top, self.bottom), slice(self.left, self.right)

    def intersect(self, other):
        left = max(self.left, other.left)
        top = max(self.top, other.top)
        return Box(left, top, max(left, min(self.right, other.right)), max(top, min(self.bottom, other.bottom)))

    def shift(self, dx, dy):
        return Box(self.left + dx, self.top + dy, self.right + dx, self.bottom + dy)

    def contains(self, x, y):
        return self.left <= x < self.right and self.top <= y < self.bottom

    def record(self):
        return {"left": self.left, "top": self.top, "right": self.right, "bottom": self.bottom}


def extent(size):
    return Box(0, 0, *size)


def read_box(raw, size, label):
    values = object_value(raw, label)
    width, height = size
    left = integer(values.get("left"), f"{label}左界", 0, width - 1)
    top = integer(values.get("top"), f"{label}上界", 0, height - 1)
    right = integer(values.get("right"), f"{label}右界", left + 1, width)
    bottom = integer(values.get("bottom"), f"{label}下界", top + 1, height)
    return Box(left, top, right, bottom)


def default_settings(left_size):
    width, height = left_size
    return {
        "zone": extent(left_size).record(),
        "flat": Box(width // 12, height // 10, width // 4, height // 4).record(),
        "dx": 0, "dy": 0, "scan_y": height // 2,
        "balance": False, "threshold": 12.0,
    }


def validate_settings(raw, left_size, right_size):
    w, h = left_size
    other_w, other_h = right_size
    dx = integer(raw.get("dx"), "横向移量", 1 - other_w, w - 1)
    dy = integer(raw.get("dy"), "纵向移量", 1 - other_h, h - 1)
    balance = raw.get("balance")
    if not isinstance(balance, bool):
        raise Invalid("请选择是否平衡照明")
    return {
        "zone": read_box(raw.get("zone"), left_size, "量测框").record(),
        "flat": read_box(raw.get("flat"), left_size, "平面参照框").record(),
        "dx": dx, "dy": dy, "scan_y": integer(raw.get("scan_y"), "横切线", 0, h - 1),
        "balance": balance, "threshold": number(raw.get("threshold"), "坡向阈值", 1, 95),
    }


def common_bounds(left_size, right_size, settings):
    return extent(left_size).intersect(extent(right_size).shift(settings["dx"], settings["dy"]))


def to_right(box, settings):
    return box.shift(-settings["dx"], -settings["dy"])
