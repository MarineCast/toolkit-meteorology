"""Subprocess failure injection for the real acquisition and publisher paths."""

from __future__ import annotations

import os
import sys
from pathlib import Path

from meteorology.core.artifacts import TransactionalFamilyPublisher
from meteorology.surface_weather import download as module

class _Patch:
    @staticmethod
    def setattr(target, name, value):
        setattr(target, name, value)


def main() -> None:
    from test_offline_weather_fixture import _patch_fetch

    config_path = Path(sys.argv[1])
    phase = sys.argv[2]
    _patch_fetch(_Patch(), [])
    if phase == "candidate":
        real_write = module.write_immutable_table
        sample_count = 0

        def interrupted_write(*args, **kwargs):
            nonlocal sample_count
            result = real_write(*args, **kwargs)
            if kwargs["family"] == "samples":
                sample_count += 1
                if sample_count == 2:
                    os._exit(71)
            return result

        module.write_immutable_table = interrupted_write
    else:
        real_journal = TransactionalFamilyPublisher._write_journal

        def interrupted_journal(path, payload):
            real_journal(path, payload)
            if phase == "promoting" and payload["phase"] == "promoting" and any(
                item["promoted"] for item in payload["items"]
            ):
                os._exit(72)
            if phase == "committed" and payload["phase"] == "committed":
                os._exit(73)

        TransactionalFamilyPublisher._write_journal = staticmethod(interrupted_journal)
    module.download_surface_weather(config_path, overwrite=True, max_workers=1)


if __name__ == "__main__":
    main()
