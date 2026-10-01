"""Load the fuel price CSV into the database, geocoding every station offline.

Run it once after `migrate`:   python manage.py load_fuel_stations

The CSV only has city/state, so each station is placed at its town's centre,
computed from the US ZIP code database bundled with the `zipcodes` package.
This runs once, ahead of time, so the API never geocodes stations per request.

Files in management/commands/ become `manage.py <file name>` commands automatically.
"""

import csv
from collections import defaultdict
from decimal import Decimal
from pathlib import Path
from statistics import median

import zipcodes
from django.conf import settings
from django.core.management.base import BaseCommand
from django.db import transaction

from routeplanner.models import FuelStation, Place
from routeplanner.services.geo import US_STATES, is_in_usa, normalize_city
from routeplanner.services.stations import clear_station_cache


def build_places():
    """Median ZIP centroid per (city, state) -> {(key, state): (name, lat, lon)}.

    A ZIP's primary city name wins over its "acceptable" alternate names, and the
    median ignores stray ZIPs far from the town (the data even has some at 0,0).

    Example result entry: ("BIGCABIN", "OK") -> ("Big Cabin", 36.534, -95.2246)
    """
    # For every (town, state), collect the coordinates of all its ZIP codes.
    # "primary" = the ZIP's official town; "alternate" = other names the post office accepts.
    primary, alternate = defaultdict(list), defaultdict(list)
    names = {}  # (key, state) -> a nicely capitalised display name
    for z in zipcodes.list_all():
        # Skip territories and anything outside the 50 states + DC.
        if z.get("country") != "US" or z["state"] not in US_STATES:
            continue
        lat, lon = float(z["lat"]), float(z["long"])
        if not is_in_usa(lat, lon):  # skip broken records (e.g. 0,0)
            continue
        for city, bucket in [(z["city"], primary)] + [(c, alternate) for c in z.get("acceptable_cities", [])]:
            key = (normalize_city(city), z["state"])
            bucket[key].append((lat, lon))
            if bucket is primary or key not in names:
                names[key] = city.title()
    # Turn each list of ZIP points into one centre point per town.
    places = {}
    for key in primary.keys() | alternate.keys():
        # Prefer the town's own ZIPs; only use "alternate" ZIPs if it has none.
        pts = primary.get(key) or alternate[key]
        # The median (middle value) is robust: one far-away ZIP can't drag the centre off.
        places[key] = (names[key], median(p[0] for p in pts), median(p[1] for p in pts))
    return places


class Command(BaseCommand):
    """The `python manage.py load_fuel_stations` command."""

    help = "Load fuel stations from the CSV and geocode them using an offline US city gazetteer."

    def add_arguments(self, parser):
        # Optional: --csv path/to/other.csv (defaults to data/fuel-prices-for-be-assessment.csv).
        parser.add_argument("--csv", default=str(settings.FUEL_PRICES_CSV), help="Path to the fuel prices CSV")

    def handle(self, *args, **options):
        csv_path = Path(options["csv"])
        places = build_places()
        self.stdout.write(f"Built gazetteer with {len(places):,} US places")

        stations = {}  # OPIS station ID -> FuelStation (not saved yet)
        skipped_non_us, unmatched = 0, defaultdict(int)
        # "utf-8-sig" also copes with a hidden byte-order mark at the start of the file.
        with csv_path.open(newline="", encoding="utf-8-sig") as f:
            for row in csv.DictReader(f):  # each row is a dict keyed by the CSV header
                state = row["State"].strip().upper()
                city = row["City"].strip()
                if state not in US_STATES:
                    skipped_non_us += 1  # Canadian provinces: routes must stay within the USA.
                    continue
                # Look up the town's centre point; this is the station's location.
                place = places.get((normalize_city(city), state))
                if place is None:
                    unmatched[f"{city}, {state}"] += 1
                    continue
                opis_id = int(row["OPIS Truckstop ID"])
                # Decimal keeps money exact (floats can't store 3.099 exactly).
                price = Decimal(row["Retail Price"].strip())
                # The CSV lists some stations more than once; keep the cheapest listing.
                if opis_id in stations and stations[opis_id].price <= price:
                    continue
                stations[opis_id] = FuelStation(
                    opis_id=opis_id,
                    name=row["Truckstop Name"].strip(),
                    address=row["Address"].strip(),
                    city=city,
                    state=state,
                    rack_id=int(row["Rack ID"]) if row["Rack ID"].strip() else None,
                    price=price,
                    lat=place[1],  # place = (name, lat, lon)
                    lon=place[2],
                )

        # Replace the old data in one transaction: if anything fails, nothing changes.
        with transaction.atomic():
            Place.objects.all().delete()
            # bulk_create inserts thousands of rows in a few queries instead of one query per row.
            Place.objects.bulk_create(
                [Place(key=k, state=s, name=n, lat=lat, lon=lon) for (k, s), (n, lat, lon) in places.items()],
                batch_size=5000,
            )
            FuelStation.objects.all().delete()
            FuelStation.objects.bulk_create(stations.values(), batch_size=2000)
        # Make sure a running server picks up the new stations.
        clear_station_cache()

        self.stdout.write(self.style.SUCCESS(f"Loaded {len(stations):,} US fuel stations"))
        self.stdout.write(f"Skipped {skipped_non_us} non-US rows")
        if unmatched:
            self.stdout.write(self.style.WARNING(f"Could not geocode {sum(unmatched.values())} rows: {dict(unmatched)}"))
