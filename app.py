
import streamlit as st
import requests
import folium
from streamlit_folium import st_folium
from streamlit_searchbox import st_searchbox
import time
import math

st.set_page_config(
    page_title="Expedition Route Planner",
    page_icon="🚛",
    layout="wide"
)

st.title("🚛 Expedition Route Planner")
st.write("Calculate and compare multiple transport routes.")

API_KEY = st.secrets["GRAPHHOPPER_API_KEY"]
BASE_URL = "https://graphhopper.com/api/1"

COLORS = ["blue", "green", "purple", "yellow", "orange", "red", "white"]

def distance_km(lat1, lon1, lat2, lon2):
    """Calculate straight-line distance between two coordinates."""
    earth_radius_km = 6371.0

    lat1 = math.radians(lat1)
    lon1 = math.radians(lon1)
    lat2 = math.radians(lat2)
    lon2 = math.radians(lon2)

    dlat = lat2 - lat1
    dlon = lon2 - lon1

    a = (
        math.sin(dlat / 2) ** 2
        + math.cos(lat1)
        * math.cos(lat2)
        * math.sin(dlon / 2) ** 2
    )

    return 2 * earth_radius_km * math.asin(
        math.sqrt(a)
    )


@st.cache_data(ttl=3600, show_spinner=False)
def search_cities(query):
    if len(query.strip()) < 3:
        return []

    response = requests.get(
        f"{BASE_URL}/geocode",
        params={
            "q": query,
            "limit": 8,
            "locale": "en",
            "key": API_KEY,
        },
        timeout=15,
    )
    response.raise_for_status()

    data = response.json()
    cities = []
    seen = set()

    for hit in data.get("hits", []):
        if hit.get("osm_value") not in (
            "city", "town", "village", "municipality"
        ):
            continue

        name = hit.get("name", "")
        country = hit.get("country", "")

        if not name or "point" not in hit:
            continue

        label = f"{name}, {country}"

        if label in seen:
            continue

        seen.add(label)

        cities.append({
            "label": label,
            "lat": hit["point"]["lat"],
            "lng": hit["point"]["lng"],
        })

    return cities


def city_search(searchterm):
    if not searchterm or len(searchterm.strip()) < 3:
        return []

    try:
        cities = search_cities(searchterm.strip())

        return [
            (
                city["label"],
                (city["lat"], city["lng"], city["label"]),
            )
            for city in cities
        ]

    except requests.RequestException:
        return []



# GRAPHHOPPER ROUTING
@st.cache_data(ttl=3600)
def calculate_routes(start, end):
    response = requests.get(
        f"{BASE_URL}/route",
        params=[
            ("point", f"{start[0]},{start[1]}"),
            ("point", f"{end[0]},{end[1]}"),
            ("profile", "car"),
            ("algorithm", "alternative_route"),
            ("alternative_route.max_paths", 3),
            ("points_encoded", "false"),
            ("instructions", "false"),
            ("key", API_KEY),
        ],
        timeout=60,
    )

    response.raise_for_status()
    data = response.json()

    if not data.get("paths"):
        raise ValueError("No routes found.")

    return data["paths"]


# REVERSE GEOCODING
@st.cache_data(ttl=86400, show_spinner=False)
def reverse_geocode(lat, lon):
    params = {
        "point": f"{lat},{lon}",
        "reverse": "true",
        "locale": "en",
        "limit": 1,
        "key": API_KEY,
    }

    for attempt in range(3):
        response = requests.get(
            f"{BASE_URL}/geocode",
            params=params,
            timeout=15,
        )

        if response.status_code == 429:
            if attempt == 2:
                response.raise_for_status()

            retry_after = response.headers.get("Retry-After")

            try:
                wait_seconds = (
                    float(retry_after)
                    if retry_after
                    else 2 ** (attempt + 1)
                )
            except ValueError:
                wait_seconds = 2 ** (attempt + 1)

            time.sleep(min(wait_seconds, 10))
            continue

        response.raise_for_status()

        hits = response.json().get("hits", [])

        if not hits:
            return None

        hit = hits[0]

        return {
            "name": hit.get("name"),
            "city": hit.get("city"),
            "town": hit.get("town"),
            "village": hit.get("village"),
            "municipality": hit.get("municipality"),
            "state": hit.get("state"),
            "country": hit.get("country"),
            "osm_value": hit.get("osm_value"),
            "osm_key": hit.get("osm_key"),
            "point": hit.get("point"),
        }

    st.write({
        "name": hit.get("name"),
        "city": hit.get("city"),
        "town": hit.get("town"),
        "osm_value": hit.get("osm_value"),
        "osm_key": hit.get("osm_key"),
    })

    return None

# SAMPLE ROUTE COORDINATES
def sample_route_coordinates(route, max_samples=50):
    coordinates = route.get("points", {}).get("coordinates", [])

    if not coordinates:
        return []

    count = min(max_samples, len(coordinates))

    indices = sorted({
        round(i * (len(coordinates) - 1) / max(1, count - 1))
        for i in range(count)
    })

    return [
        (coordinates[i][1], coordinates[i][0])
        for i in indices
    ]


