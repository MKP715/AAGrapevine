"""Where is a magazine writer from? — classify the byline location of a Grapevine / La Viña story.

    classify_location("Grand Prairie, Texas")  → scope "neta65"  (Dallas / Ellis / Tarrant counties)
    classify_location("Houston, Texas")        → scope "texas"   (Harris County — NOT Area 65)
    classify_location("Houston County, Texas") → scope "neta65"  (the county is in Area 65)
    classify_location("Cheyenne, Wyoming")     → scope "other"
    classify_location(None) / ("")             → scope "unknown"

Scopes (docs/DATA_SCHEMA.md → extra.geo):
    neta65   the place is in one of the Area 65 counties (config/site.yml spotlight.neta65_counties).
             A place that spans several counties counts when ANY of them is an Area 65 county.
    texas    somewhere else in Texas (or "Texas" with no usable city).
    other    anywhere else (another state, province or country, or a place name we cannot tie to Texas).
    unknown  no location given.

How a byline is read ("City, State[, Country]" — both magazines print "By: Jake B. | Cheyenne, Wyoming"):
  0. Tidying: an aside in parentheses is dropped ("Tyler, Texas (District 42)") unless it names a state
     or country — "Denton (Texas)" reads as "Denton, Texas". A part that is only a ZIP code is dropped.
     Words around the place are removed: "near" / "outside (of)" / "somewhere in" / "a small town in" /
     "rural" / "cerca de" / "(a las) afueras de" / "algún lugar de" … in front, "area" / "metro" /
     "vicinity" / "y alrededores" … behind ("near Tyler, Texas", "Tyler area, Texas" → Tyler).
     A real place name that starts or ends with such a word is never cut, and this reading is only
     kept when it lands in Texas (a place elsewhere keeps its label as written: "near Oslo, Norway").
  1. A trailing country ("USA", "EE. UU.", "Estados Unidos", "México" …) is set aside — also when it
     follows the state without a comma ("Dallas, Texas USA", "Dallas Texas USA").
  2. The last part must name the state: "Texas", "TX", "Tx.", "Tex.", "Tejas" (any case, dots and
     ZIP codes ignored; "Dallas TX" without a comma works too; "Texarkana, TX-AR" counts as Texas).
     Other US states (English or Spanish names — "Nueva Jersey"), Canadian provinces and Mexican states
     (names and the usual abbreviations "N.L.", "Jal.", "Chih.", "Edo. Méx." …) make the place "other".
     When the last part is not a state but an earlier part is Texas, what follows is ignored
     ("Tyler, Texas, District 42"). A region is only read as a region when it stands alone: "West
     Texas" / "West TX" / "W. Texas" is the region, "West, Texas" (and "West, TX") is the town of West
     in McLennan County.
  3. The city is looked up in data/geo/texas_places.json — every Texas city, town, village and census-
     designated place (U.S. Census 2020 place-by-county table, public domain) → its county/counties.
     "Houston County" / "Condado de Houston" is read as a county. The lookup also understands
     "N." / "S." / "E." / "W." ("N. Richland Hills"), spaces written or left out ("De Soto" = DeSoto,
     "Mc Kinney" = McKinney), "North Dallas" / "downtown Fort Worth" / "norte de Dallas", hyphenated
     or joined pairs ("Hurst-Euless-Bedford", "Sherman-Denison", "Dallas/Fort Worth", "Tyler y
     Longview"), well-known Dallas and Fort Worth neighborhoods ("Oak Cliff", "Deep Ellum",
     "Arlington Heights" … NEIGHBORHOODS below) and a few Spanish names of Texas towns ("Palestina" =
     Palestine). A place name that is itself in the gazetteer always wins ("Panhandle, Texas" is the
     town of Panhandle, not the region). Unknown Texas cities stay "texas".
  4. No state at all: only the small curated lists below count as Texas, because a bare city name is
     ambiguous ("Paris" is in France, "Arlington" in Virginia, "Athens" in Greece …). Everything else
     without a state is "other" — US writers almost always add the state.

BARE CITY LISTS (a city printed WITHOUT a state; decided 2026-09 — edit with care):
  NETA65_BARE — large Area 65 cities whose name has no well-known namesake elsewhere in the English- or
      Spanish-speaking world: Dallas, Fort Worth, Tyler, Texarkana, Abilene, Waco, Plano, Wichita Falls,
      McKinney, Frisco, Garland, Irving, Grand Prairie, Richardson, Sherman, Nacogdoches, Corsicana,
      Waxahachie, Rockwall, Rowlett. ("Grapevine" is never on it — it is the magazine's name.)
      Deliberately LEFT OUT (a bare name is too likely to mean somewhere else): Arlington (Virginia),
      Paris (France), Athens (Greece / Georgia), Jacksonville (Florida), Palestine (the region),
      Marshall (many), Denison (Iowa; Denison University), Longview (Washington), Denton (England),
      Mansfield (England / Ohio), Mesquite (Nevada), Carrollton (Georgia), Lewisville (N. Carolina),
      Henderson (Nevada), Greenville (S. Carolina), Gainesville (Florida), DeSoto (Mississippi /
      Missouri), Cedar Hill (Missouri), Weatherford (Oklahoma), Burleson (also a Texas county outside
      Area 65), Bedford / Ennis / Lancaster (England / Ireland / Pennsylvania).
  TEXAS_BARE — unambiguous big Texas cities outside Area 65: Houston, El Paso, Corpus Christi, Lubbock,
      Amarillo, McAllen, Round Rock, Killeen, Texas City. Left out: San Antonio (Chile and many
      Latin-American towns — La Viña readers), Austin (Minnesota), Laredo (Spain), Midland (Michigan),
      Odessa (Ukraine), Beaumont (California), Brownsville (Tennessee).
  Region names count too: "North Texas", "Northeast Texas", "DFW" / "Metroplex" → neta65;
  "East/West/South/Central Texas", "Hill Country", "Panhandle", "Rio Grande Valley" → texas.

`lang` (the byline's language, "en"/"es") is optional. Both magazines print the same "City, State" form
and every rule above already knows the English and Spanish names, so it only matters for the four
abbreviations that a Mexican state shares with a US state or a Canadian province (MX_SHARED_ABBR):
"N.L." / "NL" (Nuevo León — Newfoundland and Labrador), "B.C." / "BC" (Baja California — British
Columbia), "Mich." (Michoacán — Michigan) and "Col." (Colima — Colorado). A country in the byline decides
first ("…, B.C., México"), then a short list of the best-known cities of each ("Tijuana, B.C." /
"Vancouver, B.C."), then the language: in a Spanish (La Viña) byline the Mexican state, otherwise the US
state / Canadian province. The scope is "other" either way — only the printed place changes.
"""
from __future__ import annotations

import json
import re
import unicodedata
from functools import lru_cache
from typing import Any

from .common import ROOT, clean_text, get_logger, load_config

log = get_logger("geo")

GAZETTEER_PATH = ROOT / "data" / "geo" / "texas_places.json"
SCOPES = ("neta65", "texas", "other", "unknown")          # also the spotlight sort order

