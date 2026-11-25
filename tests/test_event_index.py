import time

from hook_detector.event_index import Events


def test_engagement_counts_count_ready_rising_edges_per_camera(tmp_path):
    events = Events(tmp_path)
    events.add("Bay-01-Camera-1", 90, "HOOK_NOT_ENGAGED", False)
    events.add("Bay-01-Camera-1", 100, "HOOK_ENGAGED_TO_LOCK", True)
    events.add("Bay-01-Camera-1", 110, "HOOK_OUTSIDE_LUG", True)
    events.add("Bay-01-Camera-1", 120, "HOOK_NOT_ENGAGED_TO_LOCK", False)
    events.add("Bay-01-Camera-1", 150, "HOOK_ENGAGED_TO_LOCK", True)
    events.add("Bay-02-Camera-1", 125, "HOOK_ENGAGED_TO_LOCK", True)

    assert events.engagement_counts(100, 160) == {
        "Bay-01-Camera-1": 2,
        "Bay-02-Camera-1": 1,
    }


def test_transition_log_classifies_start_end_and_filters_by_camera(tmp_path):
    events = Events(tmp_path)
    events.add("Bay-01-Camera-1", 10, "HOOK_NOT_ENGAGED", False)
    events.add("Bay-01-Camera-1", 20, "HOOK_ENGAGED_TO_LOCK", True)
    events.add("Bay-01-Camera-1", 25, "HOOK_INSIDE_LUG", True)
    events.add("Bay-01-Camera-1", 30, "HOOK_NOT_ENGAGED_TO_LOCK", False)
    events.add("Bay-02-Camera-1", 35, "HOOK_ENGAGED_TO_LOCK", True)

    results = events.transitions(15, 40, camera="Bay-01-Camera-1", limit=10)

    assert [row["event_type"] for row in results] == [
        "engagement_ended",
        "state_changed",
        "engagement_started",
    ]
    assert all(row["camera_id"] == "Bay-01-Camera-1" for row in results)
    assert results[0]["timestamp"] == 30


def test_transition_log_filters_multiple_bays(tmp_path):
    events = Events(tmp_path)
    now = time.time()
    events.add("Bay-01-Camera-1", now + 10, "HOOK_ENGAGED_TO_LOCK", True)
    events.add("Bay-04-Camera-1", now + 20, "HOOK_ENGAGED_TO_LOCK", True)
    events.add("Bay-05-Camera-1", now + 30, "HOOK_ENGAGED_TO_LOCK", True)

    results = events.transitions(now, now + 40, bay_ids=("Bay-01", "Bay-04"), limit=10)

    assert [row["camera_id"] for row in results] == [
        "Bay-04-Camera-1",
        "Bay-01-Camera-1",
    ]
