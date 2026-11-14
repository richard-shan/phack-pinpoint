"""CoStar property lookup: address in, normalized building brief out.

    python -m env.costar build --kit <extracted kit folder>     # writes env/data/costar_index.json.gz
    python -m env.costar find "7900 Fannin St"

The index is built once from the hackathon kit (commercial-properties.csv plus the
commercial-property, location-occupancy and company-detail datasets) and committed, so
nobody else needs the 400 MB kit on disk. Every value keeps the record it came from:
the property id, and for tenants the occupancy id.
"""
from __future__ import annotations

import argparse
import csv
import difflib
import gzip
import json
import re
from collections import defaultdict
from functools import lru_cache
from pathlib import Path

from . import config

INDEX = config.DATA / "costar_index.json.gz"

# Tenant words that change what a crew should expect. Checked against the tenant's name
# and business description; the industry code alone adds the generic health-care flag.
RISK_WORDS = {
    "dialysis": "dialysis patients, may be mid-treatment and unable to walk out",
    "surgery": "surgical patients, possibly sedated",
    "surgical": "surgical patients, possibly sedated",
    "oncology": "infusion or oncology patients with limited mobility",
    "infusion": "infusion patients with limited mobility",
    "rehab": "rehabilitation patients with limited mobility",
    "physical therapy": "rehabilitation patients with limited mobility",
    "pediatric": "children present",
    "daycare": "young children present",
    "day care": "young children present",
    "child care": "young children present",
    "childcare": "young children present",
    "preschool": "young children present",
    "montessori": "young children present",
    "senior": "older adults, limited mobility likely",
    "assisted living": "residents with limited mobility",
    "memory care": "residents who may not follow instructions",
    "imaging": "imaging equipment (possible strong magnet or radiation source)",
    "radiology": "imaging equipment (possible strong magnet or radiation source)",
    "mri": "MRI magnet: no ferrous equipment in the scan room",
    "laborator": "laboratory: chemical or biological materials possible",
    "pharmacy": "controlled drugs on site",
    "oxygen": "stored oxygen",
    "welding": "compressed gas cylinders",
    "chemical": "chemical storage possible",
}
HEALTH = "HealthCareAndSocialAssistance"

_ABBR = {"street": "st", "avenue": "ave", "av": "ave", "boulevard": "blvd", "road": "rd",
         "drive": "dr", "lane": "ln", "court": "ct", "place": "pl", "parkway": "pky",
         "pkwy": "pky", "highway": "hwy", "freeway": "fwy", "fwy": "fwy", "circle": "cir",
         "terrace": "ter", "trail": "trl", "square": "sq", "north": "n", "south": "s",
         "east": "e", "west": "w", "northeast": "ne", "northwest": "nw", "southeast": "se",
         "southwest": "sw", "suite": "ste", "floor": "fl"}


def normalize_address(text: str) -> str:
    """"7900 Fannin Street, Houston TX" -> "7900 fannin st". Street line only."""
    line = (text or "").split(",")[0].lower()
    line = re.sub(r"[^a-z0-9\s-]", " ", line)
    words = [_ABBR.get(w, w) for w in line.replace("-", " ").split()]
    for stop in ("ste", "fl", "unit", "bldg"):          # drop a trailing suite or unit
        if stop in words[1:]:
            words = words[:words.index(stop, 1)]
    return " ".join(words)


# ------------------------------------------------------------------ index build
def _enum(enums: dict, name: str, value):
    return (enums.get(name) or {}).get(str(value), value)


def _floor_number(code) -> int | None:
    """Floor number from CoStar's FloorName code: 24 FirstFloor, 25 SecondFloor,
    26 Mezzanine, 27 ThirdFloor ... 149 OneHundredTwentyFifthFloor. Lobby and Ground
    count as floor 1; basements, parking and mechanical levels have no number."""
    if code in (22, 23, 24):
        return 1
    if code == 25:
        return 2
    if isinstance(code, int) and 27 <= code <= 149:
        return code - 24
    return None


