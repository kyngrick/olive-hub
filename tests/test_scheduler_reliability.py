#!/usr/bin/env python3
"""Regression tests for alarm/timer/reminder routing, persistence and delivery.

Run: .venv\\Scripts\\python.exe -m unittest tests.test_scheduler_reliability -v
"""
from __future__ import annotations

import asyncio
import json
import os
import sys
import tempfile
import time
import unittest
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
os.chdir(ROOT)
import olive_hub as oh  # noqa: E402


class FakeSpeech:
    def __init__(self):
        self.said = []

    async def say(self, text):
        self.said.append(text)


def new_scheduler(tmp):
    oh.SCHEDULE_FILE = str(tmp / "schedule.json")
    oh.SCHEDULER_STATE = str(tmp / "state.json")
    oh.DELIVERY_LOG = str(tmp / "deliveries.jsonl")
    oh.SCHEDULER_TICK_SECONDS = 0.05
    speech = FakeSpeech()
    return oh.Scheduler(speech, path=oh.SCHEDULE_FILE), speech


def fake_ctx(scheduler):
    return SimpleNamespace(
        scheduler=scheduler,
        settings=SimpleNamespace(pending_ttl=60, duck_music=False),
        memory=SimpleNamespace(notes=[], add_note=lambda t: "Noted."),
        device=SimpleNamespace(add_timer=lambda *a, **k: None),
        trace_id="test",
    )


def first_intent(text):
    clean = oh.normalise(text)
    for intent in oh.CUSTOM_INTENTS + oh.INTENTS:
        if intent.name not in oh.DISABLED_INTENTS and intent.match(clean):
            return intent.name
    return None


class RoutingTests(unittest.TestCase):
    def test_alarm_timer_reminder_separation(self):
        cases = {
            "set alarm for 7 AM": "alarm.set",
            "wake me at 6 tomorrow": "alarm.set",
            "set an alarm for tomorrow at 8 am": "alarm.set",
            "set timer for 10 minutes": "timer.set",
            "set timer for 30 seconds": "timer.set",
            "remind me in 10 minutes": "reminder.set",
            "set a reminder for 10 minutes to check the oven": "reminder.set",
            "remind me tomorrow at 9": "reminder.set",
            "what time is it?": "time",
            "what are my reminders?": "reminder.list",
        }
        for text, expected in cases.items():
            self.assertEqual(first_intent(text), expected, text)

    def test_alarm_does_not_create_timer_record(self):
        with tempfile.TemporaryDirectory() as d:
            sched, _ = new_scheduler(Path(d))
            ctx = fake_ctx(sched)
            reply = asyncio.run(oh.h_alarm_set(
                "set alarm for tomorrow at 8 am", None, ctx))
            self.assertEqual(reply.intent, "alarm.set")
            kinds = [i.kind for i in sched.list()]
            self.assertIn("alarm", kinds)
            self.assertNotIn("timer", kinds)

    def test_timer_does_not_create_alarm_record(self):
        with tempfile.TemporaryDirectory() as d:
            sched, _ = new_scheduler(Path(d))
            ctx = fake_ctx(sched)
            reply = asyncio.run(oh.h_timer_set(
                "set a timer for 10 minutes", None, ctx))
            self.assertEqual(reply.intent, "timer.set")
            kinds = [i.kind for i in sched.list()]
            self.assertIn("timer", kinds)
            self.assertNotIn("alarm", kinds)


class SchedulerTests(unittest.TestCase):
    def test_delivery_and_no_duplicate(self):
        with tempfile.TemporaryDirectory() as d:
            sched, speech = new_scheduler(Path(d))
            item = asyncio.run(sched.add("reminder", time.time() + 0.1,
                                         "test", "Reminder: check the oven."))

            async def run():
                await sched.start()
                await asyncio.sleep(0.5)
                await sched.stop()
            asyncio.run(run())
            self.assertEqual(speech.said, ["Reminder: check the oven."])
            self.assertEqual(sched.list(), [])
            rows = [json.loads(x) for x in open(oh.DELIVERY_LOG) if x.strip()]
            self.assertEqual(len(rows), 1)
            self.assertTrue(rows[0]["delivered"])
            self.assertEqual(rows[0]["trace_id"], item.trace_id)

    def test_restart_recovery_once(self):
        with tempfile.TemporaryDirectory() as d:
            tmp = Path(d)
            sched, _ = new_scheduler(tmp)
            asyncio.run(sched.add("reminder", time.time() - 120,
                                  "missed", "Reminder: missed item."))
            asyncio.run(sched.stop())
            # Simulate a fresh hub loading the persisted file.
            sched2, speech2 = new_scheduler(tmp)
            self.assertEqual(len(sched2.list()), 1)

            async def run2():
                await sched2.start()
                await asyncio.sleep(0.5)
                await sched2.stop()
            asyncio.run(run2())
            self.assertEqual(len(speech2.said), 1)
            self.assertTrue(speech2.said[0].startswith("Sorry to interrupt."))
            rows = [json.loads(x) for x in open(oh.DELIVERY_LOG) if x.strip()]
            self.assertEqual(len(rows), 1)

    def test_persistence_across_instances(self):
        with tempfile.TemporaryDirectory() as d:
            tmp = Path(d)
            sched, _ = new_scheduler(tmp)
            item = asyncio.run(sched.add("alarm", time.time() + 3600,
                                         "alarm test", "Alarm test."))
            sched2, _ = new_scheduler(tmp)
            found = [i for i in sched2.list() if i.id == item.id]
            self.assertEqual(len(found), 1)
            self.assertEqual(found[0].trace_id, item.trace_id)


if __name__ == "__main__":
    unittest.main(verbosity=2)
