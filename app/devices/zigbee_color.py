import colorsys

# Innr RB 282 C mired range, as reported by Zigbee2MQTT's device exposes.
_MIRED_MIN = 153
_MIRED_MAX = 556


def pct_to_mireds(pct: int) -> int:
    """0 (warm ☀️) .. 100 (cool ❄️) -> mireds."""
    pct = max(0, min(100, pct))
    return round(_MIRED_MAX - (pct / 100) * (_MIRED_MAX - _MIRED_MIN))


def mireds_to_pct(mireds: float) -> int:
    mireds = max(_MIRED_MIN, min(_MIRED_MAX, mireds))
    return round((_MIRED_MAX - mireds) / (_MIRED_MAX - _MIRED_MIN) * 100)


def rgb_hex_to_hs_brightness(rgb: str) -> tuple[int, int, int]:
    """#rrggbb -> (hue 0-360, saturation 0-100, brightness 0-254)."""
    r = int(rgb[1:3], 16) / 255
    g = int(rgb[3:5], 16) / 255
    b = int(rgb[5:7], 16) / 255
    h, s, v = colorsys.rgb_to_hsv(r, g, b)
    return round(h * 360), round(s * 100), round(v * 254)


def hs_to_rgb_hex(hue: float, saturation: float, brightness: int = 254) -> str:
    """(hue 0-360, saturation 0-100, brightness 0-254) -> #rrggbb."""
    r, g, b = colorsys.hsv_to_rgb(hue / 360, saturation / 100, brightness / 254)
    return f"#{round(r * 255):02x}{round(g * 255):02x}{round(b * 255):02x}"
