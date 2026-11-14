# Reading a customer's evidence

Open Customers, then select a customer ID. The drawer explains the customer's current group using observed purchases and the exact data cutoff. It separates two views:

- **Selected period:** the orders, gross purchases, refunds and net revenue in the date window shown in the dashboard.
- **All observed history:** completed purchases and their recorded refunds through the earlier of the selected end and the supplied coverage end.

A Champion may have 20 observed purchases but only 5 in the selected month. These figures answer different questions; neither is a prediction. First seen is the first purchase in the supplied dataset, not necessarily the first-ever purchase at the business.

The timeline includes older purchases when they explain an observed relationship or refund. Purchases add money; refunds subtract it on their recorded date. For example, a refund this month can refer to a purchase before this month, so selected-period net revenue can be negative while the all-observed history remains positive. A completed fully refunded purchase still counts as an observed visit under this app's definitions.

Only completed purchases and valid linked refunds appear. The drawer never adds records after the data cutoff. Future dates beyond coverage do not establish inactivity. A customer with no completed purchase observed by the cutoff is unavailable rather than represented by invented history.

## Consistent paging

The newest events appear first, with a stable order for events sharing a date. Load more retrieves another page from the same source revision and date window. Group rules and monetary totals describe the complete observed history, even when only part of the event list has been loaded.

A second tab can change the active dataset. If the source revision changes, the API rejects the old request and the drawer clears its evidence. Refresh the workspace before opening the customer again. Changing the date range, locking the workspace, switching datasets or navigating to another view also closes the drawer.

## API

`GET /api/v1/owner/customers/{customer_id}` uses the same owner authorization as the dashboard. Optional parameters are `start`, `end`, `offset`, `limit` and `expected_revision`. Dates must be ISO calendar dates; limits are 1–100 events. A stale expected revision returns409, and a customer not observed by the cutoff returns404.

The result contains the bound source, selected period, customer/group explanation, all-observed totals, selected-period totals, events and pagination. Amounts remain integer USD cents; an event's kind determines its sign. Provider credentials and customer contact details are not part of this response.