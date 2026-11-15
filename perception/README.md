# Perception (lane B)

Photo matching, per-position attributes and aerial site features. Everything heavy runs
on the team GPU; any laptop can use it with Python 3 and SSH, nothing to install.

## Use it from your laptop

1. Get your public key added to the GPU (`cat ~/.ssh/id_ed25519.pub`, send the line to
   whoever has access). Check with `ssh ubuntu@192.222.54.123 hostname`.
2. From the repo root:

```
python3 -m perception.remote buildings                      # what the GPU can match against
python3 -m perception.remote match 10352309 photo.jpg       # caller photo -> positions
```

or from Python, with the same results as `perception.tools`:

```python
from perception import remote
remote.match_photo("10352309", "photo.jpg", k=5)   # [{node_id, score, confirmed, evidence_path, ...}]
remote.node_attributes(building_id, node_id)
remote.find_equipment(building_id, "fire_extinguisher", near_node=node_id)
remote.hazards_along(building_id, path)
remote.question_attributes(building_id)
remote.detect_site_features(building_id)
```

The first call opens an SSH tunnel in the background. Evidence images are downloaded to a
temp folder and `evidence_path` points at the local copy.

## Change code and run it on the GPU

```
python3 -m perception.remote sync                         # push your perception/ code
python3 -m perception.remote run perception.index 12345   # any perception command, output streams back
python3 -m perception.remote restart                      # reload the service after a sync
```

## Demo buildings

Lane A's six (graphs in `buildings/`): 10352309, 259587, 6550930, 234036, 248192, 248351,
each pinned to the scan its graph uses (`buildings.json`). `princeton-rabinowitz` is the
Rabinowitz Hall scan, floors 2 and 3, for photos taken on site. Hundreds more commercial
buildings are being indexed overnight; `remote buildings` lists what is ready.
