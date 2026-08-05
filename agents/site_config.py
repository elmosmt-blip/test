"""
site_config.py — единый источник правды для всех секций, категорий и путей сайта.

Этот модуль заменяет разрозненные константы в:
  - section_router.py
  - dashboard/app.py
  - prompt-файлах (writer.txt, trend_hunter.txt)

Все остальные модули должны импортировать отсюда, а не хардкодить.
"""

# ---------------------------------------------------------------------------
# Секции сайта (editorial_type → URL-путь)
# ---------------------------------------------------------------------------
SECTION_PATH: dict[str, str] = {
    "news":    "/news/",
    "insight": "/insights/",
    "review":  "/reviews/",
    "vendor":  "/vendors/",
}

# Множество валидных editorial_type
VALID_SECTIONS: set[str] = set(SECTION_PATH.keys())

# Алиасы — что пользователь может ввести вручную или что может вернуть LLM
SECTION_ALIASES: dict[str, str] = {
    "article":        "insight",
    "insights":       "insight",
    "reviews":        "review",
    "buyer guide":    "review",
    "buyer-guide":    "review",
    "guide":          "insight",
    "vendors":        "vendor",
    "supplier":       "vendor",
}

# ---------------------------------------------------------------------------
# Категории (фильтры на живом сайте)
# ---------------------------------------------------------------------------
VALID_CATEGORIES: set[str] = {
    "SMT Equipment",
    "Inspection",
    "Materials",
    "Smart Factory",
    "Advanced Packaging",
    "EMS",
    "Supply Chain",
    "Industry Events",
}

# Строка для LLM-промптов: "SMT Equipment | Inspection | ..."
CATEGORY_OPTIONS_STR = " | ".join(sorted(VALID_CATEGORIES))

# Алиасы: невалидная категория → валидная
CATEGORY_ALIASES: dict[str, str] = {
    # Известные «плохие» категории, которые генерируют LLM или старые агенты
    "quality control":               "Inspection",
    "process engineering":           "SMT Equipment",
    "soldering":                     "Materials",
    "npi":                           "SMT Equipment",
    "inspection & test":             "Inspection",
    "equipment & materials":         "SMT Equipment",
    "equipment":                     "SMT Equipment",
    "test":                          "Inspection",
    "aoi systems":                   "Inspection",
    "conformal coating":             "Materials",
    "dispensing systems":            "SMT Equipment",
    "manufacturers":                 "SMT Equipment",
    "pick and place machines":       "SMT Equipment",
    "reflow ovens":                  "SMT Equipment",
    "rework stations":               "SMT Equipment",
    "smt process":                   "SMT Equipment",
    "spi systems":                   "Inspection",
    "selective soldering":           "SMT Equipment",
    "solder paste printers":         "SMT Equipment",
    "wave soldering":                "SMT Equipment",
    "x-ray inspection":              "Inspection",
}

# Дефолтная категория, если не удалось определить
DEFAULT_CATEGORY = "SMT Equipment"

# ---------------------------------------------------------------------------
# SEO / URL
# ---------------------------------------------------------------------------
SITE_BASE_URL = "https://www.smtinsider.com"

# ---------------------------------------------------------------------------
# Хелперы
# ---------------------------------------------------------------------------

def normalize_section(value: str | None) -> str | None:
    """Привести строку к валидному editorial_type или None."""
    v = (value or "").strip().lower()
    if not v:
        return None
    if v in VALID_SECTIONS:
        return v
    return SECTION_ALIASES.get(v)

def normalize_category(value: str | None) -> str:
    """Привести строку категории к одной из VALID_CATEGORIES."""
    v = (value or "").strip().lower()
    if not v:
        return DEFAULT_CATEGORY

    # Точное совпадение (case-insensitive)
    for valid in VALID_CATEGORIES:
        if valid.lower() == v:
            return valid

    # Алиас
    if v in CATEGORY_ALIASES:
        return CATEGORY_ALIASES[v]

    # Частичное совпадение (последняя попытка)
    for valid in sorted(VALID_CATEGORIES, key=len, reverse=True):
        if valid.lower() in v or v in valid.lower():
            return valid

    return DEFAULT_CATEGORY