# --------------------------------------------------------------------------- normalization
# "St." / "Saint", "Ft." / "Fort", "Mt." / "Mount" are written both ways in bylines.
_TOKEN_ALIASES = {"st": "saint", "ft": "fort", "mt": "mount"}
_TYPE_SUFFIX = re.compile(r"\s+(?:city|town|village|cdp)$")


def fold(s: Any) -> str:
    """Lower case, accents removed ("San José" → "san jose")."""
    t = unicodedata.normalize("NFKD", str(s or ""))
    return "".join(c for c in t if not unicodedata.combining(c)).lower()


def normalize_place(name: Any, strip_type: bool = False) -> str:
    """The gazetteer key of a place name: lower case, no accents, no apostrophes ("Morgan's Point" →
    "morgans point"), other punctuation → spaces, "st"/"ft"/"mt" → "saint"/"fort"/"mount".
    strip_type=True also drops ONE trailing " city" / " town" / " village" / " CDP" — the Census
    type word ("Texas City city" → "texas city"). The builder uses it; lookups try without first,
    so a real name that ends in "City" ("Texas City") is still found."""
    s = fold(name).replace("’", "'").replace("'", "")
    s = re.sub(r"[^a-z0-9]+", " ", s).strip()
    if strip_type:
        s = _TYPE_SUFFIX.sub("", s)
    return " ".join(_TOKEN_ALIASES.get(t, t) for t in s.split())


def _key(s: Any) -> str:
    """Compact key for state / country names: letters only ("EE. UU." → "eeuu", "N.J." → "nj")."""
    return re.sub(r"[^a-z]", "", fold(s))


# --------------------------------------------------------------------------- reference tables
# US states: code → (English, Spanish). Spanish only differs where a Spanish exonym is standard.
US_STATES: dict[str, tuple[str, str]] = {
    "AL": ("Alabama", "Alabama"), "AK": ("Alaska", "Alaska"), "AZ": ("Arizona", "Arizona"),
    "AR": ("Arkansas", "Arkansas"), "CA": ("California", "California"), "CO": ("Colorado", "Colorado"),
    "CT": ("Connecticut", "Connecticut"), "DE": ("Delaware", "Delaware"), "FL": ("Florida", "Florida"),
    "GA": ("Georgia", "Georgia"), "HI": ("Hawaii", "Hawái"), "ID": ("Idaho", "Idaho"),
    "IL": ("Illinois", "Illinois"), "IN": ("Indiana", "Indiana"), "IA": ("Iowa", "Iowa"),
    "KS": ("Kansas", "Kansas"), "KY": ("Kentucky", "Kentucky"), "LA": ("Louisiana", "Luisiana"),
    "ME": ("Maine", "Maine"), "MD": ("Maryland", "Maryland"), "MA": ("Massachusetts", "Massachusetts"),
    "MI": ("Michigan", "Míchigan"), "MN": ("Minnesota", "Minnesota"), "MS": ("Mississippi", "Misisipi"),
    "MO": ("Missouri", "Misuri"), "MT": ("Montana", "Montana"), "NE": ("Nebraska", "Nebraska"),
    "NV": ("Nevada", "Nevada"), "NH": ("New Hampshire", "Nuevo Hampshire"), "NJ": ("New Jersey", "Nueva Jersey"),
    "NM": ("New Mexico", "Nuevo México"), "NY": ("New York", "Nueva York"),
    "NC": ("North Carolina", "Carolina del Norte"), "ND": ("North Dakota", "Dakota del Norte"),
    "OH": ("Ohio", "Ohio"), "OK": ("Oklahoma", "Oklahoma"), "OR": ("Oregon", "Oregón"),
    "PA": ("Pennsylvania", "Pensilvania"), "RI": ("Rhode Island", "Rhode Island"),
    "SC": ("South Carolina", "Carolina del Sur"), "SD": ("South Dakota", "Dakota del Sur"),
    "TN": ("Tennessee", "Tennessee"), "TX": ("Texas", "Texas"), "UT": ("Utah", "Utah"),
    "VT": ("Vermont", "Vermont"), "VA": ("Virginia", "Virginia"), "WA": ("Washington", "Washington"),
    "WV": ("West Virginia", "Virginia Occidental"), "WI": ("Wisconsin", "Wisconsin"),
    "WY": ("Wyoming", "Wyoming"), "DC": ("District of Columbia", "Distrito de Columbia"),
}
CA_PROVINCES: dict[str, tuple[str, str]] = {
    "ON": ("Ontario", "Ontario"), "QC": ("Quebec", "Quebec"), "BC": ("British Columbia", "Columbia Británica"),
    "AB": ("Alberta", "Alberta"), "MB": ("Manitoba", "Manitoba"), "SK": ("Saskatchewan", "Saskatchewan"),
    "NS": ("Nova Scotia", "Nueva Escocia"), "NB": ("New Brunswick", "Nuevo Brunswick"),
    "NL": ("Newfoundland and Labrador", "Terranova y Labrador"),
    "PE": ("Prince Edward Island", "Isla del Príncipe Eduardo"), "YT": ("Yukon", "Yukón"),
    "NT": ("Northwest Territories", "Territorios del Noroeste"), "NU": ("Nunavut", "Nunavut"),
}
MX_STATES = ("Aguascalientes", "Baja California", "Baja California Sur", "Campeche", "Chiapas", "Chihuahua",
             "Ciudad de México", "Coahuila", "Colima", "Durango", "Estado de México", "Guanajuato", "Guerrero",
             "Hidalgo", "Jalisco", "Michoacán", "Morelos", "Nayarit", "Nuevo León", "Oaxaca", "Puebla",
             "Querétaro", "Quintana Roo", "San Luis Potosí", "Sinaloa", "Sonora", "Tabasco", "Tamaulipas",
             "Tlaxcala", "Veracruz", "Yucatán", "Zacatecas")
