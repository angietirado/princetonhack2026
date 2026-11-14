# CSV data contract

Regulars accepts one business's identified sales history. Use stable pseudonymous customer IDs. Do not put names, email addresses, phone numbers or payment details in this file.

The exact header is:

```csv
kind,id,customer_id,order_id,date,amount_cents,status
order,o1,c1,,2026-11-01,1250,completed
order,o2,c1,,2026-11-08,900,completed
refund,r1,,o1,2026-11-09,250,
```

- `kind`: `order` or `refund`.
- `id`: unique within its record type; 1–80 letters, digits, underscores or hyphens.
- `customer_id`: stable pseudonym on every order. Anonymous orders are not supported yet, so total sales must not be presented as complete if they have been omitted.
- `order_id`: empty on orders; the original completed order's ID on refunds.
- `date`: inclusive UTC calendar date in `YYYY-MM-DD` form. Convert business-local timestamps before uploading.
- `amount_cents`: nonnegative whole USD cents, without currency signs, decimal points or thousands separators.
- `status`: `completed`, `pending` or `cancelled` on orders; empty on refunds.

Refunds must occur on or after their linked order, and their combined value cannot exceed that order. Pending and cancelled orders do not contribute to revenue, customer activity or cohorts. Completed fully refunded orders still count as observed visits. Refunds reduce net revenue on the refund date, even when the original order falls before the reporting period.

Uploads are limited to 750 KB, 5,000 records and 731 inclusive days of coverage, with dates between 2000-01-01 and 2100-12-31. Coverage defaults to the earliest and latest supplied record; it is not a claim that no sales are missing. The optional API coverage fields can explicitly cover a broader known interval, while still retaining the observation caveat.

Preview first. Review the business name, counts, dates and synthetic/owner-supplied flag. Import commits only the exact previewed content and options, verified by a hash. Each import is an immutable dataset stored in SQLite in the isolated `owner-data` volume. An identical import is idempotent. Selecting a dataset changes the active workspace; it does not delete the demo or earlier imports.

The dashboard, CSV export, comparison and investigator use the same selected dataset. The default demo remains available for returning to a credential-free walkthrough.
