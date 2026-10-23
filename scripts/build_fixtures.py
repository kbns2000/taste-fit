"""Build the deterministic demo world used by the mock Qloo client.

Run: python scripts/build_fixtures.py  -> writes app/qloo/fixtures.json

The world is small on purpose: ~50 entities across brands, artists, podcasts,
places, movies and destinations, a shared vocabulary of ~45 taste tags, and
a handful of cities with neighbourhoods. Affinities are derived from tag
vectors so that results stay coherent (fans of Patagonia *should* index on
Bon Iver and third-wave coffee). It is clearly labelled as demo data in the
product; it exists so the full agent flow runs without a Qloo key and so
tests are reproducible.
"""

from __future__ import annotations

import hashlib
import json
import uuid
from pathlib import Path

NS = uuid.UUID("5b1f0a4e-7d8e-4a9f-9c9a-3f1d2c4b5a60")


def eid(name: str) -> str:
    return str(uuid.uuid5(NS, name)).upper()


def tag(kind: str, slug: str, name: str) -> tuple[str, dict]:
    return f"urn:tag:{kind}:{slug}", {"name": name, "type": f"urn:tag:{kind}"}


TAGS = dict([
    tag("keyword:brand", "sustainability", "Sustainability"),
    tag("keyword:brand", "outdoor_lifestyle", "Outdoor Lifestyle"),
    tag("keyword:brand", "hiking", "Hiking"),
    tag("keyword:brand", "climbing", "Climbing"),
    tag("keyword:brand", "trail_running", "Trail Running"),
    tag("keyword:brand", "minimalism", "Minimalism"),
    tag("keyword:brand", "craft_coffee", "Craft Coffee"),
    tag("keyword:brand", "third_wave_coffee", "Third Wave Coffee"),
    tag("keyword:brand", "wellness", "Wellness"),
    tag("keyword:brand", "yoga", "Yoga"),
    tag("keyword:brand", "running", "Running"),
    tag("keyword:brand", "streetwear", "Streetwear"),
    tag("keyword:brand", "skate_culture", "Skate Culture"),
    tag("keyword:brand", "luxury", "Luxury"),
    tag("keyword:brand", "premium_pricing", "Premium Pricing"),
    tag("keyword:brand", "design_led", "Design Led"),
    tag("keyword:brand", "clean_beauty", "Clean Beauty"),
    tag("keyword:brand", "natural_ingredients", "Natural Ingredients"),
    tag("keyword:brand", "tech_early_adopters", "Tech Early Adopters"),
    tag("keyword:brand", "remote_work", "Remote Work"),
    tag("keyword:brand", "travel", "Travel"),
    tag("keyword:brand", "van_life", "Van Life"),
    tag("keyword:brand", "cycling", "Cycling"),
    tag("keyword:brand", "vintage", "Vintage"),
    tag("keyword:brand", "urban_living", "Urban Living"),
    tag("keyword:brand", "foodie", "Foodie"),
    tag("keyword:brand", "plant_based", "Plant Based"),
    tag("keyword:brand", "home_decor", "Home Decor"),
    tag("keyword:brand", "fitness_community", "Fitness Community"),
    tag("keyword:brand", "limited_drops", "Limited Drops"),
    tag("keyword:brand", "sneaker_culture", "Sneaker Culture"),
    tag("keyword:brand", "photography", "Photography"),
    tag("keyword:brand", "bookish", "Bookish"),
    tag("keyword:brand", "meditation", "Meditation"),
    tag("keyword:brand", "family", "Family"),
    tag("keyword:brand", "value_pricing", "Value Pricing"),
    tag("genre:music", "indie_folk", "Indie Folk"),
    tag("genre:music", "psychedelic_rock", "Psychedelic Rock"),
    tag("genre:music", "electronic", "Electronic"),
    tag("genre:music", "hip_hop", "Hip Hop"),
    tag("genre:music", "pop", "Pop"),
    tag("genre:media", "documentary", "Documentary"),
    tag("genre:media", "storytelling", "Storytelling"),
    tag("genre:media", "science", "Science"),
    tag("genre:media", "business", "Business"),
    tag("category:place", "cafe", "Cafe"),
    tag("category:place", "music_venue", "Music Venue"),
    tag("category:place", "climbing_gym", "Climbing Gym"),
    tag("category:place", "bookstore", "Bookstore"),
    tag("category:place", "restaurant", "Restaurant"),
    tag("category:place", "park", "Park"),
    tag("category:place", "retail_street", "Retail Street"),
    tag("category:place", "hotel", "Hotel"),
    tag("category:place", "grocery", "Grocery"),
])

T = {k.split(":")[-1]: k for k in TAGS}  # slug -> tag id


def vec(**weights: float) -> dict[str, float]:
    return {T[slug]: w for slug, w in weights.items()}


def demo(age: list[float], male: float) -> dict:
    keys = ["24_and_younger", "25_to_29", "30_to_34", "35_to_44", "45_to_54", "55_and_older"]
    return {"age": dict(zip(keys, age)), "gender": {"male": male, "female": -male}}