def _floor_words(name: str) -> str:
    """"TwentyFirstFloor" -> "Twenty First Floor"."""
    return re.sub(r"(?<=[a-z])(?=[A-Z])", " ", str(name))


def build_index(kit: str | Path, out: Path = INDEX) -> dict:
    kit = Path(kit)
    enums = json.loads((kit / "schemas" / "enums.json").read_text())
    models = defaultdict(list)
    display = {}
    with open(kit / "commercial-properties.csv", encoding="utf-8-sig") as f:
        for r in csv.DictReader(f):
            pid = int(r["PropertyId"])
            display[pid] = {"address": r["Address"], "city": r["City"], "state": r["State"]}
            if r["MatterportModelId"]:
                models[pid].append(r["MatterportModelId"])

    companies = {}
    with open(kit / "data" / "company-detail.jsonl", encoding="utf-8") as f:
        for line in f:
            c = json.loads(line)
            prof = c.get("profile") or {}
            companies[c["id"]] = {
                "name": (c.get("name") or prof.get("companyName") or "").strip(),
                "industry": _enum(enums, "IndustryType", prof.get("industryType") or 0),
                "business": _enum(enums, "BusinessType", prof.get("businessType") or 0),
                "bio": (prof.get("locationBio") or "")[:300],
                "employees": c.get("numEmployees"),
                "status": _enum(enums, "LocationBusinessStatus", c.get("businessStatus") or 0),
                "phone": c.get("locationPhone")}

    floors_of: dict[int, dict] = {}
    props = {}
    with open(kit / "data" / "commercial-property.jsonl", encoding="utf-8") as f:
        for line in f:
            p = json.loads(line)
            pid = p["id"]
            a = p.get("primaryAddress") or {}
            built = [e["year"] for e in p.get("constructionEvents") or []
                     if _enum(enums, "ConstructionEventType", e.get("eventType")) == "ConstructionComplete"]
            renovated = [e["year"] for e in p.get("constructionEvents") or []
                         if _enum(enums, "ConstructionEventType", e.get("eventType")) == "RenovationFinished"]
            elevators = {}
            for block in ("propertyOffice", "propertyRetail", "propertyIndustrial", "propertyFlex"):
                for e in (((p.get(block) or {}).get("structural") or {}).get("elevators") or []):
                    if _enum(enums, "RecordStateType", e.get("elevatorState")) != "No":
                        kind = str(_enum(enums, "ElevatorType", e.get("elevatorType"))).lower()
                        elevators[kind] = (elevators.get(kind) or 0) + (e.get("numElevators") or 0)
            floors = {fl["propertyFloorId"]: (_floor_words(_enum(enums, "FloorName", fl.get("floorName"))),
                                              _floor_number(fl.get("floorName")))
                      for fl in p.get("floors") or []}
            floors_of[pid] = floors
            contacts = []
            for c in p.get("contacts") or []:
                comp = companies.get(c.get("locationId")) or {}
                contacts.append({"role": _enum(enums, "ContactRole", c.get("contactRole")),
                                 "company": comp.get("name") or None,
                                 "phone": c.get("contactPhoneNumber") or comp.get("phone"),
                                 "contact_record_id": c.get("id")})
            desc = next((n.get("value") for n in p.get("notes") or [] if n.get("value")), None)
            ptype = _enum(enums, "PropertyType", p.get("propertyType"))
            props[pid] = {
                "building_id": str(pid),
                "address": (display.get(pid) or {}).get("address") or "",
                "city": a.get("city"), "state": a.get("state"), "postcode": a.get("postcode"),
                "latlon": [a.get("latitude"), a.get("longitude")],
                "market": {14: "Houston", 20: "Los Angeles"}.get(a.get("researchMarketId")),
                "name": p.get("buildingName"),
                "use_type": ptype,
                "use_subtype": _enum(enums, "PropertySubtype", p.get("propertySubtype")),
                "stories": p.get("numStoriesAboveGrade"),
                "year_built": min(built) if built else None,
                "year_renovated": max(renovated) if renovated else None,
                "sqft": p.get("grossBuildingAreaSquareFeet"),
                "construction": (None if not p.get("buildingConstructionMaterial") else
                                 _enum(enums, "BuildingConstructionMaterialType",
                                       p.get("buildingConstructionMaterial"))),
                "sprinklers": p.get("hasSprinklers"),
                "sprinkler_type": (None if not p.get("sprinklerType") else
                                   _enum(enums, "SprinklerType", p.get("sprinklerType"))),
                "elevators": elevators,
                "parking": [{"type": _enum(enums, "ParkingType", k.get("parkingType")),
                             "spaces": k.get("totalSpaces")} for k in p.get("parking") or []],
                "tenancy": _enum(enums, "TenancyType", ((p.get("propertyOffice") or p.get("propertyRetail")
                                                         or {}).get("tenancyType")) or 0),
                "floor_names": [name for name, _ in sorted(set(floors.values()),
                                                           key=lambda f: (f[1] is None, f[1] or 0, f[0]))],
                "contacts": contacts,
                "matterport_model_ids": models.get(pid, []),
                "photo": (p.get("primaryPhoto") or {}).get("attachmentUri"),
                "description": (desc or "")[:500] or None if ptype != "Multifamily" else None,
                "tenants": [],
                "record": {"dataset": "commercial-property", "id": pid},
            }

    with open(kit / "data" / "location-occupancy.jsonl", encoding="utf-8") as f:
        for line in f:
            o = json.loads(line)
            p = props.get(o["propertyId"])
            if p is None or o.get("toDate") is not None:
                continue
            comp = companies.get(o.get("tenantLocationId")) or {}
            for sp in o.get("spaces") or [{}]:
                floor_name, floor_no = floors_of[o["propertyId"]].get(sp.get("propertyFloorId"), (None, None))
                p["tenants"].append({
                    "name": comp.get("name") or None,
                    "suite": (sp.get("suite") or "").strip() or None,
                    "floor": floor_no,
                    "floor_name": floor_name,
                    "sqft": sp.get("squareFeetOccupied"),
                    "industry": comp.get("industry") if comp else None,
                    "business": comp.get("business") if comp else None,
                    "bio": comp.get("bio") or None,
                    "employees": comp.get("employees"),
                    "status": comp.get("status"),
                    "since": (o.get("moveInDate") or o.get("fromDate") or "")[:10] or None,
                    "record": {"dataset": "location-occupancy", "id": o["id"]}})
    for p in props.values():
        p["tenants"].sort(key=lambda t: (t["floor"] is None, t["floor"] or 0, t["suite"] or "", t["name"] or ""))
        if not p["address"]:
            p["address"] = ""
    out.parent.mkdir(parents=True, exist_ok=True)
    with gzip.open(out, "wt", encoding="utf-8") as f:
        json.dump({"properties": props}, f, separators=(",", ":"))
    _index.cache_clear()
    return {"properties": len(props), "tenants": sum(len(p["tenants"]) for p in props.values()),
            "bytes": out.stat().st_size}


