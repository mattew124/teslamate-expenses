# What the dashboard counts

All of it lives in the `expenses.ownership` view (`tmexpenses/schema.sql`), one row per car:

| Figure | Definition |
|---|---|
| Total cost | purchase + running expenses + `charging_processes.cost` |
| Distance | latest odometer − odometer on the purchase expense (else the first odometer TeslaMate saw) |
| Cost per km — all-in | total cost ÷ distance |
| Cost per km — running | (running + charging) ÷ distance, excludes the purchase price |

<!-- screenshot: dashboard - The Cost of Ownership dashboard in Grafana -->

Headline panels are all-time; the charts, pie and table follow the dashboard time range.
Currency is shown as `$` and distance in km.
