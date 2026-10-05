# Spotter Fuel-Optimized Driving Route API

A production-quality Django REST API that calculates driving routes between any two points in the USA and determines the **cost-optimal sequence of fuel stops** along the route based on vehicle constraints and real-world fuel prices.

---

## Table of Contents
1. [Project Overview](#project-overview)
2. [Architecture](#architecture)
3. [Fuel-Price Dataset & Geolocation Strategy](#fuel-price-dataset--geolocation-strategy)
4. [Fuel Optimization Algorithm](#fuel-optimization-algorithm)
5. [Vehicle & Fuel Accounting Model](#vehicle--fuel-accounting-model)
6. [Why Only 1 Routing Call?](#why-only-1-routing-call)
7. [Performance Considerations](#performance-considerations)
8. [Setup & Installation](#setup--installation)
9. [Database Setup & Data Import](#database-setup--data-import)
10. [Running the Application](#running-the-application)
11. [API Specification](#api-specification)
12. [Postman Collection](#postman-collection)
13. [Testing](#testing)
14. [Error Handling & Status Codes](#error-handling--status-codes)
15. [Loom Video Talking Points (5-Minute Guide)](#loom-video-talking-points-5-minute-guide)

---

## Project Overview

When operating commercial heavy freight or road vehicles across interstate highways, fuel is one of the highest variable operating costs. Retail diesel and gasoline prices vary significantly across state lines and cities (often differing by more than $1.00 - $2.50 per gallon).

This service accepts a **Start** and **Finish** location in the United States, retrieves the full driving route geometry, projects candidate fuel stations along the route corridor, and solves for the **minimum-cost sequence of fuel stops** such that the vehicle never exceeds its 500-mile maximum single-tank range.

---

## Architecture

The project is structured with a strict separation of concerns, keeping views thin, business logic isolated in dedicated service classes, and data operations optimized at the database level.

```
spotter-backend/
├── config/
│   ├── settings.py           # Production-ready Django settings (env-configured, cache, DB)
│   ├── urls.py               # Root URL configuration
│   ├── wsgi.py
│   └── asgi.py
├── routing/
│   ├── models.py             # FuelStation model with spatial/coordinate database indexes
│   ├── serializers.py        # Strict DRF request and response validation schemas
│   ├── views.py              # API view with centralized, safe error handling
│   ├── urls.py               # /api/route/ endpoint mapping
│   ├── services/
│   │   ├── geo_utils.py          # Haversine distance, segment projections, RouteSpatialIndex
│   │   ├── geocoding_service.py  # US location geocoder with offline city fallback & caching
│   │   ├── routing_service.py    # Free driving route provider (OSRM + ORS support)
│   │   └── fuel_optimizer.py     # Fuel-state optimal purchase planner (fixed-capacity greedy)
│   ├── management/
│   │   └── commands/
│   │       └── import_fuel_prices.py # Bulk CSV importer with deduplication & stats
│   └── tests/
│       ├── test_models.py
│       ├── test_geo_utils.py
│       ├── test_import_command.py
│       ├── test_fuel_optimizer.py
│       └── test_api.py
├── data/
│   ├── fuel-prices-for-be-assessment.csv # Source dataset (8,151 rows)
│   └── us_cities.json                   # Bundled offline US city coordinate reference
├── postman/
│   └── spotter-fuel-route.postman_collection.json # Ready-to-import Postman collection
├── manage.py
├── requirements.txt
├── .env.example
├── .gitignore
└── README.md
```

---

## Fuel-Price Dataset & Geolocation Strategy

### Inspection Findings
The provided `fuel-prices-for-be-assessment.csv` contains 8,151 rows with columns:
- `OPIS Truckstop ID`
- `Truckstop Name`
- `Address`
- `City`
- `State`
- `Rack ID`
- `Retail Price`

### Key Geographic Limitation & Design Decision
1. **No Station Coordinates in CSV**: The dataset contains **NO latitude or longitude coordinates** for truck stops.
2. **City/State Approximation**: Geocoding 8,151 stations against a remote API during import or runtime would hit third-party rate limits, take hours, and violate the assignment's rule against calling external APIs per fuel station.
3. **Offline Coordinate Reference**: We bundled a lightweight, high-accuracy offline reference mapping (`data/us_cities.json`) covering over 51,000 US cities and postal centroids (derived from open US Census/GeoNames gazetteer data).
4. **Transparency**: The coordinates stored on `FuelStation` represent the geographic centroid of the station's municipality/city exit. **They do not claim to represent the exact physical pump coordinates.**
5. **Decoupled Abstraction**: The import command and geolocation service are completely isolated. When real station-level GPS coordinates become available, they can be loaded directly without changing the optimization engine.

---

## Fuel Optimization Algorithm

Implementation: [`compute_optimal_fuel_plan`](routing/services/fuel_optimizer.py) (pure function, no DB/network) wrapped by `FuelOptimizer`.

### 1. Candidate stations (local, no external calls)
1. **Bounding-box SQL query** on indexed `latitude`/`longitude` (route bbox + 0.5°).
2. **Corridor projection** with `RouteSpatialIndex` (grid hash of route segments): for each station we get its off-route distance and its position along the route.
3. Stations more than `CORRIDOR_SEARCH_RADIUS_MILES` (default 10) off the route are dropped. No other pruning is applied, so no cheap station is discarded.
4. Along-route positions are measured on the geometry and **rescaled to OSRM's road distance**, so station positions and the destination use the same mileage scale.

### 2. Fuel-state model
- Tank capacity `C = 500 / 10 = 50 gal`. The vehicle **starts full; that fuel costs nothing**.
- The vehicle may buy **any amount** at a stop (partial fill or fill-up), never more than `C`.
- Fuel is tracked continuously: `fuel_on_arrival = fuel_after_previous_stop - leg_miles / 10`, never negative.
- Nothing is bought at the destination.

### 3. Purchase rule (fixed-capacity "gas station" greedy)
At the current node with price `p` (the origin counts as `p = 0` because its fuel is already paid for):

| Situation (looking up to 500 mi ahead) | Action |
|---|---|
| A station **cheaper than `p`** is in range | Drive to the **nearest** cheaper one, buying only `max(0, leg/10 - fuel)` gallons |
| Else, the destination is in range | Buy only `max(0, remaining/10 - fuel)` gallons and finish |
| Else | **Fill the tank** (current fuel is the cheapest reachable) and drive to the **cheapest** station in range |
| Nothing in range | `NoFeasibleFuelPlanError` (HTTP 422) |

Stations where the rule buys 0 gallons are passed through and **not reported** as stops.

### 4. Why this is optimal
Fuel burned at mile `x` must have entered the tank within the previous 500 miles, because the tank holds only 500 miles of fuel. Fuel for miles 0–500 can be the free starting fuel. So no plan can pay less than

```
LowerBound = Σ over each mile x of (1/MPG) × min price of any station in [x − 500, x)   (0 for x ≤ 500)
```

The greedy hits this bound:
- It carries cheap fuel as far as possible (fill-up) when nothing cheaper is reachable.
- It switches to cheaper fuel as soon as that fuel is reachable (buy just enough).

This is the classic fixed-route result. The test-suite also checks it numerically against an **exhaustive DP over (station, fuel-in-tank) states** on 400 random instances (`test_fuel_plan_math.py`).

What this means in practice:
- An expensive early station is skipped when the starting fuel can reach a cheaper one.
- At a cheap station the vehicle fills up and carries that fuel past expensive stations.
- At an expensive station it buys only enough to reach the next cheaper one.
- The plan ends with an empty tank whenever any fuel was bought, so no paid fuel is left over.

Complexity: `O(K × W)`, where `K` = corridor stations and `W` = stations within one tank of range.

---

## Vehicle & Fuel Accounting Model

| Quantity | Definition |
|---|---|
| `starting_fuel_gallons` | 50 (full tank, not charged) |
| `total_fuel_consumed_gallons` | `route_distance / 10` |
| `fuel_purchased_gallons` (per stop) | amount chosen by the rule above |
| `total_fuel_purchased_gallons` | `Σ fuel_purchased_gallons` |
| `ending_fuel_gallons` | fuel left on arrival |
| `cost` (per stop) | `fuel_purchased_gallons × price_per_gallon` |
| `total_fuel_cost` | `Σ cost` |

Identity that always holds (tested at unit, service and API level):

```
starting_fuel + total_fuel_purchased = total_fuel_consumed + ending_fuel
```

- **Route ≤ 500 mi**: 0 stops, purchased 0, cost $0, ending fuel `50 − distance/10`.
- **Route > 500 mi**: purchased `= distance/10 − 50` and ending fuel `= 0`. The plan never pays for fuel it does not burn.

Every stop also reports `fuel_on_arrival_gallons` and `fuel_after_purchase_gallons`, so the tank level can be checked by hand. Displayed values are rounded to 2 decimals; internal calculations are exact.

---

## Why Only 1 Routing Call?

A key evaluation criterion is minimizing external API overhead:
1. **No External Calls for Fuel Stations**: Calling a routing or geocoding provider once per fuel station would require thousands of network calls, taking minutes and exceeding free tier rate limits.
2. **Local Route Projection**: The route geometry (GeoJSON `LineString`) is fetched **exactly once** from the routing engine (OSRM).
3. **Local Spatial Optimization**: Stations are fetched from the local indexed database (1 SQL query), projected onto the polyline via `RouteSpatialIndex`, and the fuel plan is computed locally.
4. **City Geocoding**: Start and finish cities are resolved offline using `data/us_cities.json` (or cached), requiring **0 to 2 geocoding calls** at most.
5. **Total External Calls Per Request**:
   - Geocode Start: 0 (resolved offline/cached) or 1
   - Geocode Finish: 0 (resolved offline/cached) or 1
   - Driving Route: **1 call to OSRM**
   - Fuel Optimization: **0 external calls**
   - **Total: 1 routing call (3 external calls absolute maximum, 0 when cached).**

---

## Performance Considerations

- **Database Indexes**: Compound index on `(latitude, longitude)`, index on `(state, city)`, and index on `retail_price`.
- **Fast Bounding Box Filtering**: SQL filters candidates to a tight bounding box corridor before geometry calculations.
- **RouteSpatialIndex**: Route segments are bucketed into 0.25° grid cells, so each station is compared only with nearby segments, not all ~35,000 route points.
- **Single SQL query** per request (`.only()` includes every field used in the response, so there is no N+1).
- **Measured (NY → LA, 2,809 mi, 35k route points, 458 corridor stations)**:
  - Local candidate search + optimisation: **~0.36 s**.
  - The OSRM public demo server adds **~1.5–4 s** of network latency.
  - Repeat requests are served from the route/geocode cache.

---

## Setup & Installation

### Requirements
- Python 3.10+ (tested on Python 3.12)
- pip

### Step-by-Step Setup
```bash
# 1. Clone the repository
git clone <repo-url>
cd "spotter backend"

# 2. Create and activate a virtual environment
python -m venv venv
# Windows:
.\venv\Scripts\activate
# Linux/macOS:
source venv/bin/activate

# 3. Install dependencies
pip install -r requirements.txt

# 4. Configure environment variables
cp .env.example .env
```

---

## Database Setup & Data Import

```bash
# 1. Run database migrations
python manage.py migrate

# 2. Import fuel price dataset
python manage.py import_fuel_prices data/fuel-prices-for-be-assessment.csv
```

### Import Summary:
The import command validates rows, safely parses prices, deduplicates duplicate OPIS IDs by preserving the lowest retail price, skips Canadian records, and maps US stations to coordinates:
```
============================================================
Fuel Price Import Summary:
  Total CSV rows read:       8151
  Valid processed rows:      7531
  Unique stations in DB:     6626
  Newly created stations:    6626
  Updated existing stations: 0
  Skipped (Canadian):        620
  Skipped (No coordinates):  0
  Skipped (Invalid price):   0
  Skipped (Invalid ID):      0
  Execution time:            0.46 seconds
============================================================
```

---

## Running the Application

```bash
python manage.py runserver
```

The API will be available at: `http://127.0.0.1:8000/api/route/`

---

## API Specification

### Endpoint: `POST /api/route/`
**Content-Type**: `application/json`

#### Example Request:
```json
{
  "start": "New York, NY",
  "finish": "Los Angeles, CA"
}
```

#### Example Response (real output, geometry and stops truncated):
```json
{
  "request": {
    "start": "New York, NY",
    "finish": "Los Angeles, CA",
    "start_resolved": {"name": "New York, NY, USA", "latitude": 40.6943, "longitude": -73.9249},
    "finish_resolved": {"name": "Los Angeles, CA, USA", "latitude": 34.1141, "longitude": -118.4068}
  },
  "vehicle": {"max_range_miles": 500.0, "mpg": 10.0, "tank_capacity_gallons": 50.0},
  "route": {
    "distance_miles": 2809.33,
    "duration_minutes": 3016.3,
    "geometry": {"type": "LineString", "coordinates": [[-73.924563, 40.69411], [-73.925135, 40.693527], "..."]}
  },
  "fuel_plan": {
    "stops": [
      {
        "stop_number": 1,
        "station_id": 72445,
        "name": "SHEETZ #639",
        "address": "I-80 Exit 223",
        "city": "Youngstown",
        "state": "OH",
        "latitude": 41.0993,
        "longitude": -80.6463,
        "price_per_gallon": 3.059,
        "distance_from_start_miles": 395.09,
        "distance_from_previous_stop_miles": 395.09,
        "fuel_on_arrival_gallons": 10.49,
        "fuel_purchased_gallons": 6.07,
        "fuel_after_purchase_gallons": 16.56,
        "cost": 18.58
      },
      {
        "stop_number": 2,
        "station_id": 72288,
        "name": "S&G #88",
        "address": "I-475 Exit 13 & US-20",
        "city": "Toledo",
        "state": "OH",
        "latitude": 41.6638,
        "longitude": -83.5827,
        "price_per_gallon": 3.009,
        "distance_from_start_miles": 560.73,
        "distance_from_previous_stop_miles": 165.64,
        "fuel_on_arrival_gallons": 0.0,
        "fuel_purchased_gallons": 29.59,
        "fuel_after_purchase_gallons": 29.59,
        "cost": 89.05
      }
    ],
    "starting_fuel_gallons": 50.0,
    "total_fuel_consumed_gallons": 280.93,
    "total_fuel_purchased_gallons": 230.93,
    "ending_fuel_gallons": 0.0,
    "distance_from_last_stop_to_destination_miles": 288.87,
    "total_fuel_cost": 698.29,
    "assumptions_note": "Vehicle departs with a full 50-gallon tank (not charged). At each stop it buys only what is cost-optimal: a fill-up when no cheaper fuel is reachable, otherwise just enough to reach the next cheaper station or the destination. No fuel is purchased at the destination."
  }
}
```

Check: `50 + 230.93 = 280.93 + 0.00` ✓. Every leg ≤ 500 mi ✓. Full route has 15 stops.

---

## Postman Collection

A complete, production-ready Postman collection is located at:
[`postman/spotter-fuel-route.postman_collection.json`](file:///d:/spotter%20backend/postman/spotter-fuel-route.postman_collection.json)

### Included Requests:
1. **Long Route (NYC to LA)**: Tests cross-country route optimization with multiple fuel stops.
2. **Medium Route (Chicago to Miami)**: Tests North-South optimization (~1,370 miles).
3. **Short Route (NYC to Philadelphia)**: Tests $\le 500$ mile route (0 stops, full-tank start).
4. **Invalid Request - Same Start/Finish**: Validates HTTP 400 response.
5. **Invalid Request - Missing Parameters**: Validates HTTP 400 response.

---

## Testing

Run the automated test suite:
```bash
python manage.py test
```

### Test Coverage Highlights (30 tests):
- `test_fuel_plan_math.py`: pure algorithm tests.
  - Full-tank start, short route, exactly 500 mi, the 600-mile "buy only 10 gal" case.
  - Skipping an unnecessary early station; the spec's A/B/C price example.
  - Carrying cheap fuel past an expensive station; buying just enough to reach a cheaper one.
  - Partial final leg, multiple stops, impossible gaps, the accounting identity, legs ≤ 500.
  - **Cross-check against an exhaustive (station, fuel) DP on 400 random instances.**
- `test_fuel_optimizer.py`: DB-backed corridor search + optimisation, accounting identity, impossible route.
- `test_api.py`: validation, short/long routes (mocked OSRM + geocoder), per-stop tank consistency, 502/504 handling.
- `test_import_command.py`: CSV validation, price parsing, Canadian skipping, deduplication.
- `test_geo_utils.py`, `test_models.py`.

All tests use mocks for external network services to ensure fast, offline, deterministic test runs.

---

## Error Handling & Status Codes

| Scenario | HTTP Status | Response Format |
|---|---|---|
| Missing or empty fields | `400 Bad Request` | `{"error": "Validation Error", "details": {...}}` |
| Start and finish are identical | `400 Bad Request` | `{"error": "Validation Error", "details": {"finish": ...}}` |
| Location not found or outside USA | `400 Bad Request` | `{"error": "Invalid Location", "detail": "..."}` |
| No driving route exists | `404 Not Found` | `{"error": "No Route Found", "detail": "..."}` |
| Route has gap $> 500$ miles with no fuel | `422 Unprocessable Entity` | `{"error": "Unfeasible Fuel Plan", "detail": "..."}` |
| External routing service times out | `504 Gateway Timeout` | `{"error": "Routing Gateway Timeout", "detail": "..."}` |
| External routing provider 500/down | `502 Bad Gateway` | `{"error": "Bad Gateway", "detail": "..."}` |
| Unexpected internal exception | `500 Internal Error` | `{"error": "Internal Server Error", "detail": "..."}` |

*Django stack traces and internal exceptions are never exposed to clients.*

---

## Loom Video Talking Points (5-Minute Guide)

When presenting this project in a 5-minute Loom video, follow this recommended outline:

1. **Introduction & Architecture (1:00)**:
   - Introduce the project built with latest Django & Django REST Framework.
   - Point out clean service-layer design (`GeocodingService`, `RoutingService`, `FuelOptimizer`, `RouteSpatialIndex`).
   - Mention the database design: `FuelStation` with compound spatial indexes.
2. **Dataset & Geolocation Strategy (0:45)**:
   - Explain the inspection of `fuel-prices-for-be-assessment.csv`: 8,151 rows with city/state and prices, but no GPS coordinates.
   - Explain the design decision: bundled offline city/state reference (`data/us_cities.json`) allowing instant offline import in 0.46s without hitting external geocoding limits.
3. **The Core Algorithm (1:15)**:
   - Why "cheapest station" or "nearest station" fails: it ignores range and tank level.
   - The fuel-state greedy: start full (free fuel).
     - If a cheaper station is reachable, buy just enough to get there.
     - Otherwise, fill up and go to the cheapest reachable station.
     - Never buy at the destination.
   - Why it is optimal: every mile is fuelled by the cheapest fuel that could be in the tank at that mile. An exhaustive fuel-state DP in the tests confirms this.
   - Show the accounting identity `start + purchased = consumed + ending` in the response.
4. **Postman Live Demonstration (1:30)**:
   - Run **New York, NY to Los Angeles, CA**. Show:
     - distance (2,809 mi) and the GeoJSON line;
     - the stops with fuel on arrival, purchased and after purchase;
     - consumed 280.93 vs purchased 230.93 gal, total cost $698.29.
   - Run **New York, NY to Philadelphia, PA**: 0 stops, $0, because the truck starts full.
   - Run **Invalid Request**: show clean 400 Bad Request error response.
5. **Tests & Wrap-Up (0:30)**:
   - Run `python manage.py test` to show the 30 automated tests passing.
   - Summarize GitHub readiness.
