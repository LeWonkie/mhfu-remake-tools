"""Cancellation regressions for the atelier GUI; no Tk window or engine process."""
import queue
import threading
import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch

import atelier


class DeferredThread:
    def __init__(self, target, **kwargs):
        self.target = target

    def start(self):
        pass

    def run(self):
        self.target()


class DelayedCore:
    """Pause inside the engine before its active runner becomes visible."""
    def __init__(self):
        self.entered = threading.Event()
        self.publish = threading.Event()
        self.active = threading.Event()
        self.cancelled = threading.Event()
        self.cancel_calls = 0

    def operation(self, **kwargs):
        self.entered.set()
        if not self.publish.wait(2):
            return dict(status="failed", summary="test publication timeout")
        self.active.set()
        if not self.cancelled.wait(2):
            return dict(status="failed", summary="test cancellation timeout")
        return dict(status="incomplete", summary="Action arretee.")

    def cancel(self):
        self.cancel_calls += 1
        if self.active.is_set():
            self.cancelled.set()


class AtelierGuiTests(unittest.TestCase):
    def app(self, core=None):
        app = atelier.AtelierApp.__new__(atelier.AtelierApp)
        app.root = Mock()
        app.core = core or SimpleNamespace(
            cancel=Mock(), operation=Mock(return_value=dict(status="passed", summary="Termine.")))
        app.events = queue.Queue()
        app.busy = False
        app.closing = False
        app.stop_requested = threading.Event()
        app.controls = []
        app.worker = None
        for name in ("status_badge", "summary", "stop_button", "progress", "log"):
            setattr(app, name, Mock())
        app.refresh_references = Mock()
        return app

    def test_stop_before_worker_starts_skips_engine(self):
        app = self.app()
        with patch.object(atelier.threading, "Thread", DeferredThread):
            app._start("Essai", app.core.operation)
            app.stop()
            app.worker.run()
        app._poll()
        app.core.operation.assert_not_called()
        self.assertFalse(app.busy)
        self.assertEqual(app.status_badge.configure.call_args.kwargs["text"], "INCOMPLET")

    def test_close_before_worker_starts_waits_for_incomplete_result(self):
        app = self.app()
        with patch.object(atelier.threading, "Thread", DeferredThread):
            app._start("Essai", app.core.operation)
            app.close()
            app.root.destroy.assert_not_called()
            app.worker.run()
        app._poll()
        app.core.operation.assert_not_called()
        app.root.destroy.assert_called_once()
        self.assertFalse(app.busy)
        self.assertEqual(app.status_badge.configure.call_args.kwargs["text"], "INCOMPLET")

    def test_poll_retries_stop_after_engine_publishes_active_runner(self):
        for closing in (False, True):
            with self.subTest(closing=closing):
                core = DelayedCore()
                app = self.app(core)
                app._start("Essai", core.operation)
                try:
                    self.assertTrue(core.entered.wait(2))
                    app.close() if closing else app.stop()
                    self.assertFalse(core.cancelled.is_set())
                    app._poll()
                    app.root.destroy.assert_not_called()
                    core.publish.set()
                    self.assertTrue(core.active.wait(2))
                    app._poll()
                    self.assertTrue(core.cancelled.is_set())
                    app.worker.join(2)
                    self.assertFalse(app.worker.is_alive())
                    app._poll()
                    self.assertFalse(app.busy)
                    self.assertGreaterEqual(core.cancel_calls, 3)
                    self.assertEqual(app.status_badge.configure.call_args.kwargs["text"], "INCOMPLET")
                    if closing:
                        app.root.destroy.assert_called_once()
                    else:
                        app.root.destroy.assert_not_called()
                    calls_after_finish = core.cancel_calls
                    if not closing:
                        app._poll()
                        self.assertEqual(core.cancel_calls, calls_after_finish)
                finally:
                    core.publish.set()
                    core.cancelled.set()
                    app.worker.join(2)

    def test_stop_during_fast_engine_return_stays_incomplete(self):
        app = self.app()

        def finish_despite_stop(**kwargs):
            app.stop()
            return dict(status="passed", summary="Termine.")

        with patch.object(atelier.threading, "Thread", DeferredThread):
            app._start("Essai rapide", finish_despite_stop)
            app.worker.run()
        app._poll()
        self.assertFalse(app.busy)
        self.assertEqual(app.status_badge.configure.call_args.kwargs["text"], "INCOMPLET")
        calls_after_finish = app.core.cancel.call_count
        app._poll()
        self.assertEqual(app.core.cancel.call_count, calls_after_finish)

    def test_next_action_clears_previous_stop_request(self):
        app = self.app()
        with patch.object(atelier.threading, "Thread", DeferredThread):
            app._start("Premier essai", app.core.operation)
            app.stop()
            app.worker.run()
            app._poll()
            app._start("Second essai", app.core.operation)
            app.worker.run()
        app._poll()
        app.core.operation.assert_called_once()
        self.assertFalse(app.busy)
        self.assertEqual(app.status_badge.configure.call_args.kwargs["text"], "TERMINÉ")


if __name__ == "__main__":
    unittest.main(verbosity=2)
