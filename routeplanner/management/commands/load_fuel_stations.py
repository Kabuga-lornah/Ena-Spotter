"""Load the fuel price CSV into the database, geocoding every station offline.

The CSV only has city/state, so each station is placed at its town's centre,
computed from the US ZIP code database bundled with the `zipcodes` package.
This runs once, ahead of time, so the API never geocodes stations per request.
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
    """
    primary, alternate = defaultdict(list), defaultdict(list)
    names = {}
    for z in zipcodes.list_all():
        if z.get("country") != "US" or z["state"] not in US_STATES:
            continue
        lat, lon = float(z["lat"]), float(z["long"])
        if not is_in_usa(lat, lon):
            continue
        for city, bucket in [(z["city"], primary)] + [(c, alternate) for c in z.get("acceptable_cities", [])]:
            key = (normalize_city(city), z["state"])
            bucket[key].append((lat, lon))
            if bucket is primary or key not in names:
                names[key] = city.title()
    places = {}
    for key in primary.keys() | alternate.keys():
        pts = primary.get(key) or alternate[key]
        places[key] = (names[key], median(p[0] for p in pts), median(p[1] for p in pts))
    return places


class Command(BaseCommand):
    help = "Load fuel stations from the CSV and geocode them using an offline US city gazetteer."

    def add_arguments(self, parser):
        parser.add_argument("--csv", default=str(settings.FUEL_PRICES_CSV), help="Path to the fuel prices CSV")

    def handle(self, *args, **options):
        csv_path = Path(options["csv"])
        places = build_places()
        self.stdout.write(f"Built gazetteer with {len(places):,} US places")

        stations = {}
        skipped_non_us, unmatched = 0, defaultdict(int)
        with csv_path.open(newline="", encoding="utf-8-sig") as f:
            for row in csv.DictReader(f):
                state = row["State"].strip().upper()
                city = row["City"].strip()
                if state not in US_STATES:
                    skipped_non_us += 1  # Canadian provinces: routes must stay within the USA.
                    continue
                place = places.get((normalize_city(city), state))
                if place is None:
                    unmatched[f"{city}, {state}"] += 1
                    continue
                opis_id = int(row["OPIS Truckstop ID"])
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
                    lat=place[1],
                    lon=place[2],
                )

        with transaction.atomic():
            Place.objects.all().delete()
            Place.objects.bulk_create(
                [Place(key=k, state=s, name=n, lat=lat, lon=lon) for (k, s), (n, lat, lon) in places.items()],
                batch_size=5000,
            )
            FuelStation.objects.all().delete()
            FuelStation.objects.bulk_create(stations.values(), batch_size=2000)
        clear_station_cache()

        self.stdout.write(self.style.SUCCESS(f"Loaded {len(stations):,} US fuel stations"))
        self.stdout.write(f"Skipped {skipped_non_us} non-US rows")
        if unmatched:
            self.stdout.write(self.style.WARNING(f"Could not geocode {sum(unmatched.values())} rows: {dict(unmatched)}"))
