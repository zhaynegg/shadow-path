import datetime as dt

from pysolar import solar


def sun_position(lat: float, lon: float, when: dt.datetime) -> tuple[float, float]:
    """Solar altitude and azimuth in degrees at the city centre."""
    return solar.get_altitude(lat, lon, when), solar.get_azimuth(lat, lon, when)


# Below this altitude an hour is too coarse a step to quantise departure time
# into. Measured on 13 September over the 10,066-edge walk network, not guessed:
#
#            step   mean shade   edges whose shade_fraction moved > 0.1
#   13:00    +20m       0.335                                     3.6%
#   (41 deg) +60m       0.358                                    16.8%
#   17:00    +20m       0.659                                    24.2%
#   (18 deg) +60m       0.820                                    59.1%
#
# An hour at 17:00 redraws well over half the network, and moves mean shade by
# 0.22 -- more than the 0.033-to-0.291 June/December gap the cache is keyed on
# the date for. At midday the same twenty minutes moves 3.6% of edges, which is
# far below the error already in the height priors, so paying three times the
# tiles for it would buy nothing.
LOW_SUN_DEG = 25.0

# What a low-sun hour is split into. Three stamps an hour, not six: the point is
# to get inside the hour, and every extra one is another citywide tileset to
# build, ship and hold open in the browser.
LOW_SUN_MINUTES = (0, 20, 40)


def daylight_times(lat: float, lon: float, date: dt.date, tz: dt.tzinfo) -> list[dt.time]:
    """Every timestamp this date gets a shadow field for, ascending.

    Resolution follows the sun rather than the clock. How fast a shadow moves is
    a function of altitude, so the hours that need splitting are the low ones --
    which are also the first and last of the day, and in December most of it.

    The step is chosen from the sun at the top of the hour and then each stamp
    is checked for itself, which is what makes the boundary hours come out
    right: sunrise is below LOW_SUN_DEG by definition, so its hour is split, and
    the stamps before first light drop out on the second test rather than
    shifting the whole hour's resolution.
    """
    times: list[dt.time] = []
    for hour in range(24):
        at_hour = dt.datetime.combine(date, dt.time(hour), tzinfo=tz)
        altitude, _ = sun_position(lat, lon, at_hour)
        minutes = LOW_SUN_MINUTES if altitude < LOW_SUN_DEG else (0,)
        for minute in minutes:
            when = dt.datetime.combine(date, dt.time(hour, minute), tzinfo=tz)
            if sun_position(lat, lon, when)[0] > 0:
                times.append(dt.time(hour, minute))
    return times