ENTITIES: list[dict] = []


def add(name: str, type_: str, popularity: float, tags: dict, demographics: dict,
        description: str = "", akas: list[str] | None = None, **extra) -> None:
    ENTITIES.append({
        "entity_id": eid(f"{type_}:{name}"), "name": name, "type": type_,
        "popularity": popularity, "tags": tags, "demographics": demographics,
        "description": description, "akas": akas or [], **extra,
    })


# ---- Brands ----------------------------------------------------------------
add("Patagonia", "brand", 0.97, vec(sustainability=1, outdoor_lifestyle=1, hiking=.9, climbing=.8,
    trail_running=.6, travel=.5, van_life=.5, premium_pricing=.5, photography=.4, indie_folk=.4, documentary=.5),
    demo([-0.1, 0.3, 0.5, 0.4, -0.1, -0.4], 0.12), "Outdoor apparel brand known for environmental activism.")
add("Arc'teryx", "brand", 0.9, vec(outdoor_lifestyle=1, climbing=1, hiking=.8, premium_pricing=.9, design_led=.7,
    trail_running=.5, minimalism=.5, tech_early_adopters=.3),
    demo([-0.2, 0.2, 0.5, 0.5, 0.0, -0.4], 0.35), "Technical outdoor apparel from Vancouver.", ["Arcteryx"])
add("The North Face", "brand", 0.98, vec(outdoor_lifestyle=1, hiking=.8, streetwear=.5, value_pricing=.3,
    travel=.5, family=.3, climbing=.4),
    demo([0.3, 0.3, 0.2, 0.1, -0.2, -0.4], 0.25), "Outdoor apparel and equipment brand.")
add("REI", "brand", 0.93, vec(outdoor_lifestyle=1, hiking=1, cycling=.7, family=.5, sustainability=.6,
    travel=.5, van_life=.4, trail_running=.5),
    demo([-0.3, 0.1, 0.3, 0.5, 0.3, -0.1], 0.15), "Member-owned outdoor retail co-op.", ["REI Co-op"])
add("Allbirds", "brand", 0.86, vec(sustainability=1, minimalism=.8, running=.5, tech_early_adopters=.7,
    remote_work=.6, urban_living=.6, wellness=.4, design_led=.5),
    demo([-0.1, 0.4, 0.6, 0.3, -0.2, -0.5], 0.05), "Sustainable footwear brand from San Francisco.")
add("Blue Bottle Coffee", "brand", 0.9, vec(craft_coffee=1, third_wave_coffee=1, minimalism=.8, design_led=.7,
    urban_living=.7, premium_pricing=.6, remote_work=.5, foodie=.5, indie_folk=.3, photography=.3),
    demo([-0.1, 0.5, 0.6, 0.2, -0.3, -0.5], -0.05), "Specialty coffee roaster and cafe chain.", ["Blue Bottle"])
add("Stumptown Coffee Roasters", "brand", 0.8, vec(craft_coffee=1, third_wave_coffee=.9, vintage=.4, cycling=.4,
    indie_folk=.5, urban_living=.6, foodie=.4, sustainability=.4),
    demo([0.0, 0.4, 0.5, 0.2, -0.2, -0.5], 0.1), "Portland-born third-wave coffee roaster.", ["Stumptown"])
add("Glossier", "brand", 0.9, vec(clean_beauty=1, minimalism=.7, design_led=.7, urban_living=.6, pop=.5,
    wellness=.5, natural_ingredients=.5, limited_drops=.3),
    demo([0.5, 0.6, 0.2, -0.2, -0.5, -0.6], -0.6), "Direct-to-consumer beauty brand born from a blog.")
add("Aesop", "brand", 0.85, vec(natural_ingredients=1, design_led=1, luxury=.7, minimalism=.9, premium_pricing=.8,
    home_decor=.5, bookish=.4, urban_living=.6),
    demo([-0.3, 0.2, 0.6, 0.5, 0.0, -0.4], -0.1), "Australian skincare brand with design-led stores.")
add("Supreme", "brand", 0.95, vec(streetwear=1, skate_culture=1, limited_drops=1, sneaker_culture=.8, hip_hop=.7,
    urban_living=.5, luxury=.4),
    demo([0.8, 0.4, -0.1, -0.4, -0.6, -0.7], 0.55), "Skate-born streetwear label famous for drops.")
add("Nike", "brand", 1.0, vec(running=1, sneaker_culture=1, fitness_community=.8, streetwear=.6, hip_hop=.5,
    pop=.4, value_pricing=.2),
    demo([0.5, 0.3, 0.1, -0.1, -0.3, -0.5], 0.3), "Global sportswear brand.")
add("Lululemon", "brand", 0.95, vec(yoga=1, wellness=1, running=.6, fitness_community=.8, premium_pricing=.6,
    urban_living=.5, meditation=.5, minimalism=.3),
    demo([0.0, 0.4, 0.5, 0.3, -0.2, -0.5], -0.45), "Athletic apparel rooted in yoga.")
