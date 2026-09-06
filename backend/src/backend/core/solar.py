import datetime as dt

from pysolar import solar


def sun_position(lat: float, lon: float, when: dt.datetime) -> tuple[float, float]:
    """Solar altitude and azimuth in degrees at the city centre."""
    return solar.get_altitude(lat, lon, when), solar.get_azimuth(lat, lon, when)
