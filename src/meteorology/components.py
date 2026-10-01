"""Stable product IDs and native matrix prefixes for daily components."""

DAILY_COMPONENTS = {
    "surface_weather_daily": ("surface_weather", 5),
    "daylight_daily": ("daylight", 4),
    "lunar_daily": ("lunar", 5),
}

NATIVE_RESOLUTIONS = {
    prefix: resolution for prefix, resolution in DAILY_COMPONENTS.values()
}