add("Peloton", "brand", 0.85, vec(fitness_community=1, tech_early_adopters=.7, wellness=.7, running=.4,
    premium_pricing=.7, remote_work=.6, family=.3),
    demo([-0.4, 0.1, 0.5, 0.6, 0.1, -0.4], -0.1), "Connected fitness brand.")
add("Oatly", "brand", 0.84, vec(plant_based=1, sustainability=.8, craft_coffee=.7, third_wave_coffee=.6, foodie=.4,
    urban_living=.5, design_led=.5, wellness=.4),
    demo([0.2, 0.5, 0.4, 0.0, -0.4, -0.6], -0.15), "Oat drink company with a playful voice.")
add("Vans", "brand", 0.93, vec(skate_culture=1, streetwear=.8, sneaker_culture=.7, psychedelic_rock=.4,
    value_pricing=.4, photography=.3),
    demo([0.7, 0.3, 0.0, -0.3, -0.5, -0.6], 0.35), "Skate shoe and apparel brand.")
add("Carhartt WIP", "brand", 0.8, vec(streetwear=1, vintage=.7, skate_culture=.5, urban_living=.6, photography=.4,
    cycling=.3, design_led=.3),
    demo([0.5, 0.5, 0.1, -0.3, -0.5, -0.6], 0.5), "Workwear reinterpreted for streetwear.", ["Carhartt Work In Progress"])
add("MUJI", "brand", 0.88, vec(minimalism=1, design_led=.9, home_decor=.8, value_pricing=.4, bookish=.4,
    urban_living=.6, remote_work=.3),
    demo([-0.1, 0.3, 0.4, 0.3, -0.1, -0.3], -0.1), "Japanese no-brand lifestyle goods.", ["Mujirushi Ryohin"])
add("Trader Joe's", "brand", 0.95, vec(foodie=.8, value_pricing=.9, family=.6, plant_based=.4, wellness=.3,
    urban_living=.3),
    demo([-0.2, 0.2, 0.3, 0.4, 0.2, -0.1], -0.2), "Quirky grocery chain.", ["Trader Joes"])
add("Erewhon", "brand", 0.7, vec(wellness=1, luxury=.8, plant_based=.7, premium_pricing=1, natural_ingredients=.6,
    yoga=.5, foodie=.6, pop=.3),
    demo([0.2, 0.6, 0.4, 0.0, -0.4, -0.6], -0.3), "Los Angeles luxury wellness grocer.")

# ---- Artists -----------------------------------------------------------------
add("Bon Iver", "artist", 0.9, vec(indie_folk=1, outdoor_lifestyle=.6, craft_coffee=.5, photography=.4,
    sustainability=.4, bookish=.4, van_life=.3, minimalism=.3),
    demo([-0.1, 0.4, 0.5, 0.3, -0.2, -0.5], 0.05), "Indie folk project of Justin Vernon.")
add("Khruangbin", "artist", 0.82, vec(psychedelic_rock=1, vintage=.7, travel=.6, design_led=.4, craft_coffee=.4,
    urban_living=.5, foodie=.4, photography=.3),
    demo([0.0, 0.5, 0.5, 0.2, -0.3, -0.6], 0.1), "Texas trio blending global psychedelic grooves.")
add("Tame Impala", "artist", 0.92, vec(psychedelic_rock=1, electronic=.5, pop=.4, streetwear=.4, vintage=.4,
    urban_living=.4),
    demo([0.3, 0.5, 0.3, -0.1, -0.5, -0.7], 0.15), "Psychedelic pop from Kevin Parker.")
add("Fleet Foxes", "artist", 0.8, vec(indie_folk=1, outdoor_lifestyle=.7, hiking=.5, bookish=.5, vintage=.4,
    photography=.3, sustainability=.3),
    demo([-0.2, 0.3, 0.5, 0.4, -0.1, -0.5], 0.15), "Seattle-born baroque folk band.")
add("Phoebe Bridgers", "artist", 0.9, vec(indie_folk=.9, pop=.4, bookish=.5, urban_living=.5, clean_beauty=.3,
    vintage=.3),
    demo([0.6, 0.5, 0.1, -0.3, -0.6, -0.7], -0.5), "Indie singer-songwriter.")
add("Tyler, the Creator", "artist", 0.95, vec(hip_hop=1, streetwear=.9, skate_culture=.7, limited_drops=.6,
    sneaker_culture=.6, design_led=.3),
    demo([0.8, 0.4, -0.1, -0.5, -0.7, -0.8], 0.45), "Rapper, producer and fashion designer.", ["Tyler the Creator"])
add("Kendrick Lamar", "artist", 0.98, vec(hip_hop=1, sneaker_culture=.6, streetwear=.5, storytelling=.5,
    urban_living=.4),
    demo([0.5, 0.5, 0.2, -0.2, -0.6, -0.7], 0.4), "Pulitzer-winning rapper.")
