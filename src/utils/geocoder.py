"""Venue geocoding utilities with static lookup table"""
import logging
from typing import Optional, Tuple

logger = logging.getLogger(__name__)

# Static lookup table for known Cambridge/Somerville venues
# Coordinates sourced from OpenStreetMap/Google Maps
VENUE_COORDINATES = {
    # Libraries (Cambridge Public Library)
    "main library": (42.3656, -71.1039),
    "central square branch": (42.3651, -71.1032),
    "valente branch": (42.3731, -71.0862),
    "boudreau branch": (42.3872, -71.1324),
    "o'neill branch": (42.3957, -71.1344),
    "o'connell branch": (42.3680, -71.0796),
    "collins branch": (42.3851, -71.1432),

    # Theaters & Performance Venues
    "brattle theatre": (42.3735, -71.1209),
    "central square theater": (42.3648, -71.1030),
    "loeb drama center": (42.3748, -71.1190),
    "sanders theatre": (42.3763, -71.1149),
    "american repertory theater": (42.3748, -71.1190),
    "a.r.t.": (42.3748, -71.1190),
    "oberon": (42.3656, -71.1028),
    "somerville theatre": (42.3960, -71.1221),
    "the rockwell": (42.3960, -71.0992),
    "arts at the armory": (42.3986, -71.1090),
    "the armory": (42.3986, -71.1090),
    "multicultural arts center": (42.3697, -71.0816),
    "first church in cambridge": (42.3737, -71.1207),
    "first parish in cambridge": (42.3737, -71.1207),
    "theatre at first": (42.3956, -71.1221),
    "theatre@first": (42.3956, -71.1221),

    # Music Venues
    "the sinclair": (42.3728, -71.1190),
    "club passim": (42.3730, -71.1203),
    "the burren": (42.3960, -71.0992),
    "toad": (42.3654, -71.1032),
    "the cantab lounge": (42.3650, -71.1027),
    "cantab lounge": (42.3650, -71.1027),
    "lizard lounge": (42.3899, -71.1271),
    "the plough and stars": (42.3650, -71.1025),
    "plough and stars": (42.3650, -71.1025),
    "regattabar": (42.3936, -71.1324),
    "scullers jazz club": (42.3543, -71.1320),
    "the lily pad": (42.3953, -71.1225),
    "lilypad": (42.3953, -71.1225),
    "lily pad": (42.3953, -71.1225),
    "the jungle": (42.3960, -71.0992),
    "the mad monkfish": (42.3649, -71.1028),
    "mad monkfish": (42.3649, -71.1028),
    "mccarthys": (42.3728, -71.1195),
    "mccarthy's": (42.3728, -71.1195),

    # Breweries
    "aeronaut brewing": (42.3902, -71.0997),
    "aeronaut allston": (42.3538, -71.1320),
    "lamplighter brewing": (42.3636, -71.1016),
    "portico brewing": (42.3899, -71.0997),

    # Comedy
    "the comedy studio": (42.3728, -71.1195),
    "comedy studio": (42.3728, -71.1195),

    # Bookstores
    "harvard book store": (42.3725, -71.1168),
    "porter square books": (42.3884, -71.1192),
    "grolier poetry book shop": (42.3727, -71.1184),

    # Museums & Cultural
    "harvard art museums": (42.3742, -71.1143),
    "mit museum": (42.3621, -71.0977),
    "cambridge historical society": (42.3777, -71.1268),

    # Universities
    "mit": (42.3601, -71.0942),
    "harvard university": (42.3770, -71.1167),
    "harvard": (42.3770, -71.1167),
    "lesley university": (42.3870, -71.1195),

    # Community Centers
    "cambridge community center": (42.3648, -71.1049),
    "cambridge senior center": (42.3648, -71.1034),
    "cambridge ymca": (42.3655, -71.1037),
    "democracy center": (42.3730, -71.1201),
    "the foundry": (42.3680, -71.0767),
    "foundry": (42.3680, -71.0767),

    # Dance
    "the dance complex": (42.3649, -71.1035),
    "dance complex": (42.3649, -71.1035),
    "green street studios": (42.3651, -71.1026),
    "boston swing central": (42.3649, -71.1035),

    # Churches & Religious
    "first congregational church": (42.3729, -71.1201),
    "harvard memorial church": (42.3749, -71.1167),
    "st. paul church": (42.3748, -71.1201),

    # Outdoor/Parks
    "cambridge common": (42.3760, -71.1217),
    "harvard square": (42.3732, -71.1202),
    "central square": (42.3651, -71.1034),
    "porter square": (42.3884, -71.1191),
    "inman square": (42.3739, -71.0999),
    "davis square": (42.3967, -71.1225),
    "union square": (42.3794, -71.0952),

    # Other Popular Venues
    "middlesex lounge": (42.3650, -71.1029),
    "zuzu": (42.3651, -71.1030),
    "black sheep": (42.3728, -71.1195),
    "hong kong restaurant": (42.3728, -71.1197),
    "charlie's kitchen": (42.3732, -71.1202),
    "beat hotel": (42.3732, -71.1198),
    "beat brew hall": (42.3729, -71.1197),
    "bow market": (42.3795, -71.0950),

    # New England Conservatory (Boston but nearby)
    "new england conservatory": (42.3410, -71.0859),
    "jordan hall": (42.3410, -71.0859),

    # Additional venues from database
    "the middle east": (42.3649, -71.1030),
    "middle east": (42.3649, -71.1030),
    "middle east upstairs": (42.3649, -71.1030),
    "middle east downstairs": (42.3649, -71.1030),
    "middle east corner": (42.3649, -71.1030),
    "middle east restaurant": (42.3649, -71.1030),
    "lamplighter cx": (42.3689, -71.0666),
    "sally o'brien's": (42.3795, -71.0950),
    "sally o'briens": (42.3795, -71.0950),
    "the druid": (42.3739, -71.0999),
    "druid": (42.3739, -71.0999),
    "redbones": (42.3960, -71.1204),
    "the sea hag": (42.3736, -71.0987),
    "sea hag": (42.3736, -71.0987),
    "phoenix landing": (42.3648, -71.1032),
    "lou's": (42.3960, -71.0999),
    "crystal ballroom": (42.3960, -71.1221),
    "satellite": (42.3728, -71.1195),
    "an sibin": (42.3795, -71.0950),
    "state park": (42.3648, -71.1029),
    "warehouse xi": (42.3960, -71.0992),
    "warehouse 11": (42.3960, -71.0992),
    "once somerville": (42.3794, -71.0950),
    "once": (42.3794, -71.0950),
    "boynton yards": (42.3795, -71.0950),
    "remnant brewing": (42.3795, -71.0950),
    "backbar": (42.3795, -71.0950),
    "brass union": (42.3795, -71.0950),
    "highland kitchen": (42.3986, -71.1090),
    "thunder road": (42.3960, -71.1221),
    "cambridge public library": (42.3656, -71.1039),
    "cambridge main library": (42.3656, -71.1039),
    "the word": (42.3648, -71.1030),
    "atwood's tavern": (42.3652, -71.1032),
    "atwoods tavern": (42.3652, -71.1032),
    "ryles": (42.3739, -71.0999),
    "thelonious monkfish": (42.3649, -71.1028),
    "beat brasserie": (42.3730, -71.1200),
    "park street": (42.3793, -71.0950),
    "vera's": (42.3648, -71.1032),
    "veras": (42.3648, -71.1032),
    "samba bar": (42.3648, -71.1029),
    "good life": (42.3579, -71.0584),
    "the good life": (42.3579, -71.0584),
    "trident booksellers": (42.3499, -71.0855),
    "brookline booksmith": (42.3418, -71.1214),
    "coolidge corner theatre": (42.3418, -71.1214),
    "somerville armory": (42.3986, -71.1090),
    "elks lodge": (42.3960, -71.0992),
    "masonic hall": (42.3732, -71.1200),
    "odd fellows hall": (42.3732, -71.1200),
    "veterans memorial hall": (42.3960, -71.1221),
    "polish american club": (42.3737, -71.0987),
    "vfw": (42.3960, -71.0992),
    "elks": (42.3960, -71.0992),
    "crystal ballroom at somerville theatre": (42.3960, -71.1221),
}

