"""Regression scenarios for release alignment, revisions and complete history."""
import pytest
from fxmacrodata_public.research import align_macro, macro_events, timestamp_ns, PointInTimeError
from fxmacrodata_public.pagination import collect_history, iter_pages, IncompleteHistoryError, DatasetChangedError


def vintage(value, when):
    return {"val": value, "epoch": timestamp_ns(when) // 10**9, "vintage_status": "source_vintage",
            "publication_time_status": "confirmed", "publication_time_precision": "second"}


def test_reference_period_never_backdates_publication_and_revision():
    rows = [{"date": "2026-01-01", "val": 3.4,
             "revisions": [vintage(3.6, "2026-02-15T08:00:00Z"), vintage(3.4, "2026-09-01T08:00:00Z")]}]
    answer = align_macro(rows, ["2026-01-20", "2026-02-15T08:00:00Z", "2026-03-01", "2026-09-02"])
    assert [r["val"] if r else None for r in answer] == [None, None, 3.6, 3.4]


def test_mutable_head_cannot_override_revision_ledger():
    rows = [{"date": "2026-01-01", **vintage(3.4, "2026-02-15T08:00:00Z"),
             "revisions": [vintage(3.6, "2026-02-15T08:00:00Z"), vintage(3.4, "2026-09-01T08:00:00Z")]}]
    assert align_macro(rows, ["2026-03-01"])[0]["val"] == 3.6


def test_intermediate_head_timestamp_cannot_backdate_a_future_ledger_value():
    first = vintage(3, "2026-01-05T00:00:00Z")
    future = vintage(4, "2026-01-20T00:00:00Z")
    row = {"date": "2025-12-01", **vintage(4, "2026-01-10T00:00:00Z"), "revisions": [first, future]}
    assert align_macro([row], ["2026-01-15"])[0]["val"] == 3


def test_matching_public_head_cannot_overwrite_ledger_ancillary_fields():
    ledger = {**vintage(3, "2026-01-01T00:00:00Z"), "change": 1,
              "effective_date": "2026-01-02", "source": "original", "unit": "Millions"}
    row = {"date": "2025-12-01", **vintage(3, "2026-01-01T00:00:00Z"),
           "change": 99, "effective_date": "2026-02-01", "source": "current",
           "unit": "Persons", "revisions": [ledger]}
    selected = align_macro([row], ["2026-01-02"])[0]
    assert selected["change"] == 1
    assert selected["effective_date"] == "2026-01-02"
    assert selected["source"] == "original"
    assert selected["unit"] == "Millions"


def test_matching_legacy_ledger_can_gain_only_missing_publication_proof():
    proof = vintage(3, "2026-01-01T00:00:00Z")
    ledger = {"val": 3, "epoch": proof["epoch"], "change": 1, "source": "original"}
    row = {"date": "2025-12-01", **proof, "change": 99,
           "source": "current", "effective_date": "2026-02-01", "revisions": [ledger]}
    selected = align_macro([row], ["2026-01-02"])[0]
    assert selected["change"] == 1 and selected["source"] == "original"
    assert "effective_date" not in selected
    assert "publication_time_status" not in ledger  # Input remains unchanged.


def test_head_cannot_replace_existing_unconfirmed_ledger_proof():
    proof = vintage(3, "2026-01-01T00:00:00Z")
    ledger = {**proof, "publication_time_status": "unknown"}
    with pytest.raises(PointInTimeError):
        macro_events([{"date": "2025-12-01", **proof, "revisions": [ledger]}])


def test_capture_tie_prefers_ledger_but_later_head_uses_own_clock():
    first, later = str(timestamp_ns("2026-01-01")), str(timestamp_ns("2026-02-01"))
    ledger = {"val": 3, "observed_at_ns": first, "change": 1}
    row = {"date": "2025-12-01", "val": 3, "observed_at_ns": first,
           "change": 99, "revisions": [ledger]}
    assert align_macro([row], ["2026-01-02"], availability="captured")[0]["change"] == 1
    row["observed_at_ns"] = later
    selected = align_macro([row], ["2026-01-02", "2026-02-02"], availability="captured")
    assert [item["change"] for item in selected] == [1, 99]


