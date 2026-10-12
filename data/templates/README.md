# Import template

`plates_import_template.csv` is empty on purpose. One row is one diner's plate of one food at one service.

| Column | Required | Values |
|---|---|---|
| `service_date` | yes | `YYYY-MM-DD` |
| `session_name` | no | for example `Lunch` (default `imported`) |
| `diner_token` | yes | opaque token such as `T-501`; no names |
| `initial_g` | yes | first portion in whole grams, more than 0 |
| `seconds_g` | no | total seconds in grams |
| `returned_g` | no | edible food left, plate weight subtracted. Leave blank if not measured: blank means missing, not zero |
| `satisfaction` | no | `enough`, `wanted_more`, `unsure` (default `unsure`) |
| `leftover_reason` | no | `none`, `enough_food`, `dislike`, `interrupted`, `shared`, `unknown` |
| `serve_measurement` | no | `weighed` (default) or `scoop_estimate` |
| `return_measurement` | no | `weighed` (default) or `scoop_estimate` |
| `losses_g` | no | spilled or dropped grams |

Import through Setup → Import. You must describe the source, how it was collected, what it measures, its license, and whether the records are really measured or synthetic. The app checks every row first and imports nothing if any row has a problem.
