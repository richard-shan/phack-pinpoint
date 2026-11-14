# Real source data in the local console

The console catalog uses the downloaded CoStar hackathon bundle and Matterport model index. It does not use the synthetic incident fixture. The imported bundle is a source snapshot, not a live property database.

## Reference import coverage

| Coverage | Count |
| --- | ---: |
| CoStar commercial-property records | 3,339 |
| Houston records | 1,758 |
| Los Angeles records | 1,581 |
| Matterport rows with an explicit matching CoStar property ID | 5,991 |
| Rows with a consistent street number/name | 4,498 |
| Rows with insufficient address information | 352 |
| Rows with an address mismatch under the conservative check | 1,141 |
| Properties with verified, cached Matterport imagery | 22 |
| Original Matterport cube-face images copied locally | 126 |

These are data-coverage counts, not localization accuracy or route-quality metrics. “Verified” in a model's `joinStatus` means its explicit property ID and street number/name agree with a CoStar address; it does not confirm a caller's room, an entrance, or current conditions. Alternate addresses in a CoStar record are included in the address check. Street suffixes are normalized. Ambiguous internal IDs, proximity-only matches, and address-only matches do not create joins. A mismatched row can be a source inconsistency or an incomplete alias; it is retained for inspection and excluded from image evidence.

## Default property

**Bellaire Medical Professional Building — 6550 Mapleridge St, Houston, TX 77081**

- CoStar property ID: `350372`.
- Property type `5` decodes to Office; subtype `64` decodes to Office_Medical.
- Two stories, 1979 construction completion, 81,576 square feet gross building area.
- Matterport lobby model: `i8Q5pWYiYvf`; exact property ID and street number/name agree.
- Six source image views from three panorama positions are included. They are original cube faces, not AI-generated images, synthesized room matches, or a complete 360° tour.
- The sign “6550” is visible in the first cached view. This visual observation is supplementary; the join is based on record identifiers and addresses.

Local source record: `/data/records/350372.json`. Every catalog property has an equivalent inspectable source record and field-path mapping. No medical tenant, patient, operational condition, or equipment claim is inferred from the “Medical” property classification.

## Field handling

- `name` is `buildingName`; when absent, it falls back to the street address and declares that fallback.
- Property type, subtype, and construction event values are decoded with the supplied enums, not assumed to be labels or years.
- `yearBuilt` is the earliest supplied `ConstructionComplete` event, not a renovation year.
- `stories` means `numStoriesAboveGrade`, not a Matterport model's floor count.
- `sqft` prefers gross building area and otherwise uses rentable building area; `sqftBasis` identifies which.
- `postalCode` displays the first five digits; the source record preserves the original value.
- `scanDate` is null. The index's `created` and `original_created` fields are retained separately as model timestamps; neither is treated as a verified scan date.
- Model `panoPoints` are source camera positions only. They are not a navigation graph. No walls, connections, scale convention, traversability, exit, or path is inferred from them.
- Each copied image records its model ID, panorama ID, cube-face number, original position, source URL with access parameters removed, and SHA-256 checksum. The image bytes are unchanged.
- Contact data remains only in source records. It is not presented as a verified emergency contact. No person has been contacted.

## Missing by design

There is no verified caller location, automated matching confidence, Vexcel aerial, staging area, recommended entrance, walking route, stretcher route, current door status, current tenant occupancy, or AED finding in this imported catalog. The console must show those as unknown until a dispatcher supplies a clearly attributed observation or the relevant team service returns evidence. A catalog's model name is a source label, not the caller's location.

## Rebuild

From the repository root:

```sh
python3 app/scripts/build-catalog.py
```

The default input is the repository root’s `.local-data` directory. Sponsor inputs and generated outputs are not included in this contribution. To use another downloaded bundle:

```sh
python3 app/scripts/build-catalog.py --source /absolute/path/to/.local-data
```

Required inputs are `costar/data/commercial-property.jsonl`, `costar/schemas/enums.json`, and `models_index.csv`. Existing `aed_visual/<model-id>.json` metadata and `aed_visual/images/<model-id>/*.jpg` are optional cached image inputs. No network requests or credentials are used. Source-file hashes are recorded in `catalog.json` so rebuilds can be traced to their inputs.

Data and imagery are sponsor-provided source assets, not a newly granted redistribution license. Regenerate them locally from authorized inputs; they are excluded from Git.
