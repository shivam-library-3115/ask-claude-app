"""
Consolidated "Plush" product export across Blinkit, Instamart, Zepto,
Amazon, and Flipkart Minutes, across 12 pincodes, via QuickCommerceAPI.

Writes ONE combined CSV (with a "Platform" column) plus, optionally,
one CSV per platform if SPLIT_PER_PLATFORM is True below.

Setup:
    pip install requests

Usage:
    Just run the cell / script. API key and pincodes are set inline below.

Notes:
    - lat/lon are approximate city-centre / GPO coordinates per pincode.
    - Flipkart Minutes (and DMart/JioMart, if you add them later) require
      the `pincode` param explicitly, not just lat/lon — handled below.
    - Delivery ETA (platform.sla) isn't available for Amazon or Flipkart
      Minutes per QuickCommerceAPI docs, so that column will show "N/A"
      for those two platforms — this is expected, not a bug.
    - Each platform call costs 1 credit. 5 platforms x 12 pincodes = 60
      credits per full run.
"""

import csv
import os
import re
import time
from datetime import datetime, timezone

import requests

API_URL = "https://api.quickcommerceapi.com/v1/search"
API_KEY = "aa2438c6-1689-4422-bc65-35324e007810"

QUERY = "plush"
SPLIT_PER_PLATFORM = True  # also write one CSV per platform, in addition to the combined one
REQUEST_DELAY_SECONDS = 1.0  # be polite between calls

# Platform display name (for filenames/logs) -> API `platform` param value.
# Platforms that require an explicit `pincode` param (per QuickCommerceAPI docs)
# are listed in PINCODE_REQUIRED.
PLATFORMS = {
    "Blinkit": "BlinkIt",
    "Instamart": "Swiggy",
    "Zepto": "Zepto",
    "Amazon": "Amazon",
    "Flipkart Minutes": "Minutes",
}
PINCODE_REQUIRED = {"Minutes", "DMart", "JioMart"}

PINCODE_COORDS = {
    "380001": ("Ahmedabad", 23.0258, 72.5873),
    "560001": ("Bengaluru", 12.9767, 77.5713),
    "600001": ("Chennai", 13.0908, 80.2836),
    "110001": ("New Delhi", 28.6315, 77.2167),
    "122001": ("Gurugram", 28.4595, 77.0266),
    "500001": ("Hyderabad", 17.3903, 78.4744),
    "302001": ("Jaipur", 26.9124, 75.7873),
    "700001": ("Kolkata", 22.5726, 88.3639),
    "400001": ("Mumbai", 18.9322, 72.8264),
    "201301": ("Noida", 28.5708, 77.3210),
    "121001": ("Faridabad", 28.4089, 77.3178),
    "411001": ("Pune", 18.5196, 73.8553),
}

FIELDNAMES = [
    "Platform", "Product Name", "MRP", "Selling Price", "SKU / Item ID",
    "Pincode", "City", "Discount", "Delivery ETA",
    "Availability", "Pack Size", "Size", "Unit", "Extracted At",
]


def search_products(query, platform_param, pincode):
    city, lat, lon = PINCODE_COORDS[pincode]
    params = {"q": query, "lat": lat, "lon": lon, "platform": platform_param}
    if platform_param in PINCODE_REQUIRED:
        params["pincode"] = pincode

    resp = requests.get(API_URL, headers={"X-API-Key": API_KEY}, params=params, timeout=15)
    resp.raise_for_status()
    return city, resp.json()


def parse_quantity(quantity_raw):
    """Split a raw quantity string like '210 g' or '5 pcs' into (size, unit)."""
    if not quantity_raw or quantity_raw == "N/A":
        return "N/A", "N/A"
    match = re.match(r"^\s*([\d.]+)\s*([a-zA-Z]+)\s*$", str(quantity_raw))
    if match:
        return match.group(1), match.group(2)
    return quantity_raw, "N/A"


def build_rows(payload, platform_display, pincode, city):
    products = payload.get("data", {}).get("products", [])
    rows = []
    for p in products:
        mrp = p.get("mrp")
        price = p.get("offer_price", p.get("price"))
        discount = None
        if isinstance(mrp, (int, float)) and isinstance(price, (int, float)) and mrp:
            pct = round((mrp - price) / mrp * 100, 1)
            discount = f"{pct}%"

        platform_info = p.get("platform") or {}
        quantity_raw = p.get("quantity", "N/A")
        size, unit = parse_quantity(quantity_raw)

        rows.append({
            "Platform": platform_display,
            "Product Name": p.get("name", "N/A"),
            "MRP": mrp,
            "Selling Price": price,
            "SKU / Item ID": p.get("id", "N/A"),
            "Pincode": pincode,
            "City": city,
            "Discount": discount or "N/A",
            "Delivery ETA": platform_info.get("sla", "N/A"),
            "Availability": "In Stock" if p.get("available") else "Out of Stock",
            "Pack Size": quantity_raw,
            "Size": size,
            "Unit": unit,
            "Extracted At": datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds"),
        })
    return rows


def write_csv(path, rows):
    with open(path, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=FIELDNAMES)
        writer.writeheader()
        writer.writerows(rows)


def main():
    output_dir = os.path.join(os.path.expanduser("~"), "Desktop", "Scrapper output")
    os.makedirs(output_dir, exist_ok=True)
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")

    all_rows = []
    rows_by_platform = {name: [] for name in PLATFORMS}

    for platform_display, platform_param in PLATFORMS.items():
        for pincode in PINCODE_COORDS:
            print(f"Fetching '{QUERY}' on {platform_display} for {pincode}...")
            try:
                city, payload = search_products(QUERY, platform_param, pincode)
            except requests.HTTPError as e:
                print(f"  -> request failed: {e}")
                continue
            except requests.RequestException as e:
                print(f"  -> network error: {e}")
                continue

            rows = build_rows(payload, platform_display, pincode, city)
            print(f"  -> {len(rows)} product(s) found")
            all_rows.extend(rows)
            rows_by_platform[platform_display].extend(rows)

            time.sleep(REQUEST_DELAY_SECONDS)

    combined_path = os.path.join(output_dir, f"qcomm_plush_export_ALL_{timestamp}.csv")
    write_csv(combined_path, all_rows)
    print(f"\nCombined: {len(all_rows)} rows written to {combined_path}")

    if SPLIT_PER_PLATFORM:
        for platform_display, rows in rows_by_platform.items():
            slug = platform_display.lower().replace(" ", "_")
            per_platform_path = os.path.join(output_dir, f"{slug}_plush_export_{timestamp}.csv")
            write_csv(per_platform_path, rows)
            print(f"{platform_display}: {len(rows)} rows written to {per_platform_path}")


if __name__ == "__main__":
    main()