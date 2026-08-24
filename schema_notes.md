# Database knowledge — ecommerce_db

This file is human-maintained business context about the database, injected
into every question so the assistant interprets tables and columns correctly.
The app ALSO auto-reads the live column list from the database; this file adds
the meaning behind those columns and how tables map to each other. Edit this
file (in GitHub) whenever the data model changes — no code change needed.

## Global rules (apply to every answer)

- **Default year is 2026.** Whenever a query mentions a month (Jun, Jul, etc.)
  without a year, assume 2026 unless a different year is explicitly stated.
- **Answer in precise bullet points**, not long write-ups. Keep prose minimal.
- **Always show BOTH a table and a graph** for a query's results wherever the
  data supports it — call render_table and render_chart, not just prose.
- Several dumps/masters exist to enrich data by category, geo, and new/repeat.
  Use them (described below) rather than guessing.

## What this database is

Sales and marketing data, used to understand metrics across many dimensions.
Reports are either sales data or marketing-spend data.

- **sales_master** — sales data for ALL ecommerce channels (marketplaces:
  Amazon, Blinkit, Zepto, Instamart, etc.).
- **gokwik** / **GoKwik** — sales data for the WEBSITE channel only (Shopify /
  D2C). Plush's own website sales.
- **marketing_master** — marketing-spend data for all ecommerce channels.
- **meta_ads** — Meta (Facebook/Instagram) ads data; part of website marketing spend.
- **google_ads** — Google ads data; part of website marketing spend.
- **crm_automations**, **crm_campaigns** — CRM ads data; part of website marketing spend.
- **shopify_sessions** — website traffic by UTM parameter, with session and add-to-cart data.
- **discount_master** — discount data for ecommerce channels; part of ecommerce sales data.
- **meta_ad_dump** — Meta ads mapped to a category. For category-level Meta data, map via this table.
- **pincode_master** — pincode → city, region, state, zone, tier master (see mapping rules below).
- **product_master_dump** — product name / internal SKU / channel SKU mapping (see mapping rules below).
- **product_report** — website/D2C/Shopify product-level sales and units data (order-based).
- **source_medium_dump** — source/medium → channel mapping, used for the gokwik report.

## CRITICAL: the two GoKwik tables

There are **two tables with almost the same name** — `GoKwik` and `gokwik` —
covering overlapping date ranges:
- one holds roughly 01-Jan-2025 to 31-Jul-2026,
- the other holds roughly 01-Jan-2026 to 09-Aug-2026.

When calculating website sales, **do NOT double-count**. Append/union them
carefully and de-duplicate by order — never sum both tables blindly over an
overlapping period. Prefer a UNION that distinct-counts orders by
`shopify_order_name` rather than adding two separate totals.

## gokwik / GoKwik — website (Shopify/D2C) sales, one row per ORDER

Granularity: order level. Use it for sales, AOV, GMV, units/order, order counts,
COD vs Prepaid, New vs Old, geo-level breakdowns, and date-wise summaries.

- **Created_At** — order placed date. **Use this for any date filtering/grouping.**
- **shopify_order_name** — unique order ID. **COUNT(DISTINCT shopify_order_name) = number of orders.**
- **payment_method** — Prepaid or COD.
- **total_item_count** — number of distinct products in an order.
- **total_qty_ordered** — total quantity of products in an order.
- **grand_total** — sale amount AFTER discount. **This is "sales." When the user
  asks for sales, sum grand_total.**
- **coupon_code** — coupon code used on the order.
- **utm_source**, **utm_medium**, **utm_campaign** — UTM parameters. A channel is
  defined by the **combination of utm_source / utm_medium**, mappable via
  **source_medium_dump**.
- **customer_phone** — consumer's number; unique consumer ID.
  **COUNT(DISTINCT customer_phone) = number of consumers.**
- **billing_city**, **billing_pincode**, **billing_state** — raw geo fields. For
  geo analysis, map **billing_pincode** through **pincode_master** and report the
  master's values (see mapping rules).
- **product_name** — all products in the order; multiple separated by " | " in one cell.
- **mrp_total** — total GMV of the order (grand_total + discount). Discount math:
  **discount % = (mrp_total − grand_total) / mrp_total**, i.e. (GMV − Sale) / GMV.
- **customer_type___gokwik** — new vs old relative to the GoKwik platform. Not important.
- **customer_type___merchant** — new vs old relative to US (the merchant).
  **Important — use this to split new vs old sales.**

## sales_master — sales for ALL ecommerce channels

- **Order_Date** — date of order.
- **City** — city of order (but for geo analysis prefer pincode → pincode_master mapping).
- **SKU_ID** — channel-specific SKU code. **Do not use this.**
- **Platform** — the ecommerce channel name (Amazon, Blinkit, Zepto, Instamart, etc.).
- **Product_Name** — unique product CATEGORY. Use for category when asked (or map via product_master_dump).
- **internal SKU** — the actual unique SKU code. Use for SKU code when asked.

## pincode_master — geo master (map, don't read geo raw)

Granularity: one row per pincode. For ANY geo-level query — D2C or marketplace —
map the sale report's pincode to this table and return geo values from HERE, not
from the sales table's own city field.
- **pincode** — the join key.
- **statename** → use as **State**
- **City Name** → use as **City**
- **regionname** → use as **Region**
- **zone** → use as **Zone**
- **City Tier** → use as **Tier**

## product_master_dump — product/SKU/category master

Maps channel SKU code ↔ internal SKU code ↔ product name ↔ category.
- Use whenever a question needs **category-level** or **SKU-level** data
  (default SKU = internal SKU code).
- Every sales report (Ecomm or D2C) maps to this via channel SKU code or product name.

## product_report — website/D2C product-level sales (order-based)

- Product title maps to **product_master_dump** to get internal SKU / category.
- **Order Name** — the order ID for the product; maps to the gokwik report to
  pull new/old, channel, campaign, COD/Prepaid, pincode, etc. (anything at order level).
- **net_items_sold** — units. **total_sales** — sales for the product. **day** — order date.

## source_medium_dump — channel mapping for gokwik

Maps a source/medium combination to a channel name; use it to resolve the gokwik
report's utm_source/utm_medium into a proper channel.

## Everything else

Remaining tables are largely self-explanatory from their column names. If a
question depends on a table or column not described here, rely on the live
column list and say plainly if something needed is missing, rather than guessing.