# Countries: key → (ISO code, English, Spanish). Keys are _key() forms of the names people write.
_COUNTRY_ROWS = [
    ("US", "United States", "Estados Unidos", ["usa", "us", "unitedstates", "unitedstatesofamerica", "eeuu",
                                               "estadosunidos", "eua", "eu", "estadosunidosdeamerica"]),
    ("MX", "Mexico", "México", ["mexico", "mex"]), ("CA", "Canada", "Canadá", ["canada"]),
    ("ES", "Spain", "España", ["spain", "espana"]), ("FR", "France", "Francia", ["france", "francia"]),
    ("GB", "United Kingdom", "Reino Unido", ["uk", "unitedkingdom", "reinounido", "greatbritain", "granbretana"]),
    ("GB", "England", "Inglaterra", ["england", "inglaterra"]), ("GB", "Scotland", "Escocia", ["scotland", "escocia"]),
    ("GB", "Wales", "Gales", ["wales", "gales"]),
    ("GB", "Northern Ireland", "Irlanda del Norte", ["northernireland", "irlandadelnorte"]),
    ("IE", "Ireland", "Irlanda", ["ireland", "irlanda"]), ("NO", "Norway", "Noruega", ["norway", "noruega"]),
    ("AU", "Australia", "Australia", ["australia"]),
    ("NZ", "New Zealand", "Nueva Zelanda", ["newzealand", "nuevazelanda"]),
    ("HN", "Honduras", "Honduras", ["honduras"]), ("GT", "Guatemala", "Guatemala", ["guatemala"]),
    ("SV", "El Salvador", "El Salvador", ["elsalvador"]), ("NI", "Nicaragua", "Nicaragua", ["nicaragua"]),
    ("CR", "Costa Rica", "Costa Rica", ["costarica"]), ("PA", "Panama", "Panamá", ["panama"]),
    ("CO", "Colombia", "Colombia", ["colombia"]), ("VE", "Venezuela", "Venezuela", ["venezuela"]),
    ("EC", "Ecuador", "Ecuador", ["ecuador"]), ("PE", "Peru", "Perú", ["peru"]),
    ("BO", "Bolivia", "Bolivia", ["bolivia"]), ("CL", "Chile", "Chile", ["chile"]),
    ("AR", "Argentina", "Argentina", ["argentina"]), ("UY", "Uruguay", "Uruguay", ["uruguay"]),
    ("PY", "Paraguay", "Paraguay", ["paraguay"]), ("CU", "Cuba", "Cuba", ["cuba"]),
    ("DO", "Dominican Republic", "República Dominicana", ["dominicanrepublic", "republicadominicana"]),
    ("PR", "Puerto Rico", "Puerto Rico", ["puertorico", "pr"]),
    ("DE", "Germany", "Alemania", ["germany", "alemania"]), ("IT", "Italy", "Italia", ["italy", "italia"]),
    ("PT", "Portugal", "Portugal", ["portugal"]),
    ("NL", "Netherlands", "Países Bajos", ["netherlands", "paisesbajos", "holland", "holanda"]),
    ("BE", "Belgium", "Bélgica", ["belgium", "belgica"]), ("CH", "Switzerland", "Suiza", ["switzerland", "suiza"]),
    ("SE", "Sweden", "Suecia", ["sweden", "suecia"]), ("DK", "Denmark", "Dinamarca", ["denmark", "dinamarca"]),
    ("FI", "Finland", "Finlandia", ["finland", "finlandia"]), ("PL", "Poland", "Polonia", ["poland", "polonia"]),
    ("BR", "Brazil", "Brasil", ["brazil", "brasil"]), ("JP", "Japan", "Japón", ["japan", "japon"]),
    ("ZA", "South Africa", "Sudáfrica", ["southafrica", "sudafrica"]), ("IN", "India", "India", ["india"]),
    ("PH", "Philippines", "Filipinas", ["philippines", "filipinas"]), ("IL", "Israel", "Israel", ["israel"]),
]
COUNTRIES: dict[str, tuple[str, str, str]] = {k: (code, en, es) for code, en, es, keys in _COUNTRY_ROWS for k in keys}

TEXAS_KEYS = {"texas", "tx", "tex", "tejas"}
_STATE_BY_KEY: dict[str, tuple[str, str, str, str]] = {}      # key → (code, English, Spanish, country)
for _code, (_en, _es) in US_STATES.items():
    for _k in {_code.lower(), _key(_en), _key(_es)}:
        _STATE_BY_KEY[_k] = (_code, _en, _es, "US")
for _k in TEXAS_KEYS:
    _STATE_BY_KEY[_k] = ("TX", "Texas", "Texas", "US")
for _code, (_en, _es) in CA_PROVINCES.items():
    for _k in {_code.lower(), _key(_en), _key(_es)}:
        _STATE_BY_KEY.setdefault(_k, (_code, _en, _es, "CA"))
_STATE_BY_KEY["quebec"] = ("QC", "Quebec", "Quebec", "CA")
# AP-style abbreviations the Grapevine prints ("Memphis, Tenn.", "Fresno, Calif.", "Victoria, B.C.")
for _code, _abbrs in (("AL", "ala"), ("AZ", "ariz"), ("AR", "ark"), ("CA", "calif cal"), ("CO", "colo"),
                      ("CT", "conn"), ("DE", "del"), ("FL", "fla"), ("IL", "ill ills"), ("IN", "ind"),
                      ("KS", "kan kans"), ("MA", "mass"), ("MI", "mich"), ("MN", "minn"), ("MS", "miss"),
                      ("MT", "mont"), ("NE", "neb nebr"), ("NV", "nev"), ("OK", "okla"), ("OR", "ore oreg"),
                      ("PA", "penn penna"), ("TN", "tenn"), ("VT", "vt"), ("VA", "va"), ("WA", "wash"),
                      ("WV", "wva"), ("WI", "wis wisc"), ("WY", "wyo")):
    for _k in _abbrs.split():
        _STATE_BY_KEY.setdefault(_k, (_code, *US_STATES[_code], "US"))
for _code, _abbrs in (("ON", "ont"), ("QC", "que"), ("AB", "alta"), ("SK", "sask"), ("MB", "man")):
    for _k in _abbrs.split():
        _STATE_BY_KEY.setdefault(_k, (_code, *CA_PROVINCES[_code], "CA"))
for _name in MX_STATES:
    _STATE_BY_KEY.setdefault(_key(_name), (_name, _name, _name, "MX"))
for _alias, _name in (("cdmx", "Ciudad de México"), ("df", "Ciudad de México"), ("edomex", "Estado de México"),
                      ("michoacan", "Michoacán"), ("nuevoleon", "Nuevo León")):
    _STATE_BY_KEY[_alias] = (_name, _name, _name, "MX")
# The usual Mexican state abbreviations ("Guadalajara, Jal.", "Ciudad Juárez, Chih."). None of these keys
# is a US / Canadian one (setdefault keeps it that way); the three that ARE shared follow below.
for _name, _abbrs in (("Aguascalientes", "ags"), ("Baja California Sur", "bcs"), ("Campeche", "camp"),
                      ("Chiapas", "chis"), ("Chihuahua", "chih"), ("Coahuila", "coah"), ("Colima", "col"),
                      ("Durango", "dgo"), ("Guanajuato", "gto"), ("Guerrero", "gro"), ("Hidalgo", "hgo"),
                      ("Jalisco", "jal"), ("Estado de México", "edodemex edodemexico edomexico"),
                      ("Ciudad de México", "distritofederal"), ("Morelos", "mor"), ("Nayarit", "nay"),
                      ("Oaxaca", "oax"), ("Puebla", "pue"), ("Querétaro", "qro"),
                      ("Quintana Roo", "qroo"), ("San Luis Potosí", "slp"), ("Sinaloa", "sin"), ("Sonora", "son"),
                      ("Tabasco", "tab"), ("Tamaulipas", "tamps tamp"), ("Tlaxcala", "tlax"), ("Veracruz", "ver"),
                      ("Yucatán", "yuc"), ("Zacatecas", "zac")):
    for _k in _abbrs.split():
        _STATE_BY_KEY.setdefault(_k, (_name, _name, _name, "MX"))