# ------------------------------------------------------------------ lookup
@lru_cache(maxsize=1)
def _index() -> dict:
    if not INDEX.exists():
        raise RuntimeError(f"{INDEX.relative_to(config.ROOT)} is missing: run "
                           "`python -m env.costar build --kit <extracted kit folder>`")
    with gzip.open(INDEX, "rt", encoding="utf-8") as f:
        props = json.load(f)["properties"]
    by_addr = defaultdict(list)
    by_model = {}
    for pid, p in props.items():
        by_addr[normalize_address(p["address"])].append(pid)
        for m in p["matterport_model_ids"]:
            by_model.setdefault(m, pid)
    return {"props": props, "by_addr": dict(by_addr), "by_model": by_model}


def risk_flags(tenant: dict) -> list[str]:
    text = f"{tenant.get('name') or ''} {tenant.get('bio') or ''}".lower()
    flags = []
    for word, meaning in RISK_WORDS.items():
        if re.search(rf"\b{re.escape(word)}", text) and meaning not in flags:
            flags.append(meaning)
    if tenant.get("industry") == HEALTH and not flags:
        flags.append("health care or social assistance: occupants may need help getting out")
    return flags


def _brief(p: dict, match: dict) -> dict:
    tenants = [dict(t, risk_flags=risk_flags(t)) for t in p["tenants"]]
    manager = next((c for c in p["contacts"] if c["role"] == "PropertyManager"), None) \
        or next((c for c in p["contacts"] if c["role"] in ("TrueOwner", "RecordedOwner")), None)
    out = dict(p, tenants=tenants, contact=manager, match=match)
    out["occupancy_risks"] = [{"flag": f, "tenant": t["name"], "suite": t["suite"], "floor": t["floor"],
                               "record": t["record"]} for t in tenants for f in t["risk_flags"]]
    out["not_known"] = [k for k in ("stories", "year_built", "construction", "sprinklers")
                        if p.get(k) is None] + ([] if p["elevators"] else ["elevators"])
    out["note"] = "Contact phone numbers and emails in the hackathon kit are placeholders."
    return out