# Address-based fallback coordinates (for venues without names)
ADDRESS_COORDINATES = {
    "449 broadway": (42.3656, -71.1039),  # Main Library
    "45 pearl st": (42.3651, -71.1032),   # Central Square Branch
    "40 brattle street": (42.3735, -71.1209),  # Brattle Theatre
    "191 highland ave": (42.3986, -71.1090),  # Arts at the Armory
    "41 second street": (42.3697, -71.0816),  # Multicultural Arts Center
    "450 massachusetts avenue": (42.3648, -71.1030),  # Central Square Theater
    "64 brattle street": (42.3748, -71.1190),  # Loeb Drama Center
    "32 quincy street": (42.3742, -71.1143),  # Harvard Art Museums
    "1353 cambridge st": (42.3953, -71.1225),  # The Lily Pad
    "5 john f. kennedy st": (42.3728, -71.1195),  # Comedy Studio
    "472 massachusetts ave": (42.3649, -71.1030),  # Middle East
    "480 massachusetts ave": (42.3649, -71.1030),  # Middle East
    "110 n first st": (42.3689, -71.0666),  # Lamplighter CX
}


def get_venue_coordinates(
    venue_name: Optional[str] = None,
    street_address: Optional[str] = None
) -> Tuple[Optional[float], Optional[float]]:
    """
    Get coordinates for a venue by name or address.

    Returns:
        Tuple of (latitude, longitude) or (None, None) if not found
    """
    # Try venue name first
    if venue_name:
        key = venue_name.lower().strip()
        if key in VENUE_COORDINATES:
            coords = VENUE_COORDINATES[key]
            logger.debug(f"Found coordinates for venue '{venue_name}': {coords}")
            return coords

        # Try partial matching for common variations
        for known_venue, coords in VENUE_COORDINATES.items():
            if known_venue in key or key in known_venue:
                logger.debug(f"Partial match for '{venue_name}' -> '{known_venue}': {coords}")
                return coords

    # Try address
    if street_address:
        addr_key = street_address.lower().strip()
        # Remove common suffixes for matching
        addr_key = addr_key.replace(", cambridge", "").replace(", ma", "").replace(" ma ", " ")
        addr_key = addr_key.split(",")[0].strip()  # Take first part before comma

        for known_addr, coords in ADDRESS_COORDINATES.items():
            if known_addr in addr_key or addr_key.startswith(known_addr):
                logger.debug(f"Found coordinates for address '{street_address}': {coords}")
                return coords

    return None, None


def add_coordinates_to_event(event_dict: dict) -> dict:
    """
    Add coordinates to an event dictionary if not already present.

    Args:
        event_dict: Event as a dictionary

    Returns:
        Event dictionary with coordinates added (if found)
    """
    # Skip if already has coordinates
    if event_dict.get('latitude') and event_dict.get('longitude'):
        return event_dict

    lat, lng = get_venue_coordinates(
        venue_name=event_dict.get('venue_name'),
        street_address=event_dict.get('street_address')
    )

    if lat and lng:
        event_dict['latitude'] = lat
        event_dict['longitude'] = lng

    return event_dict
