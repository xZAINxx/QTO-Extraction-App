"""Phase-7 cost-saver / batch-runner tests.

These exercise the parts that don't require live Anthropic calls:

* ``BatchRunner.run`` polls ``messages.batches.retrieve`` until ``ended``
  and reports progress + ETA along the way.
* ``BatchRequest`` is converted into a properly-shaped ``params`` block
  with cache-control on the system prompt.
* ``AIClient.compose_description`` enqueues silently when
  ``cost_saver_mode`` is on, returns an UPPERCASE placeholder, and
  back-fills the cache from a faked batch result on flush.
* ``Assembler.flush_batched_compose`` upgrades placeholder descriptions
  to the composed text post-flush.
* ``TokenTracker.record_batch`` slots usage into a discounted bucket
  so the meter reflects the 50% saving without inventing fake tokens.
"""
from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from ai.batch_runner import BatchProgress, BatchRequest, BatchRunner
from core.token_tracker import TokenTracker


def _usage(in_tok: int = 100, out_tok: int = 50, cache_read: int = 0, cache_write: int = 0):
    return SimpleNamespace(
        input_tokens=in_tok,
        output_tokens=out_tok,
        cache_read_input_tokens=cache_read,
        cache_creation_input_tokens=cache_write,
    )


def test_batch_progress_human_eta_formats_correctly():
    p = BatchProgress(submitted=10, succeeded=5, eta_s=45.0, status="in_progress")
    assert p.human_eta() == "~45 s"

    p.eta_s = 90.0
    assert p.human_eta() == "~1 min"

    p.eta_s = 4000.0
    assert p.human_eta().endswith(" h")

    p.status = "ended"
    assert p.human_eta() == "complete"

    p.eta_s = None
    p.status = "in_progress"
    assert "calculating" in p.human_eta()


def test_batch_progress_fraction_done_caps_at_one():
    p = BatchProgress(submitted=4, succeeded=10)
    assert p.fraction_done() == 1.0
    p2 = BatchProgress(submitted=4, succeeded=2, errored=1)
    assert p2.fraction_done() == pytest.approx(0.75)


def test_batch_runner_returns_empty_for_empty_request_iterable():
    runner = BatchRunner(MagicMock())
    out = runner.run([])
    assert out == {}


def test_batch_runner_polls_then_returns_results(monkeypatch):
    fake_batch = SimpleNamespace(id="batch_123")

    polls: list[SimpleNamespace] = [
        SimpleNamespace(
            processing_status="in_progress",
            request_counts=SimpleNamespace(processing=2, succeeded=0, errored=0, canceled=0),
        ),
        SimpleNamespace(
            processing_status="in_progress",
            request_counts=SimpleNamespace(processing=1, succeeded=1, errored=0, canceled=0),
        ),
        SimpleNamespace(
            processing_status="ended",
            request_counts=SimpleNamespace(processing=0, succeeded=2, errored=0, canceled=0),
        ),
    ]
    poll_iter = iter(polls)

    result_entries = [
        SimpleNamespace(
            custom_id="r1",
            result=SimpleNamespace(
                type="succeeded",
                message=SimpleNamespace(
                    content=[SimpleNamespace(text="HELLO ONE")],
                    usage=_usage(in_tok=10, out_tok=4),
                    model="claude-sonnet-4-6",
                ),
            ),
        ),
        SimpleNamespace(
            custom_id="r2",
            result=SimpleNamespace(
                type="succeeded",
                message=SimpleNamespace(
                    content=[SimpleNamespace(text="HELLO TWO")],
                    usage=_usage(in_tok=8, out_tok=3),
                    model="claude-sonnet-4-6",
                ),
            ),
        ),
        # Errored entry should be silently ignored (no entry in the dict).
        SimpleNamespace(
            custom_id="r3",
            result=SimpleNamespace(type="errored", message=None),
        ),
    ]

    client = MagicMock()
    client.messages.batches.create.return_value = fake_batch
    client.messages.batches.retrieve.side_effect = lambda _id: next(poll_iter)
    client.messages.batches.results.return_value = result_entries

    monkeypatch.setattr("ai.batch_runner.time.sleep", lambda *_: None)

    progress_history: list[BatchProgress] = []
    usage_records: list[tuple[object, str]] = []

    runner = BatchRunner(client, poll_interval_s=0.01)
    out = runner.run(
        [
            BatchRequest(
                custom_id="r1", model="claude-sonnet-4-6",
                system="sys", messages=[{"role": "user", "content": "a"}],
            ),
            BatchRequest(
                custom_id="r2", model="claude-sonnet-4-6",
                system="sys", messages=[{"role": "user", "content": "b"}],
            ),
            BatchRequest(
                custom_id="r3", model="claude-sonnet-4-6",
                system="sys", messages=[{"role": "user", "content": "c"}],
            ),
        ],
        on_progress=lambda p: progress_history.append(BatchProgress(
            submitted=p.submitted, processing=p.processing,
            succeeded=p.succeeded, errored=p.errored, canceled=p.canceled,
            elapsed_s=p.elapsed_s, eta_s=p.eta_s, status=p.status,
        )),
        record_usage=lambda u, m: usage_records.append((u, m)),
    )

    assert out == {"r1": "HELLO ONE", "r2": "HELLO TWO"}
    assert any(p.status == "ended" for p in progress_history)
    assert len(usage_records) == 2
    create_kwargs = client.messages.batches.create.call_args.kwargs
    payload = create_kwargs["requests"]
    assert len(payload) == 3
    assert payload[0]["params"]["system"][0]["cache_control"] == {"type": "ephemeral"}