@pytest.mark.parametrize("availability", ["public", "captured"])
def test_replay_flag_cannot_backdate_actual_value_evidence(availability):
    row = {"date": "2026-01-01", **vintage(3, "2026-09-01T00:00:00Z"),
           "observed_at_ns": str(timestamp_ns("2026-09-01")),
           "replay_vintage_verified": True, "availability": availability,
           "selected_vintage_known_at_ns": str(timestamp_ns("2026-01-01"))}
    with pytest.raises(PointInTimeError):
        align_macro([row], ["2026-03-01"], availability=availability)


def test_verified_replay_row_recomputes_the_same_source_boundary():
    row = {"date": "2026-01-01", **vintage(3, "2026-02-01T00:00:00Z"),
           "replay_vintage_verified": True, "availability": "public",
           "selected_vintage_known_at_ns": str(timestamp_ns("2026-02-01") + 999999999)}
    assert align_macro([row], ["2026-03-01"])[0]["val"] == 3


def test_source_value_mode_requires_server_confirmation_on_every_page():
    with pytest.raises(IncompleteHistoryError, match="value_mode=source"):
        collect_history(lambda query: page([{"val": query["offset"]}]), {"value_mode": "source"})
    with pytest.raises(IncompleteHistoryError, match="value_mode=source"):
        collect_history(lambda query: {**page([{"val": query["offset"]}], query["offset"]),
                                       **({"value_mode": "source", "value_metadata": {"normalization_applied": False}}
                                          if query["offset"] == 0 else {})},
                        {"value_mode": "source"})


@pytest.mark.parametrize("applied", [True, "false", None])
def test_source_value_mode_rejects_unconfirmed_normalization(applied):
    with pytest.raises(IncompleteHistoryError, match="value_mode=source"):
        collect_history(lambda query: {**page([], total=0), "value_mode": "source",
                                       "value_metadata": {"normalization_applied": applied}},
                        {"value_mode": "source"})


def test_unrecognized_head_fields_are_not_research_features():
    row = {"date": "2026-01-01", **vintage(3, "2026-02-01T00:00:00Z"),
           "inflation_mom": 99, "new_forecast_field": 88, "change": .2}
    result = align_macro([row], ["2026-03-01"])[0]
    assert not {"inflation_mom", "new_forecast_field"}.intersection(result)
    assert result["change"] == .2


def test_realized_values_keep_eligibility_beside_legacy_forecast_metadata():
    row = {"date": "2026-01-01", **vintage(3, "2026-02-01T00:00:00Z"),
           "prediction_type": "fxmacrodata_prediction", "prediction_class": "model_forecast",
           "predicted_value": 99, "forecast_snapshot": {"val": 88}}
    selected = align_macro([row], ["2026-03-01"])[0]
    assert selected["val"] == 3
    assert not {"prediction_type", "prediction_class", "predicted_value", "forecast_snapshot"}.intersection(selected)
    row.pop("val")
    with pytest.raises(PointInTimeError):
        macro_events([row])


def test_old_period_revision_does_not_replace_newer_period():
    rows = [{"date": "2026-01-01", "revisions": [vintage(1, "2026-02-01T00:00:00Z"), vintage(2, "2026-04-01T00:00:00Z")]},
            {"date": "2026-02-01", **vintage(4, "2026-03-01T00:00:00Z")}]
    assert align_macro(rows, ["2026-04-02"])[0]["val"] == 4


def test_selected_vintage_does_not_inherit_head_changes_or_forecasts():
    rows = [{"date": "2026-01-01", "val": 3.4, "pct_change": 99, "forecast_snapshot": {"val": 88},
             "previous_value": 77, "revisions": [vintage(3.6, "2026-02-15T08:00:00Z")]}]
    result = align_macro(rows, ["2026-03-01"])[0]
    assert result["val"] == 3.6
    assert not {"pct_change", "forecast_snapshot", "previous_value"}.intersection(result)


