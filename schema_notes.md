# Database knowledge — ecommerce_db

This file is human-maintained business context about the database, injected
into every question so the assistant interprets tables and columns correctly.
The app ALSO auto-reads the live column list from the database; this file adds
the meaning behind those columns. Edit this file (in GitHub) whenever the data
model changes — no code change needed.

## What this database is

Sales and marketing data, used to understand metrics across many dimensions.
Reports are either sales data or marketing-spend data.

- **sales_master** — sales data for ALL ecommerce channels (marketplaces like
  Amazon, Blinkit, Zepto, Instamart, etc.).
- **gokwik** / **GoKwik** — sales data for the WEBSITE channel only (Shopify /
  D2C). This is Plush's own website sales.
- **marketing_master** — marketing-spend data for all ecommerce channels.
- **meta_ads** — Meta (Facebook/Instagram) ads data; part of website marketing spend.
- **google_ads** — Google ads data; part of website marketing spend.
- **crm_automations**, **crm_campaigns** — CRM ads data; part of website marketing spend.
- **shopify** — website traffic data by UTM parameter, with session and add-to-cart data.
- **discount_master** — discount data for ecommerce channels; part of ecommerce sales data.

## CRITICAL: the two GoKwik tables

There are **two tables with almost the same name** — `GoKwik` and `gokwik` —
covering overlapping date ranges:
- one holds roughly 01-Jan-2025 to 31-Jul-2026,
- the other holds roughly 01-Jan-2026 to 09-Aug-2026.

When calculating website sales, **do NOT double-count**. Append/union them
carefully and de-duplicate by order — never sum both tables blindly over an
overlapping period. When in doubt about the overlap window, prefer a UNION that
distinct-counts orders by `shopify_order_name` rather than adding two totals.

## gokwik / GoKwik — website (Shopify/D2C) sales, one row per ORDER

Granularity: order level. Use it for sales, AOV, GMV, units/order, order counts,
COD vs Prepaid, New vs Old, geo-level breakdowns, and date-wise summaries.

Key columns and their meaning:
- **Created_At** — order placed date. **Use this for any date-based filtering or grouping.**
- **shopify_order_name** — unique order ID. **COUNT(DISTINCT shopify_order_name) = number of orders.**
- **payment_method** — whether an order is Prepaid or COD.
- **total_item_count** — number of distinct products in an order.
- **total_qty_ordered** — total quantity of products in an order.
- **grand_total** — the sale amount AFTER discount. **This is "sales." Whenever
  the user asks for sales, sum grand_total.**
- **coupon_code** — coupon code used on the order.
- **utm_source**, **utm_medium**, **utm_campaign** — UTM parameters. A channel is
  defined by the **combination of utm_source / utm_medium** (mappable from the
  sales_master dump).
- **customer_phone** — consumer's number; treat as the unique consumer ID.
  **COUNT(DISTINCT customer_phone) = number of consumers.**
- **billing_city**, **billing_pincode**, **billing_state** — use these for any
  geo-level analysis.
- **product_name** — all products in the order; multiple products are separated
  by " | " within the same cell.
- **mrp_total** — total GMV of the order (grand_total + discount). Use for
  discount math: **discount % = (mrp_total − grand_total) / mrp_total**, i.e.
  (GMV − Sale) / GMV.
- **customer_type___gokwik** — new vs old relative to the GoKwik platform. Not
  very important.
- **customer_type___merchant** — new vs old relative to US (the merchant).
  **Important — use this to split new vs old sales.**

## sales_master — sales data for ALL ecommerce channels

Key columns and their meaning:
- **Order_Date** — date of order.
- **City** — city of order.
- **SKU_ID** — channel-specific SKU code. **Do not use this.**
- **Platform** — the ecommerce channel name (Amazon, Blinkit, Zepto, Instamart, etc.).
- **Product_Name** — unique product CATEGORY. **Use this whenever a product
  category is asked for.**
- **internal SKU** — the actual unique SKU code. **Use this whenever a SKU code
  is asked for.**

## Everything else

The remaining tables are largely self-explanatory from their column names. If a
question depends on a table or column not described here, rely on the live
column list and say plainly if something needed is missing, rather than guessing.