def test_batch_runner_returns_empty_on_create_failure(monkeypatch):
    client = MagicMock()
    client.messages.batches.create.side_effect = RuntimeError("boom")
    monkeypatch.setattr("ai.batch_runner.time.sleep", lambda *_: None)
    runner = BatchRunner(client)
    seen: list[BatchProgress] = []
    out = runner.run(
        [BatchRequest(
            custom_id="x", model="m", system="s",
            messages=[{"role": "user", "content": "y"}],
        )],
        on_progress=lambda p: seen.append(BatchProgress(
            submitted=p.submitted, status=p.status,
        )),
    )
    assert out == {}
    assert any(p.status == "failed" for p in seen)


def test_token_tracker_record_batch_uses_discounted_bucket():
    t = TokenTracker()
    t.record_batch(_usage(in_tok=1_000_000, out_tok=0), "claude-sonnet-4-6")
    # Sonnet input is $3/M; batch is $1.5/M.
    assert t.usage.estimated_cost_usd == pytest.approx(1.5, rel=0.01)
    bucket_keys = list(t.usage.by_model.keys())
    assert any(k.endswith("::batch") for k in bucket_keys)


def test_ai_client_cost_saver_queues_compose_and_flushes(monkeypatch):
    """End-to-end cost-saver flow with a stubbed Anthropic client."""
    from ai.client import AIClient

    fake_anthropic = MagicMock()
    monkeypatch.setattr("ai.client.anthropic.Anthropic", lambda **_: fake_anthropic)

    tracker = TokenTracker()
    cfg = {
        "anthropic_api_key": "test",
        "models": {"sonnet": "claude-sonnet-4-6"},
        "cost_saver_mode": True,
    }
    client = AIClient(cfg, tracker)

    placeholder = client.compose_description("install metal flashing", sheet="A-101", keynote_ref="1/A101")
    assert placeholder == "INSTALL METAL FLASHING"
    assert client.pending_compose_count == 1

    client.compose_description("install metal flashing", sheet="A-101", keynote_ref="1/A101")
    assert client.pending_compose_count == 1, "duplicate inputs should de-dupe by cache key"

    captured: dict = {}

    def _fake_run(self, requests, *, on_progress=None, record_usage=None):
        reqs = list(requests)
        captured["count"] = len(reqs)
        # Simulate Anthropic charging us a tiny amount and giving us back text.
        if record_usage:
            record_usage(_usage(in_tok=20, out_tok=10), "claude-sonnet-4-6")
        return {r.custom_id: f"COMPOSED::{r.custom_id}" for r in reqs}

    monkeypatch.setattr("ai.batch_runner.BatchRunner.run", _fake_run)

    filled = client.flush_pending_compose()
    assert filled == 1
    assert captured["count"] == 1
    assert client.pending_compose_count == 0

    # Subsequent compose_description for the same key should now return
    # the real composed text from the cache, not the uppercase fallback.
    composed = client.compose_description("install metal flashing", sheet="A-101", keynote_ref="1/A101")
    assert composed.startswith("COMPOSED::")


