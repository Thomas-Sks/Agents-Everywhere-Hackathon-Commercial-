"""Product catalogue on a JSON file — implements `CatalogPort`.

Prices and features come from here and never from the model: a hallucinated price in a sales
email is a mistake that costs money, not a matter of style.

The file can be populated by `scripts/load_dataset.py` from `products.csv` of the Kaggle
"CRM Sales Opportunities" dataset.
"""

from __future__ import annotations

from revenue_agent.adapters.storage.json_document import JsonDocument
from revenue_agent.domain.models import Product

PRODUCTS_KEY = "products"


class JsonFileCatalogAdapter:
    def __init__(self, document: JsonDocument) -> None:
        self._document = document

    def list_products(self) -> list[Product]:
        raw_products = self._document.read().get(PRODUCTS_KEY, [])
        return [_deserialise(raw) for raw in raw_products]

    def get_product(self, product_id: str) -> Product | None:
        return next(
            (product for product in self.list_products() if product.id == product_id), None
        )


def _deserialise(raw: dict) -> Product:
    return Product(
        id=raw["id"],
        name=raw.get("name", raw["id"]),
        category=raw.get("category", ""),
        price=float(raw.get("price", 0)),
        description=raw.get("description", ""),
    )
