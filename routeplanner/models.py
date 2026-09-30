from django.db import models


class Place(models.Model):
    """A US city/town with an approximate centre point (offline gazetteer).

    Used to geocode fuel stations (the CSV has no coordinates) and "City, ST"
    request inputs without calling an external API.
    """

    key = models.CharField(max_length=100, help_text="Normalised city name, see geo.normalize_city")
    state = models.CharField(max_length=2)
    name = models.CharField(max_length=100)
    lat = models.FloatField()
    lon = models.FloatField()

    class Meta:
        constraints = [models.UniqueConstraint(fields=["key", "state"], name="unique_place_key_state")]

    def __str__(self):
        return f"{self.name}, {self.state}"


class FuelStation(models.Model):
    opis_id = models.IntegerField(unique=True)
    name = models.CharField(max_length=200)
    address = models.CharField(max_length=300)
    city = models.CharField(max_length=100)
    state = models.CharField(max_length=2)
    rack_id = models.IntegerField(null=True, blank=True)
    price = models.DecimalField(max_digits=8, decimal_places=5, help_text="Retail price, USD per gallon")
    lat = models.FloatField()
    lon = models.FloatField()

    class Meta:
        indexes = [models.Index(fields=["lat", "lon"])]

    def __str__(self):
        return f"{self.name} ({self.city}, {self.state}) ${self.price}"
