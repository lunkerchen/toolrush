"""Contract tests that exercise the real execute_read_batch RPC.

Every case drives ``tools.toolrush_rpc.execute_read_batch`` directly; nothing
here reimplements batching, ordering, or budget logic.
"""
import json
import sys
import threading
import time
import types

import pytest

ALLOWED = frozenset({'read_file', 'search_files', 'web_search', 'web_extract'})


def _config_module(**overrides):
    section = {'enabled': True, 'parallel_reads': True}
    section.update(overrides)
    module = types.ModuleType('hermes_cli.config')
    module.load_config_readonly = lambda: {'toolrush': section}
    return module


@pytest.fixture(autouse=True)
def parallel_reads_enabled(monkeypatch):
    """toolrush_runtime.enabled() reads Hermes config; supply an enabled one."""
    monkeypatch.delenv('TOOLRUSH_PARALLEL', raising=False)
    monkeypatch.setitem(sys.modules, 'hermes_cli.config', _config_module())


class RecordingDispatch:
    """Callable stand-in for the tool handler, with per-tool behavior."""

    def __init__(self, handler=None, delays=None):
        self.handler = handler or (lambda tool, args: {'tool': tool, 'args': args})
        self.delays = delays or {}
        self.calls = []
        self._lock = threading.Lock()

    def __call__(self, tool, args):
        with self._lock:
            self.calls.append((tool, args))
        delay = self.delays.get(args.get('path') or args.get('query'))
        if delay:
            time.sleep(delay)
        result = self.handler(tool, args)
        return result if isinstance(result, str) else json.dumps(result)


def run_batch(calls, dispatch, *, counter=None, budget=64, stop_event=None,
              log=None, allowed=ALLOWED):
    from tools.toolrush_rpc import execute_read_batch
    counter = [0] if counter is None else counter
    log = [] if log is None else log
    raw = execute_read_batch(
        {'calls': calls},
        allowed_tools=allowed,
        counter=counter,
        budget=budget,
        log=log,
        dispatch=dispatch,
        stop_event=stop_event or threading.Event(),
    )
    return json.loads(raw), counter, log


def read(path):
    return {'tool': 'read_file', 'args': {'path': path}}


class TestValidBatch:
    def test_order_durations_and_log(self, toolrush_rpc):
        # Slowest call first: completion order must not leak into results.
        paths = [f'f{i}.txt' for i in range(5)]
        dispatch = RecordingDispatch(delays={'f0.txt': 0.05})
        calls = [read(p) for p in paths[:-1]]
        calls.append({'tool': 'search_files', 'args': {'query': 'needle'}})

        results, counter, log = run_batch(calls, dispatch)

        assert [r['args']['path'] for r in results[:-1]] == paths[:-1]
        assert results[-1]['tool'] == 'search_files'
        assert counter == [len(calls)]

        assert [entry['tool'] for entry in log] == ['read_file'] * 4 + ['search_files']
        assert all(entry['batch'] is True for entry in log)
        assert log[0]['args_preview'] == str({'path': 'f0.txt'})[:80]
        assert log[0]['duration'] >= 0.04
        assert all(isinstance(entry['duration'], float) for entry in log)

    def test_single_call_skips_the_pool(self, toolrush_rpc):
        dispatch = RecordingDispatch()
        results, counter, log = run_batch([read('solo.txt')], dispatch)
        assert results == [{'tool': 'read_file', 'args': {'path': 'solo.txt'}}]
        assert counter == [1] and len(log) == 1


class TestBatchSizeLimits:
    @pytest.mark.parametrize('calls', [[], [read('f.txt')] * 17, 'not-a-list', None])
    def test_rejected(self, toolrush_rpc, calls):
        from tools.toolrush_rpc import execute_read_batch, MAX_BATCH
        dispatch = RecordingDispatch()
        counter = [0]
        raw = execute_read_batch(
            {'calls': calls}, allowed_tools=ALLOWED, counter=counter, budget=64,
            log=[], dispatch=dispatch, stop_event=threading.Event(),
        )
        assert json.loads(raw)['error'] == f'parallel requires 1..{MAX_BATCH} read calls'
        assert counter == [0] and dispatch.calls == []

    def test_max_batch_accepted(self, toolrush_rpc):
        from tools.toolrush_rpc import MAX_BATCH
        dispatch = RecordingDispatch()
        calls = [read(f'f{i}.txt') for i in range(MAX_BATCH)]
        results, counter, _ = run_batch(calls, dispatch)
        assert len(results) == MAX_BATCH and counter == [MAX_BATCH]


