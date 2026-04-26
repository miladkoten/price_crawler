# price_crawler
This code gets a website and a specific product and searches for product prices in that website.

## Usage

Install dependencies:

```powershell
pip install -r requirements.txt
```

Run interactively:

```powershell
python price_crawler.py
```

Or pass the webshop and product directly:

```powershell
python price_crawler.py "https://example-shop.com" "sony headphones" --limit 10
```

The script tries common webshop search URL formats, parses likely product cards, and returns product names, detected model numbers, prices, and product URLs. Some webshops block crawlers or render results only with JavaScript; those sites may need a browser-based crawler instead.