add("ODESZA", "artist", 0.85, vec(electronic=1, outdoor_lifestyle=.5, travel=.5, trail_running=.3,
    tech_early_adopters=.4, photography=.4, wellness=.3),
    demo([0.2, 0.6, 0.4, 0.0, -0.5, -0.7], 0.2), "Electronic duo from Seattle.")
add("Billie Eilish", "artist", 0.99, vec(pop=1, streetwear=.6, sustainability=.4, plant_based=.5, clean_beauty=.3),
    demo([0.9, 0.3, -0.2, -0.5, -0.7, -0.8], -0.35), "Pop artist.")
add("Mac DeMarco", "artist", 0.8, vec(indie_folk=.6, psychedelic_rock=.6, skate_culture=.5, vintage=.6,
    craft_coffee=.3, streetwear=.3),
    demo([0.5, 0.5, 0.1, -0.3, -0.6, -0.7], 0.3), "Slacker-rock singer-songwriter.")
add("Bonobo", "artist", 0.8, vec(electronic=1, travel=.6, design_led=.5, craft_coffee=.4, minimalism=.4,
    urban_living=.5, photography=.4),
    demo([-0.1, 0.4, 0.6, 0.3, -0.3, -0.6], 0.25), "Downtempo electronic producer.")

# ---- Podcasts ------------------------------------------------------------------
add("How I Built This", "podcast", 0.9, vec(business=1, storytelling=.8, tech_early_adopters=.6, remote_work=.5,
    craft_coffee=.3, sustainability=.3),
    demo([-0.3, 0.3, 0.6, 0.5, 0.0, -0.4], 0.2), "Founders tell their stories with Guy Raz.")
add("99% Invisible", "podcast", 0.85, vec(design_led=1, storytelling=.9, urban_living=.8, bookish=.5,
    minimalism=.5, home_decor=.4, photography=.4),
    demo([-0.2, 0.4, 0.6, 0.4, -0.1, -0.5], 0.1), "Design and architecture storytelling.")
add("Radiolab", "podcast", 0.88, vec(science=1, storytelling=.9, bookish=.6, documentary=.5, family=.3),
    demo([-0.2, 0.2, 0.4, 0.5, 0.1, -0.3], 0.1), "Curiosity-driven science storytelling.")
add("The Daily", "podcast", 0.95, vec(storytelling=.6, business=.4, urban_living=.5, remote_work=.4, family=.3),
    demo([-0.2, 0.3, 0.4, 0.4, 0.1, -0.2], -0.05), "Daily news podcast from The New York Times.")
add("Huberman Lab", "podcast", 0.92, vec(wellness=1, science=.8, fitness_community=.8, meditation=.6, running=.4,
    tech_early_adopters=.5),
    demo([0.0, 0.5, 0.5, 0.2, -0.3, -0.6], 0.5), "Neuroscience and health protocols.")
add("The Dirtbag Diaries", "podcast", 0.6, vec(outdoor_lifestyle=1, climbing=.9, hiking=.8, van_life=.8,
    storytelling=.7, sustainability=.5, photography=.4),
    demo([-0.2, 0.3, 0.5, 0.4, 0.0, -0.4], 0.4), "Adventure stories from the outdoors.")
add("Song Exploder", "podcast", 0.78, vec(storytelling=.8, indie_folk=.6, electronic=.5, psychedelic_rock=.4,
    design_led=.4, bookish=.3),
    demo([0.0, 0.5, 0.5, 0.2, -0.4, -0.6], 0.15), "Musicians take apart their songs.")
add("Armchair Expert", "podcast", 0.9, vec(storytelling=.7, pop=.5, wellness=.4, family=.4, urban_living=.3),
    demo([0.0, 0.3, 0.4, 0.3, -0.1, -0.4], -0.3), "Dax Shepard's long-form interviews.")

# ---- Movies / TV --------------------------------------------------------------------
add("Free Solo", "movie", 0.9, vec(climbing=1, outdoor_lifestyle=1, documentary=1, photography=.5, van_life=.4),
    demo([-0.1, 0.3, 0.5, 0.4, 0.0, -0.4], 0.35), "Alex Honnold climbs El Capitan without a rope.")
add("180° South", "movie", 0.6, vec(outdoor_lifestyle=1, sustainability=.9, travel=.8, documentary=1, van_life=.6,
    climbing=.5, indie_folk=.4),
    demo([-0.2, 0.3, 0.5, 0.4, 0.1, -0.3], 0.4), "A journey to Patagonia inspired by Yvon Chouinard.", ["180 Degrees South"])
add("Chef's Table", "tv_show", 0.88, vec(foodie=1, documentary=.9, design_led=.6, travel=.6, craft_coffee=.4,
    luxury=.4),
    demo([-0.2, 0.3, 0.5, 0.4, 0.0, -0.4], -0.05), "Netflix series profiling chefs.")
