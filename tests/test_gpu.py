# SPDX-License-Identifier: GPL-3.0-only

from wigix import gpu, widgets
from wigix.engine import Page

widgets.load_plugins()


def fake_card(root, vendor="0x1002", busy="58"):
    dev = root / "card1" / "device"
    (dev / "hwmon" / "hwmon3").mkdir(parents=True)
    (dev / "vendor").write_text(vendor + "\n")
    if busy is not None:
        (dev / "gpu_busy_percent").write_text(busy + "\n")
    (dev / "mem_info_vram_used").write_text("5659287552\n")
    (dev / "mem_info_vram_total").write_text("25753026560\n")
    (dev / "hwmon" / "hwmon3" / "temp1_input").write_text("51000\n")
    (dev / "hwmon" / "hwmon3" / "power1_average").write_text("119000000\n")


def test_amd_sysfs(tmp_path):
    fake_card(tmp_path)
    g = gpu._amd(str(tmp_path))
    assert g["util"] == 58 and g["temp"] == 51 and g["power"] == 119
    assert gpu.summary(g) == "5.3G 51°C 119W"


def test_amd_skips_other_vendors_and_cards_without_a_counter(tmp_path):
    fake_card(tmp_path, vendor="0x8086")
    assert gpu._amd(str(tmp_path)) is None
    other = tmp_path / "x"
    other.mkdir()
    fake_card(other, busy=None)
    assert gpu._amd(str(other)) is None


def test_nvidia_parse():
    g = gpu.parse_nvidia("NVIDIA GeForce RTX 4070, 37, 1024, 12282, 45, [N/A]")
    assert g["util"] == 37 and g["vram_total"] == 12282 * 2 ** 20 and g["power"] is None
    assert gpu.parse_nvidia("garbage") is None
    assert gpu.summary(g) == "1.0G 45°C"


def test_system_widget_draws_gpu_and_can_turn_it_off(monkeypatch):
    monkeypatch.setattr(gpu, "read_gpu", lambda: {"name": "x", "util": 70.0, "vram_used": 2 ** 30, "vram_total": 8 * 2 ** 30,
                                                  "temp": 60.0, "power": 100.0})

    def render(cfg):
        page = Page({"grid": {"cols": 1, "rows": 1, "gap": 10}, "widgets": [dict(cfg, type="system", at=[0, 0, 1, 1])]}, {})
        w = page.items[0][0]
        w.update()
        w.update()
        return w, page.render()
    on, img_on = render({})
    assert on.gpu_part()["text"] == "70%"
    off, img_off = render({"gpu": False})
    assert off.gpu_part() is None and img_on.tobytes() != img_off.tobytes()


def test_no_gpu_means_no_gpu_part(monkeypatch):
    monkeypatch.setattr(gpu, "read_gpu", lambda: None)
    page = Page({"grid": {"cols": 1, "rows": 1, "gap": 10}, "widgets": [{"type": "system", "at": [0, 0, 1, 1]}]}, {})
    w = page.items[0][0]
    w.update()
    assert w.gpu_part() is None