def test_ai_client_flush_falls_back_when_batch_returns_nothing(monkeypatch):
    from ai.client import AIClient

    fake_anthropic = MagicMock()
    fake_resp = SimpleNamespace(
        content=[SimpleNamespace(text="SYNC FALLBACK")],
        usage=_usage(),
    )
    fake_anthropic.messages.create.return_value = fake_resp
    monkeypatch.setattr("ai.client.anthropic.Anthropic", lambda **_: fake_anthropic)

    tracker = TokenTracker()
    cfg = {"anthropic_api_key": "test", "cost_saver_mode": True}
    client = AIClient(cfg, tracker)

    client.compose_description("paint wall", sheet="A-102", keynote_ref="1/A102")

    # Stub BatchRunner to return an empty result dict — batch silently failed.
    monkeypatch.setattr(
        "ai.batch_runner.BatchRunner.run",
        lambda self, *a, **kw: {},
    )

    filled = client.flush_pending_compose()
    assert filled == 0
    # After flush, compose_description must return the sync fallback we stubbed.
    assert client.compose_description("paint wall", sheet="A-102", keynote_ref="1/A102") == "SYNC FALLBACK"
    assert fake_anthropic.messages.create.called


def test_assembler_flush_batched_compose_upgrades_descriptions(monkeypatch):
    """Phase-7 integration: Assembler swaps placeholder rows for real descriptions."""
    from core.assembler import Assembler
    from core.qto_row import QTORow

    # Hand-crafted fake AIClient: just enough surface for Assembler to work.
    class FakeAI:
        def __init__(self):
            self.cost_saver_mode = True
            self._cache: dict[str, str] = {}
            self.pending_compose_count = 0
            self.flushed = False

        def compose_description(self, raw, sheet="", keynote_ref=""):
            key = f"{raw}|{sheet}|{keynote_ref}"
            if key in self._cache:
                return self._cache[key]
            return raw.upper()

        def flush_pending_compose(self, on_progress=None):
            self.flushed = True
            # Pretend the batch resolved every queued row.
            self._cache["paint wall|A-102|1/A102"] = "PRIME & PAINT WALL AS PER 1/A102"
            self._cache["install flashing|A-101|2/A101"] = "PROVIDE & INSTALL FLASHING AS PER 2/A101"
            return 2

    ai = FakeAI()
    ai.pending_compose_count = 2
    tracker = TokenTracker()
    asm = Assembler({"units_canonical": {}}, ai, tracker)

    row1 = QTORow(description="PAINT WALL", source_sheet="A-102", source_page=10)
    row2 = QTORow(description="INSTALL FLASHING", source_sheet="A-101", source_page=4)
    # Manually wire the compose context as ``_make_row`` would have done.
    asm._compose_ctx[id(row1)] = ("paint wall", "A-102", "1/A102")
    asm._compose_ctx[id(row2)] = ("install flashing", "A-101", "2/A101")

    upgraded = asm.flush_batched_compose([row1, row2])
    assert upgraded == 2
    assert row1.description.startswith("PRIME & PAINT WALL")
    assert row2.description.startswith("PROVIDE & INSTALL FLASHING")
    assert ai.flushed is True


def test_assembler_flush_no_op_without_cost_saver():
    from core.assembler import Assembler
    from core.qto_row import QTORow

    class FakeAI:
        cost_saver_mode = False
        pending_compose_count = 0

        def flush_pending_compose(self, on_progress=None):
            raise AssertionError("should not be called when cost_saver is off")

    asm = Assembler({"units_canonical": {}}, FakeAI(), TokenTracker())
    rows = [QTORow(description="anything", source_sheet="A-1", source_page=1)]
    assert asm.flush_batched_compose(rows) == 0