add("Abstract: The Art of Design", "tv_show", 0.75, vec(design_led=1, documentary=.9, minimalism=.6, urban_living=.5,
    photography=.5, home_decor=.4),
    demo([-0.1, 0.4, 0.5, 0.3, -0.2, -0.5], 0.1), "Netflix documentary series on designers.", ["Abstract"])
add("Our Planet", "tv_show", 0.9, vec(documentary=1, sustainability=.9, outdoor_lifestyle=.6, family=.6, travel=.5),
    demo([-0.2, 0.1, 0.3, 0.5, 0.3, 0.0], 0.05), "Nature documentary narrated by David Attenborough.")

# ---- Destinations ----------------------------------------------------------------------
add("Portland, Oregon", "destination", 0.85, vec(craft_coffee=1, cycling=.8, outdoor_lifestyle=.8, vintage=.6,
    bookish=.6, indie_folk=.6, sustainability=.7, foodie=.6),
    demo([-0.1, 0.4, 0.5, 0.3, -0.2, -0.5], 0.05), "", ["Portland"], lat=45.5152, lon=-122.6784)
add("Boulder, Colorado", "destination", 0.8, vec(outdoor_lifestyle=1, climbing=.9, hiking=.9, trail_running=.8,
    wellness=.6, cycling=.7, sustainability=.5),
    demo([-0.1, 0.4, 0.5, 0.3, -0.1, -0.4], 0.2), "", ["Boulder"], lat=40.015, lon=-105.2705)
add("Lisbon", "destination", 0.88, vec(travel=1, remote_work=.8, foodie=.7, design_led=.5, vintage=.5,
    craft_coffee=.5, photography=.6),
    demo([0.0, 0.5, 0.5, 0.2, -0.3, -0.6], 0.0), "", [], lat=38.7223, lon=-9.1393)
add("Kyoto", "destination", 0.9, vec(travel=1, minimalism=.8, design_led=.6, bookish=.5, meditation=.6,
    photography=.7, foodie=.6),
    demo([-0.2, 0.3, 0.5, 0.4, 0.0, -0.3], -0.05), "", [], lat=35.0116, lon=135.7681)