# DETECT CITIES AND COUNTRIES
@st.cache_data(ttl=86400, show_spinner=False)
def get_route_places(route_coordinates):
    cities = []
    countries = []
    errors = []
    city_markers = []

    seen_cities = set()
    seen_countries = set()

    for lat, lon in route_coordinates:
        try:
            place = reverse_geocode(lat, lon)
        except requests.RequestException as exc:
            errors.append(str(exc))
            continue

        if not place:
            continue

        country = place.get("country")

        if country:
            country_key = country.casefold()

            if country_key not in seen_countries:
                seen_countries.add(country_key)
                countries.append(country)

        osm_value = (
            place.get("osm_value") or ""
        ).casefold()

        city_name = None

        if osm_value in {"city", "town"}:
            city_name = place.get("name")

        if not city_name:
            city_name = (
                place.get("city")
                or place.get("town")
            )

        if not city_name:
            continue


        city_name = city_name.strip()
        city_key = city_name.casefold()

        if not city_key or city_key in seen_cities:
            continue

        # this could be an input 
        min_distance_km = 150

        too_close = any(
            distance_km(
                lat, lon,
                marker["lat"], marker["lon"]
            ) < min_distance_km
            for marker in city_markers
        )

        if too_close:
            continue

        seen_cities.add(city_key)
        cities.append(city_name)

        city_markers.append({
            "name": city_name,
            "lat": lat,
            "lon": lon,
        })

        country_key = (
            country.casefold() if country else ""
        )
        city_key = (city_name.casefold(), country_key)

        if city_key in seen_cities:
            continue

        seen_cities.add(city_key)
        cities.append(city_name)

        city_markers.append({
            "name": city_name,
            "lat": lat,
            "lon": lon,
        })

    return {
        "cities": cities,
        "countries": countries,
        "city_markers": city_markers,
        "errors": errors,
    }

def format_duration(milliseconds):
    total_minutes = round(milliseconds / 60000)
    hours, minutes = divmod(total_minutes, 60)

    if hours and minutes:
        return f"{hours}h {minutes}m"

    if hours:
        return f"{hours}h"

    return f"{minutes}m"


def format_saved_duration(milliseconds):
    if milliseconds <= 0:
        return "0 min"

    return format_duration(milliseconds)


def show_route_cards(routes, route_places):
    fastest_index = min(
        range(len(routes)),
        key=lambda i: routes[i]["time"],
    )

    shortest_index = min(
        range(len(routes)),
        key=lambda i: routes[i]["distance"],
    )

    slowest_time = max(route["time"] for route in routes)
    longest_distance = max(
        route["distance"] for route in routes
    )

    st.subheader("📊 Compare your routes")

    st.caption(
        "Travel times are GraphHopper estimates. City and country "
        "details are estimated from sampled route points and may "
        "omit places."
    )

    cols = st.columns(len(routes))

    for i, (col, route) in enumerate(zip(cols, routes)):
        distance_km = route["distance"] / 1000
        duration_ms = route["time"]

        places = route_places[i]
        cities = places.get("cities", [])
        countries = places.get("countries", [])

        with col:
            with st.container(border=True):
                st.markdown(f"### Route {i + 1}")

                badges = []

                if i == fastest_index:
                    badges.append("⚡ Fastest")

                if i == shortest_index:
                    badges.append("📏 Shortest")

                if badges:
                    st.markdown(" · ".join(badges))

                st.metric(
                    "Travel time",
                    format_duration(duration_ms),
                )

                st.metric(
                    "Distance",
                    f"{distance_km:,.1f} km",
                )

                time_saved = slowest_time - duration_ms
                distance_saved = longest_distance - route["distance"]

                st.caption(
                    f"⏱️ {format_saved_duration(time_saved)} "
                    "quicker than the slowest route"
                )

                st.caption(
                    f"📉 {distance_saved / 1000:,.1f} km "
                    "shorter than the longest route"
                )

                st.divider()

                st.markdown("**🌍 Countries traversed**")
                st.metric("Countries detected", len(countries))

                if countries:
                    st.write(" → ".join(countries))
                else:
                    st.caption("Country information unavailable")

                st.markdown("**🏙️ Cities along the route**")
                st.metric("Cities detected", len(cities))

                if cities:
                    st.write(" → ".join(cities))
                else:
                    st.caption("No cities detected along this route.")

                if i == fastest_index and i == shortest_index:
                    st.success("Fastest and shortest")
                elif i == fastest_index:
                    st.success("Best for travel time")
                elif i == shortest_index:
                    st.info("Best for distance")


col1, col2 = st.columns(2)

with col1:
    departure = st_searchbox(
        search_function=city_search,
        label="Departure city",
        placeholder="Start typing a city...",
        key="departure_search",
        debounce=300,
        edit_after_submit="option",
        style_overrides={
            "searchbox": {"optionEmpty": "hidden"}
        },
    )

