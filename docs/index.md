# teslamate-expenses

Track what your Tesla costs to own, next to [TeslaMate](https://github.com/teslamate-org/teslamate).

!!! warning "Vibe coded"

    This project was written with an AI coding assistant (Claude Code), directed
    and tested by a person, not hand-written or audited line by line. It has tests, runs in a
    read-only, non-root container and keeps its database role least-privilege, but treat it as you
    would any young, unreviewed software: back up your TeslaMate database before first use, and
    read the code before you trust it with anything you care about.

Not affiliated with TeslaMate or Tesla. It's an unofficial add-on that reads TeslaMate's data.

OneDrive is a trademark of Microsoft Corporation. This project is not affiliated with or endorsed by Microsoft. The OneDrive logo is fetched at runtime from [selfh.st/icons](https://selfh.st/icons) ([CC BY 4.0](https://creativecommons.org/licenses/by/4.0/)); it is not part of this repository.

- **Web UI** (Flask) to add/edit expenses: category, date, time, amount, odometer, vendor,
  notes and a link to the PDF invoice in your cloud storage. The odometer can be filled in from
  TeslaMate's own position data for the chosen date and time. The amount field works out sums
  (`120.50+89.95`, `400/3`) when you leave it, press Enter or save, and has keys for the operators.
  The list sorts by any column, either way (click a heading, or use **Sort by** and **Order**), and
  switches between rows and tiles with the toggle beside the filter. Editing an expense, then saving
  or cancelling, returns you to the category and order you were looking at.
  <!-- screenshot: expense-list-tiles - The expense list as tiles, with the sort and layout controls -->
  <!-- screenshot: amount-calculator - The amount field with a sum typed in and its result -->
- **Optional OneDrive upload**: choose the invoice PDF in the form and it goes into that car's
  OneDrive folder, with the link filled in for you.
- **Postgres schema** `expenses` inside the TeslaMate database, so Grafana joins it to
  TeslaMate's charging costs and odometer with no second datasource.
- **Grafana dashboard** "Cost of Ownership": total cost of ownership, purchase vs running
  vs charging, cost per km, per day and per month, monthly spend by category, cumulative cost, and
  a table of every expense with a clickable invoice link.

<!-- screenshot: expense-form - The form for adding an expense -->

Starter categories: Initial purchase, Insurance, Registration, Tyres, Cabin air filter,
Air filter, Wheel alignment, Tyre rotation. Add more on the **Categories** page
(new ones count as running costs).

```
 browser ──► proxy + forward-auth ──► expenses (Flask/gunicorn) ──► TeslaMate Postgres
                                                                       ▲   schema `expenses`
                                     Grafana (TeslaMate datasource) ───┘   + public.* (read-only)
```

New here? Start with [Setup](setup.md).