# A lone abbreviation is not a place ("Jal." alone); those that are also everyday words ("Son", "Sin",
# "Camp", "Tab" …) only count as their own comma-separated part ("Hermosillo, Son."), never glued to the
# city ("Boot Camp") — see _split_trailing_state and _classify_parts step 3.
_MX_ABBR_KEYS = frozenset({"ags", "bcs", "camp", "chis", "chih", "coah", "col", "dgo", "gto", "gro", "hgo", "jal",
                           "mor", "nay", "oax", "pue", "qro", "qroo", "slp", "sin", "son", "tab", "tamps", "tamp",
                           "tlax", "ver", "yuc", "zac"})
_MX_ABBR_WORDS = frozenset({"camp", "col", "mor", "nay", "sin", "son", "tab", "ver"})
# "Col." is Colima in Mexico but also how many people shorten Colorado ("Denver, Col.").
_STATE_BY_KEY["col"] = ("CO", *US_STATES["CO"], "US")
# Abbreviations a Mexican state shares with a US state / Canadian province (module docstring, `lang`):
# key → (Mexican state, {best-known Mexican cities}, {best-known US / Canadian cities}).
MX_SHARED_ABBR: dict[str, tuple[str, frozenset, frozenset]] = {
    "col": ("Colima",
            frozenset({"colima", "manzanillo", "tecoman", "villa de alvarez", "armeria", "comala"}),
            frozenset({"denver", "colorado springs", "aurora", "boulder", "fort collins", "pueblo", "grand junction",
                       "greeley", "longmont", "loveland", "lakewood", "littleton", "arvada", "westminster",
                       "thornton", "durango", "canon city", "englewood", "golden", "castle rock"})),
    "nl": ("Nuevo León",
           frozenset({"monterrey", "san nicolas de los garza", "san nicolas", "apodaca", "san pedro garza garcia",
                      "santa catarina", "general escobedo", "escobedo", "linares", "montemorelos",
                      "cadereyta jimenez", "cadereyta", "sabinas hidalgo", "allende"}),
           frozenset({"saint johns", "corner brook", "mount pearl", "gander", "labrador city", "grand falls windsor",
                      "happy valley goose bay", "conception bay south", "paradise"})),
    "bc": ("Baja California",
           frozenset({"tijuana", "mexicali", "ensenada", "tecate", "rosarito", "playas de rosarito", "san quintin",
                      "san felipe"}),
           frozenset({"vancouver", "victoria", "surrey", "burnaby", "richmond", "kelowna", "kamloops", "nanaimo",
                      "abbotsford", "coquitlam", "prince george", "langley", "delta", "chilliwack", "north vancouver",
                      "west vancouver", "new westminster", "maple ridge", "port coquitlam", "penticton", "vernon",
                      "courtenay", "whistler", "squamish", "agassiz", "mission", "salmon arm", "cranbrook", "nelson",
                      "terrace", "fort saint john", "dawson creek", "powell river", "duncan", "parksville",
                      "white rock", "port alberni", "campbell river", "quesnel", "williams lake", "prince rupert",
                      "castlegar", "revelstoke", "sechelt", "comox", "ladner", "tsawwassen", "sidney", "sooke"})),
    "mich": ("Michoacán",
             frozenset({"morelia", "uruapan", "zamora", "lazaro cardenas", "zitacuaro", "apatzingan", "patzcuaro",
                        "la piedad", "sahuayo", "jacona", "hidalgo", "ciudad hidalgo", "tacambaro"}),
             frozenset({"detroit", "grand rapids", "lansing", "east lansing", "ann arbor", "flint", "kalamazoo",
                        "dearborn", "saginaw", "warren", "sterling heights", "livonia", "pontiac", "traverse city",
                        "battle creek", "muskegon", "holland", "jackson", "bay city", "southfield", "troy",
                        "ypsilanti", "port huron", "marquette", "midland", "wyoming", "novi", "farmington hills"})),
}
# Two-letter US / Canadian codes collide with Mexico's "Mexico" etc. only as whole parts — fine.

# Texas regions people write instead of a city → (scope, English label, Spanish label).
TEXAS_REGIONS: dict[str, tuple[str, str, str]] = {}
for _keys, _scope, _en, _es in (
        (("north texas", "norte de texas", "norte de tejas", "north tx", "n texas", "n tx"), "neta65", "North Texas",
         "Norte de Texas"),
        (("northeast texas", "north east texas", "ne texas", "noreste de texas", "nordeste de texas",
          "noreste de tejas", "northeast tx", "ne tx"), "neta65", "Northeast Texas", "Noreste de Texas"),
        (("north central texas", "centro norte de texas", "norte central de texas"), "neta65",
         "North Central Texas", "Centro-norte de Texas"),
        (("dfw", "dallas fort worth", "dallas fort worth metroplex", "metroplex", "the metroplex",
          "dallas y fort worth", "dallas and fort worth"), "neta65", "Dallas–Fort Worth", "Dallas–Fort Worth"),
        (("mid cities", "the mid cities", "midcities"), "neta65", "Mid-Cities", "Mid-Cities"),
        (("east texas", "este de texas", "este de tejas", "e texas", "east tx", "e tx"), "texas", "East Texas",
         "Este de Texas"),
        (("west texas", "oeste de texas", "oeste de tejas", "w texas", "west tx", "w tx"), "texas", "West Texas",
         "Oeste de Texas"),
        (("south texas", "sur de texas", "sur de tejas", "s texas", "south tx", "s tx"), "texas", "South Texas",
         "Sur de Texas"),
        (("central texas", "centro de texas", "centro de tejas", "central tx"), "texas", "Central Texas",
         "Centro de Texas"),
        (("southeast texas", "sureste de texas", "se texas", "southeast tx", "se tx"), "texas", "Southeast Texas",
         "Sureste de Texas"),
        (("texas panhandle", "the panhandle", "panhandle"), "texas", "Texas Panhandle", "Panhandle de Texas"),
        (("hill country", "texas hill country"), "texas", "Texas Hill Country", "Hill Country de Texas"),
        (("rio grande valley", "the valley", "valle del rio grande"), "texas", "Rio Grande Valley",
         "Valle del Río Grande")):
    for _k in _keys:
        TEXAS_REGIONS[normalize_place(_k)] = (_scope, _en, _es)
# Regions that are really one metro area: shown like a city ("Dallas–Fort Worth, Texas"), with counties.
_CITYLIKE_REGIONS = {"Dallas–Fort Worth": ["Dallas", "Tarrant"], "Mid-Cities": ["Tarrant", "Dallas"]}

# Names that are not Census places but mean one, read ONLY once the state is known to be Texas (or for
# a curated bare city): well-known Dallas / Fort Worth neighborhoods (the city's own county decides the
# scope) and Spanish names of Texas towns (English label → the Census name). A name that is itself in
# the gazetteer is never looked up here (Highland Park, River Oaks, Westover Hills … are real towns).
# alias → (gazetteer key(s), English display name or None = keep the writer's spelling)
NEIGHBORHOODS: dict[str, tuple[tuple[str, ...], str | None]] = {}
for _target, _names in (
        # only names that mean this one place in Texas ("Pleasant Grove", "Lakewood", "Northside",
        # "Tanglewood" … exist in several Texas cities and are left out)
        ("dallas", "Oak Cliff, North Oak Cliff, Deep Ellum, Lake Highlands, Oak Lawn, Preston Hollow, Bishop Arts,"
                   " Bishop Arts District, White Rock Lake, Old East Dallas, Uptown Dallas, Vickery Meadow,"
                   " Casa Linda, Kessler Park, Red Bird, The Cedars, Dallas Design District, Knox Henderson,"
                   " Lower Greenville, M Streets, Bluffview, Love Field, Far North Dallas, Kiest Park"),
        ("fort worth", "Arlington Heights, Near Southside, Fairmount, Ryan Place, TCU, Wedgwood, Polytechnic,"
                       " Stockyards, Fort Worth Stockyards, Handley, Sundance Square, West 7th, Eastchase, Ridglea,"
                       " Ridglea Hills, Berkeley Place, Mistletoe Heights, Diamond Hill")):
    for _n in _names.split(","):
        NEIGHBORHOODS[normalize_place(_n)] = ((_target,), None)
