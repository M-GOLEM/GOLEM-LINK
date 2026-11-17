import asyncio

from golem_link import resource_monitor
from golem_link.config import ConfigManager
from golem_link.db import Database
from golem_link.events import EventBus
from golem_link.identity import NodeIdentity
from golem_link.overload import OverloadEngine
from golem_link.resource_monitor import (
    ResourceMonitor,
    _acpi_temps,
    _gpu_bytes,
    _gpu_number,
    _lhm_temps,
)


def test_gpu_fields_tolerate_nvidia_smi_bracketed_na():
    assert _gpu_number("[N/A]") is None
    assert _gpu_number("N/A") is None
    assert _gpu_number(" 74 ") == 74.0
    assert _gpu_bytes("[N/A]") is None
    assert _gpu_bytes("974") == 974 * 1024 * 1024


def test_gpu_stats_parse_temperature_despite_na_utilisation(tmp_path, monkeypatch):
    cfg = ConfigManager(tmp_path / "data")
    cfg.ensure_dirs()
    monitor = ResourceMonitor(NodeIdentity(cfg), cfg, EventBus())

    class Completed:
        stdout = "GeForce GT 730, [N/A], 974, 2048, 76, [N/A]\n"

    monkeypatch.setattr(resource_monitor.shutil, "which", lambda _name: "nvidia-smi")
    monkeypatch.setattr(resource_monitor.subprocess, "run", lambda *args, **kwargs: Completed())
    stats = monitor._gpu_stats()
    assert stats == [
        {
            "name": "GeForce GT 730",
            "utilization": None,
            "memory_used": 974 * 1024 * 1024,
            "memory_total": 2048 * 1024 * 1024,
            "temperature": 76.0,
            "power": None,
        }
    ]


def test_acpi_temperatures_convert_decikelvin():
    text = (
        '[{"InstanceName":"THERMAL ZONE 0","CurrentTemperature":2982},'
        '{"InstanceName":"THERMAL ZONE 1","CurrentTemperature":100},'
        '{"InstanceName":"bad","CurrentTemperature":null}]'
    )
    entries = _acpi_temps(text)
    assert len(entries) == 1
    assert entries[0]["label"] == "THERMAL ZONE 0"
    assert abs(entries[0]["current"] - 25.05) < 0.06
    assert entries[0]["chip"] == "acpi"


def test_lhm_temperatures_parse_single_object_and_filter_values():
    assert [entry["current"] for entry in _lhm_temps('{"Name":"CPU Package","Value":61.5}')] == [61.5]
    entries = _lhm_temps(
        '[{"Name":"CPU Package","Value":61.5},{"Name":"Board","Value":0},{"Name":"Crazy","Value":400}]'
    )
    assert [entry["label"] for entry in entries] == ["CPU Package"]
    assert _lhm_temps("") == []
    assert _lhm_temps("not json") == []


def test_event_bus_drops_oldest_frame_instead_of_raising():
    bus = EventBus()

    async def scenario() -> None:
        bus.attach_loop(asyncio.get_running_loop())
        queue = await bus.subscribe()
        for index in range(300):
            bus.publish("metrics", {"index": index})
        await asyncio.sleep(0.05)
        assert queue.qsize() == 256
        last = None
        while not queue.empty():
            last = queue.get_nowait()
        assert last["data"] == {"index": 299}
        bus.unsubscribe(queue)

    asyncio.run(scenario())


def test_overload_uses_gpu_temperature_when_platform_sensors_missing(tmp_path):
    cfg = ConfigManager(tmp_path / "data")
    cfg.ensure_dirs()
    db = Database(cfg.db_path())
    engine = OverloadEngine(cfg, db, EventBus(), "gl-self")
    pressure = engine.evaluate(
        {
            "cpu": {"percent": 12.0},
            "ram": {"percent": 20.0},
            "temps": [],
            "gpu": [{"name": "GeForce GT 730", "temperature": 74.0}],
            "disk": [],
        }
    )
    assert pressure.max_temp_c == 74.0
    assert pressure.thermal_protected is False
    db.close()
