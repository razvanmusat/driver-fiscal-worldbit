from .fisco import FiscoDriver
from .fiscool import FiscoolDriver

DRIVERS = {FiscoDriver.name: FiscoDriver, FiscoolDriver.name: FiscoolDriver}


def driver_settings(cfg, name):
    """Setările driverului: valorile salvate peste valorile implicite declarate de driver."""
    cls = DRIVERS[name]
    merged = {f.key: (dict(f.default) if isinstance(f.default, dict) else f.default) for f in cls.fields}
    merged.update(cfg.get("drivers", {}).get(name, {}))
    return merged


def build_driver(cfg):
    name = cfg["driver"]
    if name not in DRIVERS:
        raise ValueError(f"driver necunoscut: {name}")
    return DRIVERS[name](driver_settings(cfg, name))
