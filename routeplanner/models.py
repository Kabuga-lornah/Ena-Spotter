"""Database tables. Django creates them from these classes when you run `migrate`.

Both tables are filled by `python manage.py load_fuel_stations`.
"""

from django.db import models


class Place(models.Model):
    """A US city/town with an approximate centre point (offline gazetteer).

    Used to geocode fuel stations (the CSV has no coordinates) and "City, ST"
    request inputs without calling an external API.
    """

    # Normalised name used for lookups: "St. Louis" and "Saint Louis" both become "STLOUIS".
    key = models.CharField(max_length=100, help_text="Normalised city name, see geo.normalize_city")
    state = models.CharField(max_length=2)  # two-letter code, e.g. "MO"
    name = models.CharField(max_length=100)  # display name, e.g. "Saint Louis"
    lat = models.FloatField()
    lon = models.FloatField()

    class Meta:
        # Each town appears once per state (there's a Springfield in many states).
        constraints = [models.UniqueConstraint(fields=["key", "state"], name="unique_place_key_state")]

    def __str__(self):
        return f"{self.name}, {self.state}"


class FuelStation(models.Model):
    """One row of the fuel price CSV, plus coordinates added at import time."""

    opis_id = models.IntegerField(unique=True)  # "OPIS Truckstop ID" column
    name = models.CharField(max_length=200)
    address = models.CharField(max_length=300)
    city = models.CharField(max_length=100)
    state = models.CharField(max_length=2)
    rack_id = models.IntegerField(null=True, blank=True)
    # DecimalField stores money exactly (no floating-point rounding errors).
    price = models.DecimalField(max_digits=8, decimal_places=5, help_text="Retail price, USD per gallon")
    # Currently the town centre (the CSV has no coordinates). Better coordinates can be
    # stored here later without changing any other code.
    lat = models.FloatField()
    lon = models.FloatField()

    class Meta:
        # Speeds up searches by location.
        indexes = [models.Index(fields=["lat", "lon"])]

    def __str__(self):
        return f"{self.name} ({self.city}, {self.state}) ${self.price}"
