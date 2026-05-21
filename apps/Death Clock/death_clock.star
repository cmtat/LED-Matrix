"""
Applet: Death Clock
Summary: Countdown to your death
Description: Shows a live countdown in seconds of how long you have left to live based on your birthday and gender using average US life expectancy data.
Author: carter
"""

load("render.star", "render")
load("schema.star", "schema")
load("time.star", "time")

LIFE_EXPECTANCY_SECONDS = {
    "male": 76 * 365 * 24 * 3600,
    "female": 81 * 365 * 24 * 3600,
}

def main(config):
    birth_year = int(config.str("birth_year", "1990"))
    birth_month = int(config.str("birth_month", "1"))
    birth_day = int(config.str("birth_day", "1"))
    gender = config.str("gender", "male")

    life_secs = LIFE_EXPECTANCY_SECONDS.get(gender, LIFE_EXPECTANCY_SECONDS["male"])

    birth_unix = time.time(
        year = birth_year,
        month = birth_month,
        day = birth_day,
        hour = 0,
        minute = 0,
        second = 0,
        location = "UTC",
    ).unix

    death_unix = birth_unix + life_secs
    now_unix = time.now().unix
    seconds_left = death_unix - now_unix

    if seconds_left < 0:
        seconds_left = 0

    frames = [
        render_frame(seconds_left - i if seconds_left - i >= 0 else 0)
        for i in range(120)
    ]

    return render.Root(
        delay = 1000,
        child = render.Animation(children = frames),
    )


def render_frame(secs):
    if secs == 0:
        count_text = "EXPIRED"
        count_color = "#ff4444"
    else:
        count_text = str(secs)
        count_color = "#00ff00"

    return render.Column(
        expanded = True,
        main_align = "start",
        cross_align = "center",
        children = [
            render.Box(
                width = 64,
                height = 9,
                child = render.Padding(
                    pad = (0, 1, 0, 0),
                    child = render.Text(
                        content = "Death Clock",
                        color = "#ffffff",
                        font = "tom-thumb",
                    ),
                ),
            ),
            render.Box(
                width = 64,
                height = 15,
                child = render.Row(
                    expanded = True,
                    main_align = "center",
                    cross_align = "center",
                    children = [
                        render.Text(
                            content = count_text,
                            color = count_color,
                            font = "tb-8",
                        ),
                    ],
                ),
            ),
            render.Box(
                width = 64,
                height = 8,
                child = render.Text(
                    content = "seconds left",
                    color = "#444444",
                    font = "tom-thumb",
                ),
            ),
        ],
    )


def get_schema():
    months = [
        schema.Option(display = "January", value = "1"),
        schema.Option(display = "February", value = "2"),
        schema.Option(display = "March", value = "3"),
        schema.Option(display = "April", value = "4"),
        schema.Option(display = "May", value = "5"),
        schema.Option(display = "June", value = "6"),
        schema.Option(display = "July", value = "7"),
        schema.Option(display = "August", value = "8"),
        schema.Option(display = "September", value = "9"),
        schema.Option(display = "October", value = "10"),
        schema.Option(display = "November", value = "11"),
        schema.Option(display = "December", value = "12"),
    ]
    days = [schema.Option(display = str(d), value = str(d)) for d in range(1, 32)]

    return schema.Schema(
        version = "1",
        fields = [
            schema.Text(
                id = "birth_year",
                name = "Birth Year",
                desc = "Your year of birth (e.g. 1990)",
                icon = "calendar",
                default = "1990",
            ),
            schema.Dropdown(
                id = "birth_month",
                name = "Birth Month",
                desc = "Your month of birth",
                icon = "calendar",
                options = months,
                default = "1",
            ),
            schema.Dropdown(
                id = "birth_day",
                name = "Birth Day",
                desc = "Your day of birth",
                icon = "calendar",
                options = days,
                default = "1",
            ),
            schema.Dropdown(
                id = "gender",
                name = "Biological Sex",
                desc = "Sets life expectancy (male ~76 yrs, female ~81 yrs US avg)",
                icon = "person",
                options = [
                    schema.Option(display = "Male", value = "male"),
                    schema.Option(display = "Female", value = "female"),
                ],
                default = "male",
            ),
        ],
    )
