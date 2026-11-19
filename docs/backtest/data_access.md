# Backtest data: where it comes from, and what must never be committed

## Inputs

Two exports, both non-commercial, neither redistributed through this repository.

### OpenSanctions maritime export

Download the "simple" CSV and the follow-the-money JSONL export for the
`sanctions` collection from <https://www.opensanctions.org/datasets/sanctions/>.
Put these files in one directory, `data/opensanctions` by default:

- `maritime.csv`: the maritime simple export, one row per vessel, with the
  `risk` column holding semicolon separated tags such as `mare.shadow` and
  `mare.detained`.
- `entities.ftm.json`: the JSONL export, one entity per line.
- `vessels.json`: the FTM vessels extracted from that export, without reverse
  links, as a JSON array.

`data/` is already excluded by `.gitignore`. OpenSanctions data is CC BY-NC.

### Global Fishing Watch vessel identity

The API is at <https://gateway.api.globalfishingwatch.org/v3/vessels/search>
using the dataset `public-global-vessel-identity:latest`.

1. Create a GFW account and an API token at <https://globalfishingwatch.org/our-apis/>.
2. Export it in the shell that runs the fetch:

   ```
   export GFW_TOKEN=...
   ```

   The script reads the token from the environment and never writes it to disk.
3. Run the fetch. It caches each response under `work/`, which `.gitignore`
   excludes, and resumes if interrupted.

GFW data is CC BY-NC and non-commercial. The raw responses are cached locally
for the analysis and must not be committed, pasted into an issue, or attached to
a pull request.

## Work directory

```
work/
  cohort.json     positives and controls, safe to regenerate, still not committed
  gfw_cache.json  raw API responses
```

Everything under `work/` and `data/` is reproducible from the two sources above
plus the code, which is why neither is committed.

## Reproducing the report

```
python -I scripts/backtest/run.py cohort --data-dir data/opensanctions
python -I scripts/backtest/run.py fetch  --work work --cohort work/cohort.json
python -I scripts/backtest/run.py report --work work --cutoff 2024-05-31
```

`python -I` keeps the interpreter from importing anything out of the data
directory, since these are external exports.

The report is deterministic given the same cache; the control sample is drawn
with seed 7. If the upstream exports change, the cohort changes with them, so
the numbers in `time_split_results.md` are tied to the export date recorded
there.