NEIGHBORHOODS[normalize_place("HEB")] = (("hurst", "euless", "bedford"), None)          # Hurst-Euless-Bedford
for _es, _en in (("Palestina", "Palestine"), ("San Agustín", "San Augustine"), ("Nuevo Londres", "New London"),
                 ("Nueva Londres", "New London"), ("Nueva Braunfels", "New Braunfels"),
                 ("Nuevo Braunfels", "New Braunfels"), ("Corpus Cristi", "Corpus Christi"),
                 ("Puerto Arturo", "Port Arthur"), ("Puerto Lavaca", "Port Lavaca"), ("Puerto Isabel", "Port Isabel")):
    NEIGHBORHOODS[normalize_place(_es)] = ((normalize_place(_en),), _en)

# A city printed without any state — see the module docstring before changing these.
NETA65_BARE = {normalize_place(c) for c in (
    "Dallas", "Fort Worth", "Tyler", "Texarkana", "Abilene", "Waco", "Plano", "Wichita Falls", "McKinney",
    "Frisco", "Garland", "Irving", "Grand Prairie", "Richardson", "Sherman", "Nacogdoches", "Corsicana",
    "Waxahachie", "Rockwall", "Rowlett")}
TEXAS_BARE = {normalize_place(c) for c in (
    "Houston", "El Paso", "Corpus Christi", "Lubbock", "Amarillo", "McAllen", "Round Rock", "Killeen",
    "Texas City")}

# The county a multi-county city mainly lies in (for display only — the scope uses ALL counties).
# Single-county places need no entry; a multi-county place without an entry gets county = None.
PRINCIPAL_COUNTY = {normalize_place(k): v for k, v in {
    "Dallas": "Dallas", "Fort Worth": "Tarrant", "Grand Prairie": "Dallas", "Carrollton": "Dallas",
    "Garland": "Dallas", "Irving": "Dallas", "Mesquite": "Dallas", "Richardson": "Dallas", "Rowlett": "Dallas",
    "Sachse": "Dallas", "Seagoville": "Dallas", "Cedar Hill": "Dallas", "Coppell": "Dallas",
    "Glenn Heights": "Dallas", "Plano": "Collin", "Frisco": "Collin", "Wylie": "Collin", "Celina": "Collin",
    "Prosper": "Collin", "Josephine": "Collin", "Royse City": "Rockwall", "Heath": "Rockwall",
    "Lewisville": "Denton", "Flower Mound": "Denton", "Roanoke": "Denton", "Trophy Club": "Denton",
    "Sanger": "Denton", "Pilot Point": "Denton", "Grapevine": "Tarrant", "Southlake": "Tarrant",
    "Mansfield": "Tarrant", "Crowley": "Tarrant", "Azle": "Tarrant", "Haslet": "Tarrant", "Westlake": "Tarrant",
    "Burleson": "Johnson", "Venus": "Johnson", "Abilene": "Taylor", "Longview": "Gregg", "Kilgore": "Gregg",
    "Gladewater": "Gregg", "Mineral Wells": "Palo Pinto", "Springtown": "Parker", "Reno": "Parker",
    "Mabank": "Kaufman", "Seven Points": "Henderson", "Winnsboro": "Wood", "Hughes Springs": "Cass",
    "Bullard": "Smith", "Troup": "Smith", "Overton": "Rusk", "Tatum": "Rusk", "Frankston": "Anderson",
    "Stamford": "Jones", "Hamlin": "Jones", "Whitewright": "Grayson", "Van Alstyne": "Grayson",
    "Trenton": "Fannin", "McGregor": "McLennan", "Mart": "McLennan", "Valley Mills": "Bosque",
    "Hico": "Hamilton", "Ferris": "Ellis", "Ovilla": "Ellis", "Windthorst": "Archer",
    # big Texas cities outside Area 65
    "Houston": "Harris", "San Antonio": "Bexar", "Austin": "Travis", "Round Rock": "Williamson",
    "Cedar Park": "Williamson", "Leander": "Williamson", "Pflugerville": "Travis", "Sugar Land": "Fort Bend",
    "Missouri City": "Fort Bend", "Pearland": "Brazoria", "Katy": "Harris", "League City": "Galveston",
    "Friendswood": "Galveston", "Baytown": "Harris", "New Braunfels": "Comal", "Schertz": "Guadalupe",
    "Amarillo": "Potter", "Corpus Christi": "Nueces", "Killeen": "Bell", "College Station": "Brazos",
    "Texas City": "Galveston", "El Paso": "El Paso", "Lubbock": "Lubbock", "Waller": "Waller",
    "The Woodlands": "Montgomery",
}.items()}


@lru_cache(maxsize=1)
def gazetteer() -> dict[str, list[str]]:
    """{normalized place name: [county, …]} — data/geo/texas_places.json (see scripts/dev/build_texas_gazetteer.py)."""
    try:
        with open(GAZETTEER_PATH, encoding="utf-8") as f:
            data = json.load(f)
        if isinstance(data, dict):
            return {str(k): [str(c) for c in v] for k, v in data.items() if isinstance(v, list)}
    except FileNotFoundError:
        log.error("%s is missing — run python -m scripts.dev.build_texas_gazetteer; every Texas city "
                  "now counts as 'texas' (not Area 65)", GAZETTEER_PATH)
    except Exception as e:
        log.error("%s is unreadable (%s) — Texas cities cannot be matched to counties", GAZETTEER_PATH, e)
    return {}


@lru_cache(maxsize=1)
def texas_counties() -> dict[str, str]:
    """{folded county name: County Name} for all 254 Texas counties (from the gazetteer values)."""
    out: dict[str, str] = {}
    for counties in gazetteer().values():
        for c in counties:
            out.setdefault(normalize_place(c), c)
    return out


def neta65_counties(cfg: dict | None = None) -> set[str]:
    """Folded names of the Area 65 counties from config/site.yml spotlight.neta65_counties."""
    cfg = cfg if cfg is not None else load_config()
    names = ((cfg.get("spotlight") or {}).get("neta65_counties") or []) if isinstance(cfg, dict) else []
    return {normalize_place(re.sub(r"(?i)\s+county$", "", str(n))) for n in names if str(n).strip()}


@lru_cache(maxsize=1)
def _neta65_cached() -> frozenset:
    try:
        return frozenset(neta65_counties())
    except Exception as e:   # a broken config must not stop the build
        log.error("could not read spotlight.neta65_counties from config/site.yml: %s", e)
        return frozenset()