# ---- Places (with city) ------------------------------------------------------------------
PLACES = [
    # name, locality, neighborhood, lat, lon, tags, demo, address
    ("Devoción", "New York", "Williamsburg", 40.7155, -73.9616, vec(cafe=1, craft_coffee=1, third_wave_coffee=.9,
        design_led=.7, home_decor=.5, urban_living=.6, remote_work=.5), demo([-0.1, 0.5, 0.5, 0.2, -0.3, -0.5], 0.0),
        "69 Grand St, Brooklyn, NY"),
    ("Sey Coffee", "New York", "Bushwick", 40.7055, -73.9325, vec(cafe=1, craft_coffee=1, third_wave_coffee=1,
        minimalism=.8, design_led=.6, photography=.4), demo([0.0, 0.6, 0.5, 0.1, -0.4, -0.6], 0.1),
        "18 Grattan St, Brooklyn, NY"),
    ("Roberta's", "New York", "Bushwick", 40.7051, -73.9336, vec(restaurant=1, foodie=1, urban_living=.6,
        vintage=.4, psychedelic_rock=.3), demo([0.1, 0.5, 0.4, 0.1, -0.4, -0.6], 0.1), "261 Moore St, Brooklyn, NY"),
    ("Brooklyn Bowl", "New York", "Williamsburg", 40.7219, -73.9575, vec(music_venue=1, indie_folk=.5,
        psychedelic_rock=.5, foodie=.4, urban_living=.5), demo([0.1, 0.5, 0.4, 0.1, -0.4, -0.6], 0.2),
        "61 Wythe Ave, Brooklyn, NY"),
    ("Elsewhere", "New York", "Bushwick", 40.7088, -73.9230, vec(music_venue=1, electronic=.9, hip_hop=.4,
        streetwear=.4, urban_living=.5), demo([0.5, 0.6, 0.1, -0.4, -0.7, -0.8], 0.2), "599 Johnson Ave, Brooklyn, NY"),
    ("Public Records", "New York", "Gowanus", 40.6797, -73.9870, vec(music_venue=1, electronic=.8, plant_based=.7,
        design_led=.7, sustainability=.5, foodie=.5), demo([0.0, 0.6, 0.5, 0.1, -0.5, -0.7], 0.1),
        "233 Butler St, Brooklyn, NY"),
    ("Brooklyn Boulders", "New York", "Gowanus", 40.6773, -73.9845, vec(climbing_gym=1, climbing=1,
        fitness_community=.8, outdoor_lifestyle=.7, remote_work=.3), demo([0.0, 0.5, 0.5, 0.2, -0.4, -0.6], 0.3),
        "575 Degraw St, Brooklyn, NY"),
    ("McNally Jackson Books", "New York", "SoHo", 40.7238, -73.9966, vec(bookstore=1, bookish=1, design_led=.5,
        urban_living=.6, storytelling=.5), demo([-0.1, 0.4, 0.5, 0.3, -0.2, -0.4], -0.15), "52 Prince St, New York, NY"),
    ("The Standard, High Line", "New York", "Meatpacking", 40.7409, -74.0080, vec(hotel=1, luxury=.9,
        design_led=.7, travel=.7, pop=.4, premium_pricing=.8), demo([0.0, 0.5, 0.5, 0.2, -0.4, -0.6], 0.05),
        "848 Washington St, New York, NY"),
    ("Prospect Park", "New York", "Park Slope", 40.6602, -73.9690, vec(park=1, running=.8, cycling=.7, family=.7,
        outdoor_lifestyle=.6, hiking=.3), demo([-0.1, 0.2, 0.4, 0.5, 0.1, -0.2], 0.0), "Brooklyn, NY"),
    ("Hudson Yards", "New York", "Hudson Yards", 40.7538, -74.0022, vec(retail_street=1, luxury=.9, tech_early_adopters=.5,
        premium_pricing=.8, travel=.5), demo([0.0, 0.3, 0.4, 0.3, 0.0, -0.3], 0.05), "20 Hudson Yards, New York, NY"),
    ("Sightglass Coffee", "San Francisco", "SoMa", 37.7769, -122.4089, vec(cafe=1, craft_coffee=1, third_wave_coffee=1,
        tech_early_adopters=.6, design_led=.6, remote_work=.6), demo([-0.1, 0.5, 0.6, 0.2, -0.4, -0.6], 0.2),
        "270 7th St, San Francisco, CA"),
    ("Tartine Manufactory", "San Francisco", "Mission", 37.7614, -122.4111, vec(cafe=.8, restaurant=.8, foodie=1,
        craft_coffee=.7, design_led=.5), demo([-0.1, 0.5, 0.5, 0.2, -0.3, -0.5], -0.1), "595 Alabama St, San Francisco, CA"),
    ("Mission Cliffs", "San Francisco", "Mission", 37.7641, -122.4144, vec(climbing_gym=1, climbing=1,
        fitness_community=.8, outdoor_lifestyle=.7, tech_early_adopters=.3), demo([0.0, 0.5, 0.5, 0.2, -0.4, -0.6], 0.3),
        "2295 Harrison St, San Francisco, CA"),
    ("The Independent", "San Francisco", "NoPa", 37.7756, -122.4379, vec(music_venue=1, indie_folk=.6,
        psychedelic_rock=.6, electronic=.4), demo([0.1, 0.5, 0.4, 0.1, -0.4, -0.6], 0.2), "628 Divisadero St, San Francisco, CA"),
    ("Dolores Park", "San Francisco", "Mission", 37.7596, -122.4269, vec(park=1, urban_living=.7, skate_culture=.4,
        family=.3, running=.4), demo([0.3, 0.6, 0.3, -0.1, -0.5, -0.7], 0.0), "Dolores St, San Francisco, CA"),
    ("Green Apple Books", "San Francisco", "Inner Richmond", 37.7831, -122.4660, vec(bookstore=1, bookish=1, vintage=.5,
        storytelling=.4), demo([-0.1, 0.3, 0.5, 0.4, 0.0, -0.3], -0.05), "506 Clement St, San Francisco, CA"),
    ("Verve Coffee Roasters", "Los Angeles", "Arts District", 34.0403, -118.2329, vec(cafe=1, craft_coffee=1,
        third_wave_coffee=.9, design_led=.6, photography=.5, streetwear=.3), demo([0.0, 0.5, 0.5, 0.1, -0.4, -0.6], 0.05),
        "833 S Spring St, Los Angeles, CA"),
    ("Abbot Kinney Boulevard", "Los Angeles", "Venice", 33.9906, -118.4663, vec(retail_street=1, design_led=.7,
        wellness=.6, luxury=.5, clean_beauty=.5, skate_culture=.4), demo([0.1, 0.5, 0.5, 0.1, -0.4, -0.6], -0.1),
        "Abbot Kinney Blvd, Venice, CA"),
    ("Runyon Canyon Park", "Los Angeles", "Hollywood Hills", 34.1106, -118.3496, vec(park=1, hiking=.9, running=.7,
        wellness=.6, fitness_community=.6, outdoor_lifestyle=.6), demo([0.1, 0.5, 0.4, 0.1, -0.4, -0.6], -0.05),
        "2000 N Fuller Ave, Los Angeles, CA"),
    ("The Last Bookstore", "Los Angeles", "Downtown", 34.0479, -118.2498, vec(bookstore=1, bookish=1, vintage=.7,
        photography=.6, design_led=.4), demo([0.2, 0.5, 0.4, 0.1, -0.4, -0.6], 0.0), "453 S Spring St, Los Angeles, CA"),
    ("Erewhon Venice", "Los Angeles", "Venice", 33.9931, -118.4712, vec(grocery=1, wellness=1, luxury=.7,
        plant_based=.7, premium_pricing=.9, yoga=.5), demo([0.2, 0.6, 0.4, 0.0, -0.4, -0.6], -0.3),
        "585 Venice Blvd, Venice, CA"),
    ("The Fonda Theatre", "Los Angeles", "Hollywood", 34.1018, -118.3263, vec(music_venue=1, pop=.6, hip_hop=.5,
        psychedelic_rock=.4), demo([0.4, 0.5, 0.2, -0.2, -0.6, -0.7], 0.1), "6126 Hollywood Blvd, Los Angeles, CA"),
    ("Coava Coffee Roasters", "Portland", "Buckman", 45.5162, -122.6557, vec(cafe=1, craft_coffee=1,
        third_wave_coffee=1, minimalism=.7, cycling=.4, design_led=.5), demo([0.0, 0.5, 0.5, 0.2, -0.4, -0.6], 0.15),
        "1300 SE Grand Ave, Portland, OR"),
    ("Powell's City of Books", "Portland", "Pearl District", 45.5231, -122.6813, vec(bookstore=1, bookish=1,
        vintage=.5, storytelling=.5, family=.3), demo([-0.1, 0.3, 0.4, 0.4, 0.1, -0.2], -0.05),
        "1005 W Burnside St, Portland, OR", ["Powell's Books"]),
    ("Forest Park", "Portland", "Northwest", 45.5549, -122.7549, vec(park=1, hiking=1, trail_running=.8,
        outdoor_lifestyle=.9, family=.4), demo([-0.1, 0.3, 0.5, 0.4, 0.0, -0.3], 0.15), "Portland, OR"),
    ("Mississippi Studios", "Portland", "Boise", 45.5516, -122.6753, vec(music_venue=1, indie_folk=.8,
        psychedelic_rock=.5, craft_coffee=.3), demo([0.0, 0.5, 0.5, 0.2, -0.4, -0.6], 0.15), "3939 N Mississippi Ave, Portland, OR"),
]
for row in PLACES:
    name, locality, hood, lat, lon, tags, dm, address = row[:8]
    akas = row[8] if len(row) > 8 else []
    add(name, "place", 0.6 + 0.3 * (int(hashlib.md5(name.encode()).hexdigest(), 16) % 7) / 7, tags, dm, "", akas, locality=locality,
        neighborhood=hood, lat=lat, lon=lon, address=address)

