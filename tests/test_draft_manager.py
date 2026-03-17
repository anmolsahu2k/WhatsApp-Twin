"""Tests for multi-draft manager."""

from whatsapp_twin.generator.draft_manager import DraftManager, DraftSet


# --- DraftSet ---

def test_draft_set_basic():
    ds = DraftSet(contact_name="Manav")
    ds.add("draft 1")
    ds.add("draft 2")
    assert ds.count == 2
    assert ds.current_draft == "draft 1"
    assert ds.current_index == 0


def test_draft_set_cycle_next():
    ds = DraftSet(contact_name="Manav")
    ds.add("d1")
    ds.add("d2")
    ds.add("d3")

    assert ds.next() == "d2"
    assert ds.next() == "d3"
    assert ds.next() == "d1"  # wraps around


def test_draft_set_cycle_previous():
    ds = DraftSet(contact_name="Manav")
    ds.add("d1")
    ds.add("d2")
    ds.add("d3")

    assert ds.previous() == "d3"  # wraps backward
    assert ds.previous() == "d2"
    assert ds.previous() == "d1"


def test_draft_set_empty():
    ds = DraftSet(contact_name="Manav")
    assert ds.current_draft is None
    assert ds.next() is None
    assert ds.previous() is None


# --- DraftManager ---

def test_manager_start_new_set():
    dm = DraftManager()
    ds = dm.start_new_set("Manav", "first draft")

    assert dm.has_active_set
    assert ds.count == 1
    assert ds.current_draft == "first draft"


def test_manager_add_variant():
    dm = DraftManager()
    dm.start_new_set("Manav", "d1")
    dm.add_variant("d2")

    assert dm.current_set.count == 2


def test_manager_max_drafts():
    dm = DraftManager()
    dm.start_new_set("Manav", "d1")
    dm.add_variant("d2")
    dm.add_variant("d3")
    dm.add_variant("d4")  # should be ignored (max 3)

    assert dm.current_set.count == 3


def test_manager_should_generate_variant():
    dm = DraftManager()
    assert not dm.should_generate_variant("Manav")  # no set yet

    dm.start_new_set("Manav", "d1")
    assert dm.should_generate_variant("Manav")  # same contact, room for more
    assert not dm.should_generate_variant("Rahul")  # different contact

    dm.add_variant("d2")
    dm.add_variant("d3")
    assert not dm.should_generate_variant("Manav")  # full


def test_manager_cycle():
    dm = DraftManager()
    dm.start_new_set("Manav", "d1")
    dm.add_variant("d2")
    dm.add_variant("d3")

    assert dm.cycle_next() == "d2"
    assert dm.cycle_next() == "d3"
    assert dm.cycle_next() == "d1"


def test_manager_clear():
    dm = DraftManager()
    dm.start_new_set("Manav", "d1")
    dm.clear()
    assert not dm.has_active_set
    assert dm.cycle_next() is None


def test_manager_status_text():
    dm = DraftManager()
    assert dm.status_text() == ""

    dm.start_new_set("Manav", "d1")
    assert dm.status_text() == "1/1"

    dm.add_variant("d2")
    assert dm.status_text() == "1/2"

    dm.cycle_next()
    assert dm.status_text() == "2/2"


def test_new_set_replaces_old():
    dm = DraftManager()
    dm.start_new_set("Manav", "old draft")
    dm.start_new_set("Rahul", "new draft")

    assert dm.current_set.contact_name == "Rahul"
    assert dm.current_set.count == 1
