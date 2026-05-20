"""
Applet: Matrix Message
Summary: Custom message display
Description: Write your own text and choose colors, motion, and border style for the LED matrix.
Author: Carter
"""

load("render.star", "render")
load("schema.star", "schema")
FRAME_WIDTH = 64
FRAME_HEIGHT = 32

DEFAULT_MESSAGE = "HELLO MATRIX"
DEFAULT_TEXT_COLOR = "#00ff88"
DEFAULT_BG_COLOR = "#000000"
DEFAULT_ACCENT_COLOR = "#0af"

def main(config):
    message = config.str("message", DEFAULT_MESSAGE)
    if message == None or message == "":
        message = DEFAULT_MESSAGE

    mode = config.str("mode", "scroll")
    text_color = config.str("text_color", DEFAULT_TEXT_COLOR)
    bg_color = config.str("bg_color", DEFAULT_BG_COLOR)
    accent_color = config.str("accent_color", DEFAULT_ACCENT_COLOR)
    speed = config.str("speed", "medium")
    uppercase = config.bool("uppercase", True)
    show_border = config.bool("show_border", True)

    if uppercase:
        message = message.upper()

    delay = delay_for_speed(speed)
    content = content_for_mode(mode, message, text_color, accent_color, show_border)

    return render.Root(
        delay = delay,
        child = render.Stack(
            children = [
                render.Box(
                    width = FRAME_WIDTH,
                    height = FRAME_HEIGHT,
                    color = bg_color,
                ),
                content,
            ],
        ),
    )

def content_for_mode(mode, message, text_color, accent_color, show_border):
    if mode == "center":
        child = centered_message(message, text_color)
    elif mode == "vertical":
        child = vertical_message(message, text_color)
    elif mode == "split":
        child = split_message(message, text_color, accent_color)
    else:
        child = scrolling_message(message, text_color)

    if not show_border:
        return child

    return render.Stack(
        children = [
            border(accent_color),
            child,
        ],
    )

def centered_message(message, text_color):
    return render.Padding(
        pad = (2, 3, 2, 2),
        child = render.WrappedText(
            content = message,
            color = text_color,
            width = 60,
            linespacing = 0,
        ),
    )

def scrolling_message(message, text_color):
    return render.Marquee(
        height = FRAME_HEIGHT,
        offset_start = FRAME_WIDTH,
        offset_end = FRAME_WIDTH,
        child = render.Text(
            content = message,
            color = text_color,
        ),
    )

def vertical_message(message, text_color):
    return render.Marquee(
        height = FRAME_HEIGHT,
        offset_start = 28,
        offset_end = 28,
        scroll_direction = "vertical",
        child = render.WrappedText(
            content = message,
            color = text_color,
            width = 60,
            linespacing = 1,
        ),
    )

def split_message(message, text_color, accent_color):
    parts = message.split("|")
    title = parts[0].strip()
    body = len(parts) > 1 and parts[1].strip() or message

    return render.Column(
        children = [
            render.Padding(
                pad = (2, 1, 2, 0),
                child = render.WrappedText(
                    content = title,
                    color = accent_color,
                    width = 60,
                    linespacing = 0,
                ),
            ),
            render.Box(
                width = FRAME_WIDTH,
                height = 1,
                color = accent_color,
            ),
            render.Marquee(
                height = 20,
                offset_start = 18,
                offset_end = 18,
                child = render.Text(
                    content = body,
                    color = text_color,
                ),
            ),
        ],
    )

def border(color):
    return render.Stack(
        children = [
            render.Box(width = FRAME_WIDTH, height = 1, color = color),
            render.Padding(
                pad = (0, FRAME_HEIGHT - 1, 0, 0),
                child = render.Box(width = FRAME_WIDTH, height = 1, color = color),
            ),
            render.Box(width = 1, height = FRAME_HEIGHT, color = color),
            render.Padding(
                pad = (FRAME_WIDTH - 1, 0, 0, 0),
                child = render.Box(width = 1, height = FRAME_HEIGHT, color = color),
            ),
        ],
    )

def delay_for_speed(speed):
    if speed == "slow":
        return 140
    if speed == "fast":
        return 45
    if speed == "turbo":
        return 25

    return 80

def get_schema():
    mode_options = [
        schema.Option(display = "Scrolling", value = "scroll"),
        schema.Option(display = "Centered", value = "center"),
        schema.Option(display = "Vertical Scroll", value = "vertical"),
        schema.Option(display = "Title + Ticker", value = "split"),
    ]

    speed_options = [
        schema.Option(display = "Slow", value = "slow"),
        schema.Option(display = "Medium", value = "medium"),
        schema.Option(display = "Fast", value = "fast"),
        schema.Option(display = "Turbo", value = "turbo"),
    ]

    color_options = [
        schema.Option(display = "Matrix Green", value = "#00ff88"),
        schema.Option(display = "Cyan", value = "#00ccff"),
        schema.Option(display = "White", value = "#ffffff"),
        schema.Option(display = "Yellow", value = "#ffee55"),
        schema.Option(display = "Pink", value = "#ff4fd8"),
        schema.Option(display = "Red", value = "#ff3333"),
        schema.Option(display = "Orange", value = "#ff8a00"),
        schema.Option(display = "Purple", value = "#a855ff"),
    ]

    bg_options = [
        schema.Option(display = "Black", value = "#000000"),
        schema.Option(display = "Deep Blue", value = "#020617"),
        schema.Option(display = "Charcoal", value = "#111111"),
        schema.Option(display = "Midnight Purple", value = "#13001f"),
    ]

    accent_options = [
        schema.Option(display = "Cyan", value = "#00aaff"),
        schema.Option(display = "Matrix Green", value = "#00ff88"),
        schema.Option(display = "Yellow", value = "#ffee55"),
        schema.Option(display = "Pink", value = "#ff4fd8"),
        schema.Option(display = "White", value = "#ffffff"),
    ]

    return schema.Schema(
        version = "1",
        fields = [
            schema.Text(
                id = "message",
                name = "Message",
                desc = "Text to display. For Title + Ticker mode, use Title | Message.",
                icon = "font",
                default = DEFAULT_MESSAGE,
            ),
            schema.Dropdown(
                id = "mode",
                name = "Style",
                desc = "Choose how the message appears.",
                icon = "gear",
                default = "scroll",
                options = mode_options,
            ),
            schema.Dropdown(
                id = "speed",
                name = "Speed",
                desc = "Animation speed for scrolling styles.",
                icon = "gear",
                default = "medium",
                options = speed_options,
            ),
            schema.Dropdown(
                id = "text_color",
                name = "Text Color",
                desc = "Color of the main message text.",
                icon = "gear",
                default = DEFAULT_TEXT_COLOR,
                options = color_options,
            ),
            schema.Dropdown(
                id = "bg_color",
                name = "Background",
                desc = "Background color.",
                icon = "gear",
                default = DEFAULT_BG_COLOR,
                options = bg_options,
            ),
            schema.Dropdown(
                id = "accent_color",
                name = "Accent Color",
                desc = "Border and title color.",
                icon = "gear",
                default = DEFAULT_ACCENT_COLOR,
                options = accent_options,
            ),
            schema.Toggle(
                id = "uppercase",
                name = "Uppercase",
                desc = "Convert the message to uppercase.",
                icon = "gear",
                default = True,
            ),
            schema.Toggle(
                id = "show_border",
                name = "Border",
                desc = "Show a one-pixel border around the message.",
                icon = "gear",
                default = True,
            ),
        ],
    )
