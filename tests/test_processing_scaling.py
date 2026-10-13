from pathlib import Path
import importlib.util
import json


def test_each_physical_point_block_is_read_once(tmp_path, monkeypatch, capsys):
    path = Path(__file__).resolve().parents[1] / 'benchmarks/native_processing.py'
    spec = importlib.util.spec_from_file_location('benchmark', path)
    benchmark = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(benchmark)
    monkeypatch.setattr(benchmark.sys, 'argv', [str(path)])
    benchmark.main()
    measured = json.loads(capsys.readouterr().out)
    assert measured['decode_calls'] == 1
    assert measured['rows_read'] == 256 * 169
    assert measured['row_groups_read'] == 4 * 169
    assert measured['output_rows'] == 256
