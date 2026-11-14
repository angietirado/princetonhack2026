"""Versioned display classifications derived only from exact source category labels."""

from collections.abc import Iterable

from spending_api.models import CategoryMapping, CategoryMappingEntry, Transaction

# Keep this vocabulary explicit: merchant names and descriptions cannot establish
# item-level detail, recurring status, business purpose, or tax deductibility.
# Only lookup keys are normalized; raw source categories remain unchanged.
_SOURCE_CATEGORIES: dict[str, tuple[str, str, str | None, str | None]] = {
    "coffee": ("dining", "Dining", "coffee", "Coffee"),
    "dining": ("dining", "Dining", None, None),
    "groceries": ("groceries", "Groceries", None, None),
    "rent": ("housing", "Housing", "rent", "Rent"),
    "housing": ("housing", "Housing", None, None),
    "transport": ("transport", "Transport", None, None),
    "subscriptions": ("subscriptions", "Subscriptions", None, None),
    "utilities": ("utilities", "Utilities", None, None),
    "entertainment": ("entertainment", "Entertainment", None, None),
    "shopping": ("shopping", "Shopping", None, None),
    "healthcare": ("healthcare", "Healthcare", None, None),
    "education": ("education", "Education", None, None),
    "fees": ("fees", "Fees", None, None),
}


def build_category_mapping(rows: Iterable[Transaction]) -> CategoryMapping:
    """Describe each distinct raw category in the returned rows, including exclusions."""
    entries = []
    for raw_category in sorted({row.category for row in rows}):
        classification = _SOURCE_CATEGORIES.get(raw_category.strip().casefold())
        if classification is None:
            entries.append(
                CategoryMappingEntry(
                    raw_category=raw_category,
                    category_id="unclassified",
                    category_label="Unclassified",
                    origin="unclassified",
                )
            )
            continue
        category_id, category_label, subcategory_id, subcategory_label = classification
        entries.append(
            CategoryMappingEntry(
                raw_category=raw_category,
                category_id=category_id,
                category_label=category_label,
                subcategory_id=subcategory_id,
                subcategory_label=subcategory_label,
                origin="source_mapping",
            )
        )
    return CategoryMapping(entries=entries)