with col2:
    destination = st_searchbox(
        search_function=city_search,
        label="Destination city",
        placeholder="Start typing a city...",
        key="destination_search",
        debounce=300,
        edit_after_submit="option",
        style_overrides={
            "searchbox": {"optionEmpty": "hidden"}
        },
    )

# CALCULATE ROUTES
if st.button("Calculate Routes", type="primary"):
    if not departure or not destination:
        st.warning("Please select both cities.")

    else:
        try:
            start = departure[:2]
            end = destination[:2]

            if start == end:
                st.warning(
                    "Please select two different locations."
                )
                st.stop()

            with st.spinner("Finding available routes..."):
                routes = calculate_routes(start, end)

                st.session_state["routes"] = routes
                st.session_state["start"] = start
                st.session_state["end"] = end

                st.session_state["city_labels"] = (
                    departure[2],
                    destination[2],
                )

                for key in list(st.session_state.keys()):
                    if key.startswith("route_visible_"):
                        del st.session_state[key]

        except requests.HTTPError as error:
            detail = (
                error.response.text[:500]
                if error.response is not None
                else str(error)
            )

            st.error(f"GraphHopper API error: {detail}")

        except Exception as error:
            st.error(f"Could not calculate routes: {error}")

# DISPLAY ROUTES AND MAP
if "routes" in st.session_state:
    routes = st.session_state["routes"]
    start = st.session_state["start"]
    end = st.session_state["end"]

    start_label, end_label = st.session_state.get(
        "city_labels",
        ("Departure", "Destination"),
    )

    st.subheader(
        f"Routes: {start_label} → {end_label}"
    )

    route_places = []

    with st.spinner(
        "Checking cities and countries along routes..."
    ):
        for route in routes:
            coordinates = route.get(
                "points", {}
            ).get("coordinates", [])

            if not coordinates:
                route_places.append({
                    "cities": [],
                    "countries": [],
                    "city_markers": [],
                    "errors": [],
                })
                continue

            sampled_points = tuple(
                sample_route_coordinates(
                    route,
                    max_samples=50,
                )
            )

            places = get_route_places(sampled_points)
            route_places.append(places)

    show_route_cards(routes, route_places)

    st.subheader("🗺️ Map")

    st.caption(
        "Use the checkboxes to show or hide routes. "
        "Each route and its city pins share the same color."
    )

    selected_routes = []
    checkbox_cols = st.columns(len(routes))

    for index, (col, route) in enumerate(
        zip(checkbox_cols, routes)
    ):
        distance_km = route["distance"] / 1000
        duration = format_duration(route["time"])

        label = (
            f"Route {index + 1} · "
            f"{distance_km:.1f} km · {duration}"
        )

        with col:
            visible = st.checkbox(
                label,
                value=True,
                key=f"route_visible_{index}",
            )

        if visible:
            selected_routes.append((index, route))

    # CREATE MAP
    m = folium.Map(
        location=[
            (start[0] + end[0]) / 2,
            (start[1] + end[1]) / 2,
        ],
        zoom_start=6,
        control_scale=True,
    )

    # Departure Pin
    folium.Marker(
        location=start,
        tooltip=f"Departure: {start_label}",
        icon=folium.Icon(
            color="green",
            icon="play",
            prefix="fa",
        ),
    ).add_to(m)

    # Destination Pin
    folium.Marker(
        location=end,
        tooltip=f"Destination: {end_label}",
        icon=folium.Icon(
            color="red",
            icon="flag-checkered",
            prefix="fa",
        ),
    ).add_to(m)

    all_points = []

    for index, route in selected_routes:
        coordinates = route["points"]["coordinates"]

        points = [
            [lat, lon]
            for lon, lat in coordinates
        ]

        all_points.extend(points)

        route_color = COLORS[index % len(COLORS)]

        folium.PolyLine(
            points,
            color=route_color,
            weight=6,
            opacity=0.8,
            tooltip=(
                f"Route {index + 1}: "
                f"{route['distance'] / 1000:.1f} km · "
                f"{format_duration(route['time'])}"
            ),
        ).add_to(m)

        places = route_places[index]

        for city in places.get("city_markers", []):
            folium.Marker(
                location=[
                    city["lat"],
                    city["lon"],
                ],
                tooltip=(
                    f"{city['name']} · Route {index + 1}"
                ),
                popup=folium.Popup(
                    f"<b>{city['name']}</b><br>"
                    f"Route {index + 1}",
                    max_width=250,
                ),
                icon=folium.Icon(
                    color=route_color,
                    icon="map-marker",
                    prefix="fa",
                ),
            ).add_to(m)

    if all_points:
        m.fit_bounds(all_points)
    else:
        m.fit_bounds([start, end])

    st_folium(
        m,
        width=None,
        height=600,
        returned_objects=[],
        key="expedition_route_map",
    )