@lru_cache(maxsize=1)
def _compact_index() -> dict[str, str]:
    """{gazetteer key without spaces: key} — "de soto" finds "desoto", "mc kinney" finds "mckinney".
    A spaceless form shared by two places ("lake view" / "lakeview") is left out."""
    seen: dict[str, str | None] = {}
    for k in gazetteer():
        c = k.replace(" ", "")
        seen[c] = None if c in seen and seen[c] != k else k
    return {c: k for c, k in seen.items() if k}


def reset_caches() -> None:
    """Forget the loaded gazetteer / county list (tests, or after rebuilding the gazetteer)."""
    gazetteer.cache_clear()
    texas_counties.cache_clear()
    _compact_index.cache_clear()
    _neta65_cached.cache_clear()


# --------------------------------------------------------------------------- helpers
def _pretty_city(s: str) -> str:
    """Tidy a city as the writer gave it: "Ft. Worth" → "Fort Worth", "DALLAS" → "Dallas"."""
    t = clean_text(s).strip(" .,-")
    t = re.sub(r"(?i)^ft\.?\s+", "Fort ", t)
    t = re.sub(r"(?i)^mt\.?\s+", "Mount ", t)
    if t and (t.islower() or t.isupper()) and len(t) > 3:
        t = " ".join(w[:1].upper() + w[1:].lower() if w.isalpha() else w for w in t.split())
    return t


# Words around a place (module docstring, step 0). Every alternative ends with the space before the place.
_LEAD_QUAL = re.compile(
    r"(?i)^(?:"
    r"(?:just|right|somewhere|someplace|living|lives|based|located|residing)\s+"
    r"(?=(?:in|near|outside|around|close|north|south|east|west|en|cerca)\b)"               # "somewhere in …"
    r"|(?:(?:an?|one)\s+(?:(?:small|little|tiny|quiet|rural)\s+)?|(?:small|little|tiny|quiet|rural)\s+)"
    r"(?:town|village|community|farm|ranch|city|suburb|place)\s+"                           # "a small town in …"
    r"(?:in|near|outside(?:\s+of)?|of|(?:north|south|east|west)\s+of)\s+"
    r"|(?:in|near|nearby|outside(?:\s+of)?|around|close\s+to|(?:north|south|east|west|northeast|northwest|southeast"
    r"|southwest)\s+of|(?:the\s+)?outskirts\s+of)\s+"
    r"|(?:rural|suburban)\s+"
    r"|(?:en\s+)?(?:alg[uú]n\s+lugar|alguna\s+parte)\s+(?:de|en|cerca\s+de)\s+"
    r"|un\s+(?:peque[nñ]o\s+)?(?:pueblo|pueblito|lugar)\s+(?:peque[nñ]o\s+)?(?:de|en|cerca\s+de)\s+"
    r"|(?:(?:en|a)\s+)?(?:las\s+)?afueras\s+de\s+"
    r"|(?:muy\s+)?cerca\s+de\s+"
    r"|(?:el\s+|la\s+)?(?:[aá]rea|zona|regi[oó]n)\s+(?:metropolitana\s+|rural\s+|conurbada\s+)?de\s+"
    r")")
_TRAIL_QUAL = re.compile(      # not the "Bay Area" / "Tri-State Area" — those are names
    r"(?i)(?:(?<!bay)(?<!state)[\s-]+(?:metro(?:politan)?[\s-]+)?area|[\s-]+metro(?:plex)?|\s+region|\s+vicinity"
    r"|\s+suburbs?|\s+environs|\s+outskirts|\s+(?:and|&|y)\s+(?:the\s+)?(?:surrounding\s+(?:areas?|towns|communities)"
    r"|vicinity|surroundings|(?:sus\s+)?alrededores))$")


def _known_place(t: str) -> bool:
    k = normalize_place(t)
    return k in TEXAS_REGIONS or k in NEIGHBORHOODS or k in gazetteer()


def _strip_qualifiers(s: str) -> str:
    """"near Tyler" → "Tyler", "Tyler area" → "Tyler", "somewhere in East Texas" → "East Texas",
    "cerca de Tyler" → "Tyler". A known Texas place, region or neighborhood is never cut."""
    t = clean_text(s).strip(" .,-")
    for _ in range(4):
        if not t or _known_place(t):
            break
        u = _TRAIL_QUAL.sub("", _LEAD_QUAL.sub("", t, count=1), count=1).strip(" .,-")
        if not u or u == t:
            break
        t = u
    return t


# "N. Richland Hills" → "north richland hills" (tried only when the name as written is not a place)
_DIRECTION_TOKENS = {"n": "north", "s": "south", "e": "east", "w": "west",
                     "ne": "northeast", "nw": "northwest", "se": "southeast", "sw": "southwest"}
# "North Dallas" / "downtown Fort Worth" / "norte de Dallas" / "City of Addison" → the city (matched on a
# normalized key, only for a place already known to be in Texas)
_PLACE_PREFIX = re.compile(
    r"^(?:(?:far|deep)\s+)?(?:north|south|east|west|northeast|northwest|southeast|southwest|north east|north west"
    r"|south east|south west|n|s|e|w|ne|nw|se|sw|downtown|uptown|midtown|central|greater|metro|inner|outer"
    r"|(?:the\s+)?(?:city|town|village)\s+of|(?:la\s+)?ciudad\s+de"
    r"|(?:(?:el|la|zona)\s+)?(?:norte|sur|este|oeste|noreste|noroeste|sureste|suroeste|centro)\s+de"
    r"(?:\s+la\s+ciudad\s+de)?)\s+")
_PIECES = re.compile(r"\s*(?:/|&|\+|\band\b|\by\b|[-–—])\s*")


def _lookup(city: str, depth: int = 0) -> tuple[str, list[str], str | None] | None:
    """(gazetteer key, counties, English name or None) of a Texas place, or None. Tries, in order: the
    name as written; without a type word ("Rowlett city"); NEIGHBORHOODS ("Oak Cliff" → Dallas,
    "Palestina" → Palestine); "N." as "North" ("N. Richland Hills"); without spaces ("De Soto" → DeSoto,
    "Mc Kinney" → McKinney); without a direction / "downtown" / "norte de" in front ("North Dallas");
    then each piece of "Dallas/Fort Worth", "Tyler & Longview", "Tyler y Longview", "Sherman-Denison"."""
    gz = gazetteer()
    for strip in (False, True):
        k = normalize_place(city, strip_type=strip)
        if k in gz:
            return k, list(gz[k]), None
    k = normalize_place(city)
    if not k or depth > 3:
        return None
    alias = NEIGHBORHOODS.get(k)
    if alias:
        targets, english = alias
        found: list[str] = []
        for t in targets:
            found += [c for c in gz.get(t, []) if c not in found]
        if found:
            return " / ".join(targets), found, english
    words = k.split()
    k2 = " ".join(_DIRECTION_TOKENS.get(w, w) for w in words)
    if len(words) > 1 and k2 != k and k2 in gz:        # a lone "W." is not the town of West
        return k2, list(gz[k2]), None
    compact = _compact_index().get(k.replace(" ", ""))
    if compact:
        return compact, list(gz[compact]), None
    m = _PLACE_PREFIX.match(k)
    if m and k[m.end():]:
        hit = _lookup(k[m.end():], depth + 1)
        if hit:
            return hit[0], hit[1], None
    pieces = [p for p in _PIECES.split(city) if p.strip()]
    if len(pieces) > 1:
        found = []
        keys: list[str] = []
        for p in pieces:
            hit = _lookup(p, depth + 1)
            if hit:
                keys.append(hit[0])
                found += [c for c in hit[1] if c not in found]
        return (" / ".join(keys), found, None) if found else None
    return None