# ---- Localities (cities with neighbourhood taste profiles) ------------------------------------
LOCALITIES = {
    "new york": {"name": "New York", "akas": ["nyc", "new york city", "manhattan", "brooklyn", "the big apple"],
                 "lat": 40.7128, "lon": -74.0060, "neighborhoods": [
        ("Williamsburg", 40.7140, -73.9610, 0.95, vec(craft_coffee=1, design_led=.8, indie_folk=.6, vintage=.6, urban_living=.8, streetwear=.4)),
        ("Bushwick", 40.6958, -73.9171, 0.85, vec(electronic=.9, streetwear=.7, skate_culture=.5, foodie=.6, craft_coffee=.6, photography=.6)),
        ("Gowanus", 40.6742, -73.9890, 0.6, vec(climbing=.8, plant_based=.6, design_led=.6, sustainability=.6, cycling=.5)),
        ("Park Slope", 40.6710, -73.9814, 0.8, vec(family=1, bookish=.7, running=.6, sustainability=.6, foodie=.5, outdoor_lifestyle=.4)),
        ("SoHo", 40.7233, -74.0030, 1.0, vec(luxury=.9, design_led=.8, streetwear=.7, clean_beauty=.6, premium_pricing=.8, limited_drops=.6)),
        ("Lower East Side", 40.7150, -73.9843, 0.9, vec(streetwear=.8, hip_hop=.6, vintage=.6, music_venue=.5, foodie=.6, skate_culture=.5)),
        ("Upper West Side", 40.7870, -73.9754, 0.8, vec(family=.9, bookish=.7, running=.5, wellness=.5, premium_pricing=.5)),
        ("Chelsea", 40.7465, -74.0014, 0.9, vec(design_led=.8, luxury=.7, fitness_community=.6, photography=.6, wellness=.5)),
        ("Astoria", 40.7644, -73.9235, 0.7, vec(foodie=.9, family=.5, value_pricing=.5, urban_living=.6, craft_coffee=.4)),
    ]},
    "san francisco": {"name": "San Francisco", "akas": ["sf", "san fran", "bay area"], "lat": 37.7749, "lon": -122.4194,
                      "neighborhoods": [
        ("Mission", 37.7599, -122.4148, 0.95, vec(foodie=.9, craft_coffee=.8, skate_culture=.5, vintage=.6, climbing=.5, urban_living=.8)),
        ("SoMa", 37.7785, -122.4056, 0.8, vec(tech_early_adopters=1, remote_work=.8, craft_coffee=.7, fitness_community=.5)),
        ("Hayes Valley", 37.7759, -122.4245, 0.85, vec(design_led=.9, clean_beauty=.6, luxury=.5, craft_coffee=.7, minimalism=.6)),
        ("Marina", 37.8037, -122.4368, 0.8, vec(fitness_community=.9, running=.8, wellness=.7, yoga=.6, premium_pricing=.6)),
        ("Outer Sunset", 37.7530, -122.4940, 0.6, vec(outdoor_lifestyle=.9, van_life=.5, craft_coffee=.6, sustainability=.6, photography=.5)),
        ("Inner Richmond", 37.7800, -122.4650, 0.6, vec(bookish=.7, foodie=.7, family=.6, hiking=.5)),
    ]},
    "los angeles": {"name": "Los Angeles", "akas": ["la", "l.a.", "los angeles city"], "lat": 34.0522, "lon": -118.2437,
                    "neighborhoods": [
        ("Venice", 33.9850, -118.4695, 0.9, vec(wellness=.9, skate_culture=.7, design_led=.6, luxury=.5, plant_based=.6, yoga=.6)),
        ("Silver Lake", 34.0869, -118.2702, 0.85, vec(indie_folk=.8, vintage=.7, craft_coffee=.8, psychedelic_rock=.6, bookish=.5)),
        ("Arts District", 34.0403, -118.2329, 0.8, vec(design_led=.8, streetwear=.7, craft_coffee=.7, photography=.6, foodie=.6)),
        ("Hollywood", 34.0928, -118.3287, 1.0, vec(pop=.9, music_venue=.7, hip_hop=.5, travel=.6)),
        ("Santa Monica", 34.0195, -118.4912, 0.9, vec(running=.8, wellness=.7, family=.6, cycling=.6, outdoor_lifestyle=.6)),
        ("Highland Park", 34.1115, -118.1920, 0.7, vec(vintage=.8, craft_coffee=.6, indie_folk=.6, foodie=.6, cycling=.4)),
        ("Pasadena", 34.1478, -118.1445, 0.7, vec(family=.9, hiking=.6, bookish=.5, science=.5)),
    ]},
    "portland": {"name": "Portland", "akas": ["portland or", "portland oregon", "pdx"], "lat": 45.5152, "lon": -122.6784,
                 "neighborhoods": [
        ("Pearl District", 45.5285, -122.6830, 0.9, vec(design_led=.8, bookish=.7, luxury=.5, craft_coffee=.7)),
        ("Alberta Arts", 45.5590, -122.6480, 0.75, vec(vintage=.8, indie_folk=.7, foodie=.7, craft_coffee=.7, photography=.5)),
        ("Hawthorne", 45.5120, -122.6360, 0.8, vec(vintage=.9, bookish=.6, sustainability=.7, cycling=.7, plant_based=.5)),
        ("St. Johns", 45.5897, -122.7536, 0.5, vec(outdoor_lifestyle=.8, family=.6, hiking=.6, van_life=.5)),
        ("Mississippi", 45.5500, -122.6750, 0.75, vec(indie_folk=.8, music_venue=.7, craft_coffee=.7, cycling=.5)),
    ]},
    "london": {"name": "London", "akas": ["london uk", "greater london"], "lat": 51.5074, "lon": -0.1278,
               "neighborhoods": [
        ("Shoreditch", 51.5260, -0.0770, 0.95, vec(streetwear=.8, design_led=.8, craft_coffee=.8, electronic=.6, vintage=.6)),
        ("Hackney", 51.5450, -0.0550, 0.8, vec(vintage=.8, craft_coffee=.7, plant_based=.6, cycling=.7, indie_folk=.5)),
        ("Peckham", 51.4740, -0.0690, 0.7, vec(music_venue=.8, hip_hop=.6, electronic=.6, foodie=.6, photography=.5)),
        ("Notting Hill", 51.5160, -0.2050, 0.85, vec(luxury=.8, clean_beauty=.6, family=.5, premium_pricing=.7, bookish=.5)),
        ("Richmond", 51.4613, -0.3037, 0.6, vec(family=.9, running=.7, outdoor_lifestyle=.7, cycling=.6)),
    ]},
}

OUT = {
    "tags": TAGS,
    "entities": ENTITIES,
    "localities": {
        key: {
            "entity_id": eid(f"locality:{val['name']}"),
            "name": val["name"], "akas": val["akas"], "lat": val["lat"], "lon": val["lon"],
            "neighborhoods": [
                {"name": n[0], "lat": n[1], "lon": n[2], "popularity": n[3], "tags": n[4]} for n in val["neighborhoods"]
            ],
        }
        for key, val in LOCALITIES.items()
    },
}

if __name__ == "__main__":
    out_path = Path(__file__).resolve().parent.parent / "app" / "qloo" / "fixtures.json"
    out_path.write_text(json.dumps(OUT, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"wrote {out_path} ({len(ENTITIES)} entities, {len(TAGS)} tags, {len(LOCALITIES)} localities)")