def get_property(address: str) -> dict:
    """Building brief for a street address, a CoStar PropertyId or a Matterport model id.

    Returns {building_id, use_type, stories, year_built, sqft, tenants[], contact, ...}.
    `match.confidence` is 1.0 for an exact address match and lower for a fuzzy one;
    `match.alternatives` lists other properties at the same street address.
    """
    idx = _index()
    q = str(address).strip()
    if q in idx["props"]:
        return _brief(idx["props"][q], {"by": "building_id", "confidence": 1.0, "alternatives": []})
    if q in idx["by_model"]:
        return _brief(idx["props"][idx["by_model"][q]],
                      {"by": "matterport_model_id", "confidence": 1.0, "alternatives": []})
    key = normalize_address(q)
    hits = idx["by_addr"].get(key)
    conf = 1.0
    if not hits:
        close = difflib.get_close_matches(key, idx["by_addr"].keys(), n=1, cutoff=0.82)
        same_number = close and close[0].split()[:1] == key.split()[:1]
        if not close or not same_number:
            return {"building_id": None, "found": False, "query": q,
                    "reason": "no property at that address in the CoStar kit",
                    "closest": [idx["props"][pid]["address"] for c in
                                difflib.get_close_matches(key, idx["by_addr"].keys(), n=3, cutoff=0.6)
                                for pid in idx["by_addr"][c][:1]]}
        hits = idx["by_addr"][close[0]]
        conf = round(difflib.SequenceMatcher(None, key, close[0]).ratio(), 2)
    city = q.lower()
    hits = sorted(hits, key=lambda pid: ((idx["props"][pid]["city"] or "").lower() not in city,
                                         -len(idx["props"][pid]["matterport_model_ids"]),
                                         -len(idx["props"][pid]["tenants"])))
    best = idx["props"][hits[0]]
    out = _brief(best, {"by": "address", "confidence": conf,
                        "alternatives": [{"building_id": pid, "address": idx["props"][pid]["address"],
                                          "city": idx["props"][pid]["city"],
                                          "use_type": idx["props"][pid]["use_type"]} for pid in hits[1:]]})
    out["found"] = True
    return out


def get_tenants(building_id: str, floor: int | None = None, suite: str | None = None) -> list[dict]:
    """Current tenants of a building, optionally narrowed to a floor or a suite."""
    p = _index()["props"].get(str(building_id))
    if p is None:
        return []
    out = []
    for t in p["tenants"]:
        if floor is not None and t["floor"] != int(floor):
            continue
        if suite is not None and (t["suite"] or "").lower().lstrip("#") != str(suite).lower().lstrip("#"):
            continue
        out.append(dict(t, risk_flags=risk_flags(t)))
    return out


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description=__doc__)
    sub = ap.add_subparsers(dest="cmd", required=True)
    b = sub.add_parser("build")
    b.add_argument("--kit", required=True, help="folder the CoStar kit zip was extracted into")
    f = sub.add_parser("find")
    f.add_argument("address")
    a = ap.parse_args()
    if a.cmd == "build":
        print(build_index(a.kit))
    else:
        r = get_property(a.address)
        r["tenants"] = r.get("tenants", [])[:5]
        print(json.dumps(r, indent=1)[:4000])