_COUNTY_RE = re.compile(r"(?i)^(?:condado\s+de\s+(?P<a>.+)|(?P<b>.+?)\s+(?:county|co\.?|condado))$")


def _county_of(part: str) -> str | None:
    """'Houston County' / 'Condado de Houston' / 'Smith Co.' → 'Houston' if it is a Texas county."""
    m = _COUNTY_RE.match(clean_text(part).strip(" ."))
    if not m:
        return None
    return texas_counties().get(normalize_place(m.group("a") or m.group("b")))


def _result(scope: str, *, city=None, county=None, counties=None, state=None, country=None,
            label_en=None, label_es=None) -> dict:
    return {"scope": scope, "city": city or None, "county": county or None, "counties": list(counties or []),
            "state": state or None, "country": country or None,
            "label_en": label_en or None, "label_es": label_es or None}


UNKNOWN = _result("unknown")
_NO_LOCATION = {"", "n a", "na", "none", "unknown", "desconocido", "anonymous", "anonimo", "anonima", "no location"}
# Byline "places" that are really notes — reprints print e.g. "Excerpt. Original title: “Editorial: On
# the 9th Tradition,” Grapevine, August 1948" where the place would be.
_NOT_A_PLACE = re.compile(r"(?i)\bexcerpt|original title|t[ií]tulo original|\breprint|\breimpres|\bextracto\b|"
                          r"\b(?:18|19|20)\d\d\b|[“”\"«»:]")


def _region_result(region: tuple[str, str, str]) -> dict:
    scope, en, es = region
    if en in _CITYLIKE_REGIONS:        # "DFW" / "Dallas/Fort Worth, Texas" / "Mid-Cities"
        return _result(scope, city=en, counties=_CITYLIKE_REGIONS[en], state="TX", country="US",
                       label_en=f"{en}, Texas", label_es=f"{es}, Texas")
    return _result(scope, state="TX", country="US", label_en=en, label_es=es)


def _texas_result(city_part: str | None, country: str | None) -> dict:
    """A place known to be in Texas: find its counties and decide neta65 vs texas."""
    neta = _neta65_cached()
    city_part = _strip_qualifiers(city_part) if city_part else city_part
    if not city_part:
        return _result("texas", state="TX", country="US", label_en="Texas", label_es="Texas")
    whole_key = normalize_place(city_part)
    region = TEXAS_REGIONS.get(whole_key)
    if region and whole_key not in gazetteer():   # "Panhandle, Texas" is the town of Panhandle
        return _region_result(region)
    county = _county_of(city_part)
    if county:
        scope = "neta65" if normalize_place(county) in neta else "texas"
        return _result(scope, county=county, counties=[county], state="TX", country="US",
                       label_en=f"{county} County, Texas", label_es=f"Condado de {county}, Texas")
    # "Oak Cliff, Dallas" → try the part next to the state first, then the others
    parts = [p for p in (_strip_qualifiers(x) for x in city_part.split(",")) if p]
    for cand in reversed(parts):
        hit = _lookup(cand)
        if hit:
            key, counties, english = hit
            city = _pretty_city(cand)
            if normalize_place(cand) != key and normalize_place(cand, strip_type=True) == key:
                city = re.sub(r"(?i)\s+(?:city|town|village|cdp)$", "", city)     # "Rowlett city"
            scope = "neta65" if any(normalize_place(c) in neta for c in counties) else "texas"
            if len(counties) == 1:
                principal = counties[0]
            else:
                principal = PRINCIPAL_COUNTY.get(key)
                principal = principal if principal in counties else None
            return _result(scope, city=city, county=principal, counties=counties, state="TX", country="US",
                           label_en=f"{english or city}, Texas", label_es=f"{city}, Texas")
    city = _pretty_city(parts[-1] if parts else city_part)
    return _result("texas", city=city, state="TX", country="US", label_en=f"{city}, Texas",
                   label_es=f"{city}, Texas")


def _state_row(k: str, city: str = "", lang: str | None = None, country: tuple | None = None) -> tuple | None:
    """The state row of a key; settles the abbreviations Mexico shares with the US / Canada ("N.L.",
    "B.C.", "Mich.", "Col.") — the byline's country, then the city, then its language (module docstring)."""
    row = _STATE_BY_KEY.get(k)
    shared = MX_SHARED_ABBR.get(k)
    if not row or not shared:
        return row
    name, mx_cities, other_cities = shared
    mx = (name, name, name, "MX")
    if country and country[0] == "MX":
        return mx
    if country and country[0] == row[3]:
        return row
    c = normalize_place(city)
    if c in mx_cities:
        return mx
    if c in other_cities:
        return row
    return mx if str(lang or "").lower().startswith("es") else row


def _split_trailing_state(part: str, lang: str | None = None,
                          country: tuple | None = None) -> tuple[str, tuple | None]:
    """'Dallas TX' / 'Tyler Texas' / 'New York New York' (no comma) → (city, state row); else (part, None).
    A two-letter code only counts in capitals ("Portland OR"), except Texas ("Dallas tx"); a Mexican
    abbreviation that is also a word never does ("Boot Camp" is not in Campeche, "Hermosillo Son" stays)."""
    words = part.split()
    for n in range(min(4, len(words) - 1), 0, -1):       # longest state name first; keep a city
        cand = " ".join(words[-n:]).rstrip(".")
        k = _key(cand)
        if k not in _STATE_BY_KEY or k in _MX_ABBR_WORDS:
            continue
        if len(k) == 2 and k not in TEXAS_KEYS and not cand.replace(".", "").isupper():
            continue
        rest = " ".join(words[:-n])
        return rest, _state_row(k, rest, lang, country)
    return part, None


def _split_trailing_country(part: str) -> tuple[str, tuple | None]:
    """'Texas USA' / 'Dallas Texas EE. UU.' (no comma before the country) → ('Texas', country row) — only
    when what is left is, or ends with, a state: "New Mexico", "Nuevo México" and "Madrid España" stay."""
    if _key(part) in _STATE_BY_KEY:
        return part, None
    words = part.split()
    for n in range(min(4, len(words) - 1), 0, -1):
        k = _key(" ".join(words[-n:]))
        if k not in COUNTRIES:
            continue
        rest = " ".join(words[:-n])
        if _key(rest) in _STATE_BY_KEY or _split_trailing_state(rest)[1]:
            return rest, COUNTRIES[k]
    return part, None