def test_conflicting_same_time_value_is_unavailable():
    a, b = vintage(1, "2026-02-01T00:00:00Z"), vintage(2, "2026-02-01T00:00:00Z")
    assert align_macro([{"date": "2026-01-01", "revisions": [a, b, a]}], ["2026-03-01"]) == [None]


@pytest.mark.parametrize("fields", [{}, {"announcement_datetime": 1700000000},
    {"vintage_status": "latest_snapshot", "observed_at_ns": 1},
    {**vintage(1, "2026-02-01T00:00:00Z"), "publication_time_precision": "date"},
    {**vintage(1, "2026-02-01T00:00:00Z"), "release_time_assumed": True}])
def test_unknown_or_unverified_clocks_fail_closed(fields):
    with pytest.raises(PointInTimeError):
        macro_events([{"date": "2026-01-01", "val": 1, **fields}])


def test_captured_vintage_uses_its_own_clock_and_delay():
    rows = [{"date": "2026-01-01", "val": 99, "first_observed_at_ns": str(timestamp_ns("2026-01-01")),
             "revisions": [{"val": 3, "observed_at_ns": str(timestamp_ns("2026-02-01"))}]}]
    assert align_macro(rows, ["2026-01-31", "2026-02-01", "2026-02-03"], availability="captured", delay_seconds=86400)[2]["val"] == 3
    assert align_macro(rows, ["2026-02-02"], availability="captured", max_age_days=.5) == [None]


def page(rows, offset=0, total=2, version="one"):
    more = offset + len(rows) < total
    return {"data": rows, "replay": {"dataset_version": version}, "source": "fixture",
            "pagination": {"offset": offset, "returned_count": len(rows), "total_count": total,
                           "has_more": more, "next_offset": offset + len(rows) if more else None}}


def test_complete_pages_preserve_metadata_sort_and_pin_version():
    calls = []
    def fetch(params):
        calls.append(params)
        offset = params["offset"]
        return page([{"date": "2026-02-01" if offset == 0 else "2026-01-01", "val": offset}], offset)
    result = collect_history(fetch, {"as_of": "2026-03-01"})
    assert result["complete"] and len(result["data"]) == 2
    assert result["data"][0]["date"] == "2026-01-01"
    assert calls[1]["dataset_version"] == "one"
    assert result["pages"][0]["source"] == "fixture"


def test_page_failure_gives_resume_checkpoint_and_no_partial_list():
    def fetch(params):
        if params["offset"]:
            raise RuntimeError("offline")
        return page([{"val": 1}])
    with pytest.raises(IncompleteHistoryError) as error:
        collect_history(fetch)
    assert error.value.resume["next_offset"] == 1
    assert error.value.resume["dataset_version"] == "one"
    assert error.value.resume["complete"] is False
    continued = list(iter_pages(lambda params: page([{"val": 2}], 1), resume=error.value.resume))
    assert continued[0]["client_resume"]["complete"]


def test_changed_version_and_repeated_page_raise():
    with pytest.raises(DatasetChangedError):
        collect_history(lambda p: page([{"val": p["offset"]}], p["offset"], version=str(p["offset"])))
    with pytest.raises(IncompleteHistoryError, match="repeated"):
        collect_history(lambda p: page([{"val": 1}], p["offset"]))


def test_budget_exhaustion_and_truncated_final_page_raise():
    with pytest.raises(IncompleteHistoryError, match="max_pages"):
        collect_history(lambda p: page([{"val": p["offset"]}], p["offset"]), max_pages=1)
    bad = page([{"val": 1}]); bad["pagination"].update(has_more=False, next_offset=None)
    with pytest.raises(IncompleteHistoryError):
        collect_history(lambda _: bad)