class TestToolRejection:
    @pytest.mark.parametrize('tool', ['write_file', 'terminal', 'rm', 'edit_file'])
    def test_non_read_tool(self, toolrush_rpc, tool):
        dispatch = RecordingDispatch()
        counter = [0]
        results, counter, log = run_batch(
            [read('ok.txt'), {'tool': tool, 'args': {}}], dispatch, counter=counter)
        assert results['error'] == f'Tool {tool!r} is not an enabled parallel read tool'
        assert counter == [0] and dispatch.calls == [] and log == []

    def test_read_tool_outside_allowed_tools(self, toolrush_rpc):
        dispatch = RecordingDispatch()
        results, counter, _ = run_batch(
            [{'tool': 'web_search', 'args': {'query': 'x'}}], dispatch,
            allowed={'read_file'})
        assert results['error'] == "Tool 'web_search' is not an enabled parallel read tool"
        assert counter == [0] and dispatch.calls == []

    @pytest.mark.parametrize('call, message', [
        ('read_file', 'Each parallel call must be a tool/args object'),
        ({'tool': 'read_file'}, 'Each parallel call requires an args object'),
        ({'tool': 'read_file', 'args': []}, 'Each parallel call requires an args object'),
    ])
    def test_malformed_call(self, toolrush_rpc, call, message):
        dispatch = RecordingDispatch()
        results, counter, _ = run_batch([call], dispatch)
        assert results['error'] == message
        assert counter == [0] and dispatch.calls == []


class TestBudget:
    def test_batch_refused_whole(self, toolrush_rpc):
        dispatch = RecordingDispatch()
        counter = [15]
        results, counter, log = run_batch(
            [read('a.txt'), read('b.txt')], dispatch, counter=counter, budget=16)
        assert results['error'] == 'Tool call limit reached (16); entire batch refused'
        assert counter == [15] and dispatch.calls == [] and log == []

    def test_batch_exactly_at_budget_runs(self, toolrush_rpc):
        dispatch = RecordingDispatch()
        counter = [14]
        results, counter, _ = run_batch(
            [read('a.txt'), read('b.txt')], dispatch, counter=counter, budget=16)
        assert len(results) == 2 and counter == [16]


class CountingEvent:
    """is_set() is False for the first `false_for` probes, then True."""

    def __init__(self, false_for):
        self.false_for = false_for
        self.probes = 0
        self._lock = threading.Lock()

    def is_set(self):
        with self._lock:
            self.probes += 1
            return self.probes > self.false_for


class TestStopEvent:
    def test_interrupted_before_dispatch(self, toolrush_rpc):
        dispatch = RecordingDispatch()
        stop = threading.Event()
        stop.set()
        results, counter, log = run_batch(
            [read('a.txt'), read('b.txt')], dispatch, stop_event=stop)
        assert results['error'] == 'Tool batch interrupted before dispatch'
        assert counter == [0] and dispatch.calls == [] and log == []

    def test_interrupted_during_dispatch(self, toolrush_rpc):
        # One probe passes the pre-dispatch gate, one lets a single worker run.
        dispatch = RecordingDispatch()
        calls = [read(f'f{i}.txt') for i in range(4)]
        results, counter, log = run_batch(
            calls, dispatch, stop_event=CountingEvent(false_for=2))

        interrupted = [r for r in results if r == {'error': 'Tool batch interrupted'}]
        assert len(interrupted) == 3
        assert len(dispatch.calls) == 1
        # The batch is still reserved and logged in full, in input order.
        assert counter == [4]
        assert [entry['args_preview'] for entry in log] == [
            str({'path': f'f{i}.txt'})[:80] for i in range(4)]


class TestPartialFailureIsolation:
    def test_raising_and_failing_calls_are_contained(self, toolrush_rpc):
        def handler(tool, args):
            path = args['path']
            if path == 'boom.txt':
                raise RuntimeError('disk on fire')
            if path == 'missing.txt':
                return {'error': 'no such file'}
            return {'content': f'data of {path}'}

        paths = ['good1.txt', 'boom.txt', 'missing.txt', 'good2.txt']
        dispatch = RecordingDispatch(handler, delays={'good1.txt': 0.03})
        results, counter, log = run_batch([read(p) for p in paths], dispatch)

        assert results[0] == {'content': 'data of good1.txt'}
        assert results[1] == {'error': 'disk on fire'}
        assert results[2] == {'error': 'no such file'}
        assert results[3] == {'content': 'data of good2.txt'}
        assert counter == [4]
        assert [entry['args_preview'] for entry in log] == [
            str({'path': p})[:80] for p in paths]

    def test_non_json_dispatch_output_passes_through(self, toolrush_rpc):
        dispatch = RecordingDispatch(lambda tool, args: 'plain text body')
        results, _, _ = run_batch([read('a.txt'), read('b.txt')], dispatch)
        assert results == ['plain text body', 'plain text body']