def _dual_state_is_texas(part: str) -> bool:
    """'TX-AR' / 'Texas/Oklahoma' (a border town such as Texarkana) → True when one of the two is Texas."""
    m = re.fullmatch(r"([A-Za-z.]+)\s*[-/&–]\s*([A-Za-z.]+)", part.strip())
    if not m:
        return False
    a, b = _key(m.group(1)), _key(m.group(2))
    return a in _STATE_BY_KEY and b in _STATE_BY_KEY and bool({a, b} & TEXAS_KEYS)


def _paren(m: re.Match) -> str:
    """"(Texas)" / "(TX, USA)" → ", Texas, " (a state or country in parentheses is part of the place);
    any other aside — "(District 42)", "(Area 65)" — is dropped."""
    inner = m.group(1)
    bits = [b for b in re.split(r"\s*,\s*", inner) if b.strip(" .")]
    if bits and all(_key(b) in _STATE_BY_KEY or _key(b) in COUNTRIES for b in bits):
        return f", {inner}, "
    return " "


# --------------------------------------------------------------------------- main API
def classify_location(text: Any, lang: str | None = None) -> dict:
    """Byline location → {scope, city, county, counties, state, country, label_en, label_es}.
    `lang` ("en"/"es", optional) only settles "N.L." / "B.C." / "Mich." / "Col." (module docstring).
    Never raises; anything unreadable is "unknown" (no text) or "other"."""
    try:
        return _classify(text, lang)
    except Exception as e:     # one odd byline must never break the build
        log.warning("could not classify location %r: %s", text, e)
        t = clean_text(text)
        return _result("other", label_en=t, label_es=t) if t else dict(UNKNOWN)


def _classify(text: Any, lang: str | None = None) -> dict:
    raw = clean_text(re.sub(r"\(([^)]*)\)", _paren, str(text or "")))    # drop "(Area 65)"; "(Texas)" stays
    raw = raw.strip(" .,;|-–—")
    if normalize_place(raw) in _NO_LOCATION or _NOT_A_PLACE.search(raw) or len(raw) > 80:
        return dict(UNKNOWN)
    if re.search(r"(?i)\b(?:[aá]rea|neta)\s*65\b", str(text)):
        return _result("neta65", state="TX", country="US", label_en="Northeast Texas (Area 65)",
                       label_es="Noreste de Texas (Área 65)")
    parts = [p.strip(" .") for p in re.split(r"\s*[,;|]\s*|\s+[-–—]\s+", raw)]
    parts = [re.sub(r"(?:^|\s+)\d{5}(?:-\d{4})?$", "", p) for p in parts if p.strip(" .")]   # ZIP codes
    parts = [p for p in parts if p]
    if not parts:
        return dict(UNKNOWN)
    # "near Tyler, Texas" / "Tyler area, Texas" / "somewhere in East Texas": read without the extra words
    # first — but keep that reading only when it lands in Texas, so a place elsewhere keeps its label as
    # written ("near Oslo, Norway", "San Francisco Bay Area, California").
    plain = [q for q in (_strip_qualifiers(p) for p in parts) if q]
    if plain and plain != parts:
        r = _classify_parts(plain, lang)
        if r["scope"] in ("neta65", "texas"):
            return r
    return _classify_parts(parts, lang)


def _classify_parts(parts: list[str], lang: str | None) -> dict:
    """Steps 1–4 of the module docstring on the byline split at its commas."""
    parts = list(parts)
    # 1. trailing country ("…, USA"; also "…, Texas USA" without a comma)
    country = None
    while True:
        if len(parts) > 1 and _key(parts[-1]) in COUNTRIES:
            country = COUNTRIES[_key(parts.pop())]
            continue
        rest, ctry = _split_trailing_country(parts[-1])
        if ctry and rest:
            parts[-1], country = rest, ctry
            continue
        break
    if len(parts) == 1 and _key(parts[0]) in COUNTRIES and not _STATE_BY_KEY.get(_key(parts[0])):
        code, en, es = COUNTRIES[_key(parts[0])]
        return _result("other", country=code, label_en=en, label_es=es)
    if (country and country[0] != "US" and not _STATE_BY_KEY.get(_key(parts[-1]))
            and not _split_trailing_state(parts[-1], lang, country)[1]):
        city = _pretty_city(", ".join(parts))
        return _result("other", city=city if len(parts) == 1 else None, country=country[0],
                       label_en=f"{city}, {country[1]}", label_es=f"{city}, {country[2]}")

    # 2. a Texas region instead of a city ("North Texas", "Este de Texas", "DFW", "Dallas, Fort Worth") —
    #    but "West, Texas" is the town of West: a Texas place followed by the state is never a region
    whole = ", ".join(parts)
    region = TEXAS_REGIONS.get(normalize_place(whole))
    if region and not (len(parts) > 1 and _key(parts[-1]) in TEXAS_KEYS and _lookup(", ".join(parts[:-1]))):
        return _region_result(region)

    # 3. the state: the last part, or the end of the last part ("Dallas TX")
    k = _key(parts[-1])
    state = _state_row(k, parts[-2] if len(parts) > 1 else "", lang, country)
    if state and len(parts) == 1 and k not in TEXAS_KEYS and (len(k) == 2 or k in _MX_ABBR_KEYS):
        state = None                              # a lone "IN" / "ME" / "Son" is not a place
    city_parts = parts[:-1] if state else parts
    if not state:
        rest, st = _split_trailing_state(parts[-1], lang, country)
        if st and rest:
            state, city_parts = st, parts[:-1] + [rest]
    if not state and len(parts) > 1 and _dual_state_is_texas(parts[-1]):
        state, city_parts = _STATE_BY_KEY["tx"], parts[:-1]              # "Texarkana, TX-AR"
    if state and state[0] == "TX":
        return _texas_result(", ".join(city_parts) if city_parts else None, "US")
    if state:
        code, en, es, ctry = state
        st_field = code if ctry != "MX" else en
        if not city_parts:
            return _result("other", state=st_field, country=ctry, label_en=en, label_es=es)
        city = _pretty_city(", ".join(city_parts))
        city_en = "New York" if normalize_place(city) == "nueva york" else city
        city_es = "Nueva York" if normalize_place(city) == "new york" else city
        return _result("other", city=city_en, state=st_field, country=ctry,
                       label_en=f"{city_en}, {en}", label_es=f"{city_es}, {es}")

    # 3b. the last part is not a state, but an earlier one is Texas: "Tyler, Texas, District 42"
    for i in range(len(parts) - 2, -1, -1):
        if _key(parts[i]) in TEXAS_KEYS:
            return _texas_result(", ".join(parts[:i]) or None, "US")
        rest, st = _split_trailing_state(parts[i], lang, country)
        if st and st[0] == "TX" and rest:
            return _texas_result(", ".join(parts[:i] + [rest]), "US")

    # 4. no state: only the curated bare-city lists are Texas; anything else is "other"
    if len(parts) == 1 and normalize_place(parts[0]) in (NETA65_BARE | TEXAS_BARE):
        return _texas_result(parts[0], "US")
    city = _pretty_city(whole)
    return _result("other", city=city if len(parts) == 1 else None, country="US" if country else None,
                   label_en=city, label_es=city)
