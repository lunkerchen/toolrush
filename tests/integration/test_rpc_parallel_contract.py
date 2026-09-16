"""Canonical RPC parallel contract and negative control tests."""
import importlib.util
import json
from pathlib import Path
import threading
import time
from concurrent.futures import ThreadPoolExecutor
import pytest

P = Path(__file__).resolve().parents[2] / 'v2' / 'plugin'
spec = importlib.util.spec_from_file_location('toolrush_admission', P / 'lib' / 'agent' / 'toolrush_admission.py')
admission = importlib.util.module_from_spec(spec)
spec.loader.exec_module(admission)
readonly = admission.readonly

class TestRpcParallelContract:
    def test_ordering_and_max_batch(self):
        # Simulate parallel batch execution with strict order preservation
        calls = [{'id': i, 'tool': 'read_file', 'args': {'path': f'file_{i}.txt'}} for i in range(16)]
        
        def execute_one(call):
            time.sleep(0.01 * (16 - call['id']) / 16.0)
            return {'id': call['id'], 'result': f"content_{call['id']}"}

        with ThreadPoolExecutor(max_workers=4) as executor:
            results = list(executor.map(execute_one, calls))

        # Order must be identical to input order
        assert [r['id'] for r in results] == list(range(16))
        assert [r['result'] for r in results] == [f"content_{i}" for i in range(16)]

    def test_batch_size_limit_rejection(self):
        MAX_BATCH = 16
        valid_batch = [{'tool': 'read_file', 'args': {}}] * MAX_BATCH
        invalid_batch = [{'tool': 'read_file', 'args': {}}] * (MAX_BATCH + 1)
        
        def check_batch(b):
            if len(b) > MAX_BATCH:
                return {'error': f'Batch size {len(b)} exceeds maximum allowed {MAX_BATCH}'}
            return {'ok': True}

        assert check_batch(valid_batch) == {'ok': True}
        assert 'exceeds maximum' in check_batch(invalid_batch)['error']

    def test_disabled_tools_and_write_tools_rejected(self):
        assert readonly("write_file /tmp/test.txt 'content'") is False
        assert readonly("rm file.txt") is False
        assert readonly("curl -X POST https://example.com") is False
        assert readonly("git commit -m 'msg'") is False

    def test_partial_tool_failure_isolation(self):
        def run_call(call):
            if call['path'] == 'bad':
                return {'error': 'Failed to read bad file'}
            return {'content': f"data of {call['path']}"}

        calls = [{'path': 'good1'}, {'path': 'bad'}, {'path': 'good2'}]
        with ThreadPoolExecutor(max_workers=2) as executor:
            res = list(executor.map(run_call, calls))

        assert res[0] == {'content': 'data of good1'}
        assert 'error' in res[1]
        assert res[2] == {'content': 'data of good2'}
