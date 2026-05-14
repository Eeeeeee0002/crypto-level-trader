"""City definitions with coordinates and temperature units for Polymarket weather markets."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class City:
    name: str
    latitude: float
    longitude: float
    timezone: str
    unit: str  # "celsius" or "fahrenheit"


CITIES: dict[str, City] = {
    "London": City("London", 51.5085, -0.1257, "Europe/London", "celsius"),
    "Paris": City("Paris", 48.8566, 2.3522, "Europe/Paris", "celsius"),
    "NYC": City("NYC", 40.7128, -74.0060, "America/New_York", "fahrenheit"),
    "Miami": City("Miami", 25.7617, -80.1918, "America/New_York", "fahrenheit"),
    "Tokyo": City("Tokyo", 35.6762, 139.6503, "Asia/Tokyo", "celsius"),
    "Hong Kong": City("Hong Kong", 22.3193, 114.1694, "Asia/Hong_Kong", "celsius"),
    "Shanghai": City("Shanghai", 31.2304, 121.4737, "Asia/Shanghai", "celsius"),
    "Seoul": City("Seoul", 37.5665, 126.9780, "Asia/Seoul", "celsius"),
    "Moscow": City("Moscow", 55.7558, 37.6173, "Europe/Moscow", "celsius"),
    "Istanbul": City("Istanbul", 41.0082, 28.9784, "Europe/Istanbul", "celsius"),
    "Jakarta": City("Jakarta", -6.2088, 106.8456, "Asia/Jakarta", "celsius"),
    "Madrid": City("Madrid", 40.4168, -3.7038, "Europe/Madrid", "celsius"),
    "Amsterdam": City("Amsterdam", 52.3676, 4.9041, "Europe/Amsterdam", "celsius"),
    "Sao Paulo": City("Sao Paulo", -23.5505, -46.6333, "America/Sao_Paulo", "celsius"),
    "Buenos Aires": City("Buenos Aires", -34.6037, -58.3816, "America/Argentina/Buenos_Aires", "celsius"),
    "Seattle": City("Seattle", 47.6062, -122.3321, "America/Los_Angeles", "fahrenheit"),
    "Los Angeles": City("Los Angeles", 34.0522, -118.2437, "America/Los_Angeles", "fahrenheit"),
    "Atlanta": City("Atlanta", 33.7490, -84.3880, "America/New_York", "fahrenheit"),
    "Wellington": City("Wellington", -41.2866, 174.7756, "Pacific/Auckland", "celsius"),
    "Chicago": City("Chicago", 41.8781, -87.6298, "America/Chicago", "fahrenheit"),
    "Dallas": City("Dallas", 32.7767, -96.7970, "America/Chicago", "fahrenheit"),
    "Denver": City("Denver", 39.7392, -104.9903, "America/Denver", "fahrenheit"),
    "San Francisco": City("San Francisco", 37.7749, -122.4194, "America/Los_Angeles", "fahrenheit"),
    "Phoenix": City("Phoenix", 33.4484, -112.0740, "America/Phoenix", "fahrenheit"),
    "Sydney": City("Sydney", -33.8688, 151.2093, "Australia/Sydney", "celsius"),
    "Melbourne": City("Melbourne", -37.8136, 144.9631, "Australia/Melbourne", "celsius"),
    "Singapore": City("Singapore", 1.3521, 103.8198, "Asia/Singapore", "celsius"),
    "Dubai": City("Dubai", 25.2048, 55.2708, "Asia/Dubai", "celsius"),
    "Bangkok": City("Bangkok", 13.7563, 100.5018, "Asia/Bangkok", "celsius"),
    "Mumbai": City("Mumbai", 19.0760, 72.8777, "Asia/Kolkata", "celsius"),
    "Berlin": City("Berlin", 52.5200, 13.4050, "Europe/Berlin", "celsius"),
    "Rome": City("Rome", 41.9028, 12.4964, "Europe/Rome", "celsius"),
}


def find_city(name: str) -> City | None:
    """Look up a city by name (case-insensitive, partial match)."""
    lower = name.lower()
    for city_name, city in CITIES.items():
        if city_name.lower() == lower:
            return city
    for city_name, city in CITIES.items():
        if lower in city_name.lower() or city_name.lower() in lower:
            return city
    return None
