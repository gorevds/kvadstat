"""Тесты классификатора класса ЖК (kvadstat.klass)."""
from kvadstat import klass


def test_curated_overrides_dev_default():
    # Донстрой по умолчанию бизнес, но Баррин Хаус курирован как элит.
    assert klass.classify(3510081163685, "Донстрой") == "элит"
    # Гранель по умолчанию комфорт, но Павелецкая курирована как бизнес.
    assert klass.classify(9597429770985, "Гранель") == "бизнес"


def test_dev_default_applies_without_price():
    # Некурированный ПИК → комфорт без всякой цены.
    assert klass.classify(118, "ПИК") == "комфорт"
    # Некурированный Level → бизнес.
    assert klass.classify(6194403967319, "Level") == "бизнес"


def test_auto_band_for_unknown_developer():
    # Неизвестный застройщик: класс по цене, тир столицы.
    assert klass.classify(999999, "НовыйЗастройщик", "msk", 350_000) == "комфорт"
    assert klass.classify(999999, "НовыйЗастройщик", "msk", 500_000) == "бизнес"
    assert klass.classify(999999, "НовыйЗастройщик", "msk", 1_500_000) == "элит"
    # Регион: те же 250k уже комфорт (ниже столичного эконома).
    assert klass.classify(999999, "НовыйЗастройщик", "ekb", 250_000) == "бизнес"
    assert klass.classify(999999, "НовыйЗастройщик", "ekb", 150_000) == "комфорт"


def test_region_tier_shifts_bands():
    # 350k в столице — комфорт, в регионе — премиум.
    assert klass.auto_class("msk", 350_000) == "комфорт"
    assert klass.auto_class("ekb", 350_000) == "премиум"


def test_unknown_without_price_is_none():
    assert klass.classify(999999, "НовыйЗастройщик") is None


def test_all_curated_values_valid():
    for v in klass._CURATED.values():
        assert v in klass.VALID_CLASSES
    for v in klass._DEV_DEFAULT.values():
        assert v in klass.VALID_CLASSES
