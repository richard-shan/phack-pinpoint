# Perception outputs for lane A's six buildings

Generated Saturday evening from the scan each graph in `buildings/<id>/graph.json` uses.

- `<building_id>/attributes.json`: one record per scanned position, keyed
  `<matterport_model_id>/<sweep_id>`. Import with
  `python3 -m env.import_attributes <building_id> --from perception/data/<building_id>/attributes.json`.
- `<building_id>/site.json`: entrances, staging spots and obstructions from the aerial
  tile, each with `latlon`, a `side`, a `confidence` and its sources, plus the
  post-disaster pass under `disaster`.

Evidence paths are relative to the repo root and point into `cache/`, which is not in
git: the images exist only on the machine that ran the pass. Regenerate them with
`python -m perception.index <id>` and `python -m perception.site <id>`.

Not hand-checked. Entrances are inferred from canopies, walkways and recesses, not seen;
treat anything below 0.6 confidence as a candidate. Hazards are over-reported in two
buildings (259587 and 248351 flag about a third of positions).
