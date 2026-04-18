import json
from pathlib import Path

from src import utils


def _patch_state_files(monkeypatch, tmp_path: Path):
    monkeypatch.setattr(utils, "SEEN_FILE", tmp_path / "seen_articles.json")
    monkeypatch.setattr(utils, "REPLIED_FILE", tmp_path / "replied_to.json")
    monkeypatch.setattr(utils, "RUNTIME_STATE_FILE", tmp_path / "runtime_state.json")


def test_seen_articles_recovers_from_backup_when_primary_is_corrupt(monkeypatch, tmp_path):
    _patch_state_files(monkeypatch, tmp_path)

    primary = utils.SEEN_FILE
    backup = primary.with_suffix(primary.suffix + ".bak")

    backup.write_text(json.dumps({"links": ["https://example.com"], "recent_topics": ["LLMs"]}))
    primary.write_text('{"links": ["incomplete"')

    loaded = utils.load_seen_articles()

    # v4.15: load_seen_articles back-fills pioneer_recent on legacy state.
    assert loaded == {
        "links": ["https://example.com"],
        "recent_topics": ["LLMs"],
        "pioneer_recent": [],
    }


def test_seen_articles_preserves_corrupt_file_and_resets_when_no_valid_backup(monkeypatch, tmp_path):
    _patch_state_files(monkeypatch, tmp_path)

    primary = utils.SEEN_FILE
    backup = primary.with_suffix(primary.suffix + ".bak")

    primary.write_text('{"broken": true')
    backup.write_text('{"also": "broken"')

    loaded = utils.load_seen_articles()

    expected = {"links": [], "recent_topics": [], "pioneer_recent": []}
    assert loaded == expected
    assert primary.exists()
    assert json.loads(primary.read_text()) == expected
    # Corrupt file is saved with a timestamp suffix (e.g. seen_articles.json.corrupt.1234567890)
    corrupt_files = list(primary.parent.glob(primary.name + ".corrupt.*"))
    assert corrupt_files, "Expected a timestamped .corrupt.* file to exist"


def test_replied_to_recovers_from_interrupted_write(monkeypatch, tmp_path):
    _patch_state_files(monkeypatch, tmp_path)

    primary = utils.REPLIED_FILE
    backup = primary.with_suffix(primary.suffix + ".bak")

    backup.write_text(json.dumps(["at://did:plc:1/post/1", "at://did:plc:2/post/2"]))
    primary.write_text('["at://did:plc:1/post/1",')

    loaded = utils.load_replied_to()

    assert loaded == ["at://did:plc:1/post/1", "at://did:plc:2/post/2"]
    assert json.loads(primary.read_text()) == loaded


def test_profile_bio_cooldown_blocks_immediate_redundant_update(monkeypatch, tmp_path):
    _patch_state_files(monkeypatch, tmp_path)

    utils.mark_profile_bio_updated("bluesky", "Bio A")
    should_update = utils.should_update_profile_bio("bluesky", "Bio A", cooldown_hours=168)
    assert should_update is False


def test_profile_bio_cooldown_allows_changed_text(monkeypatch, tmp_path):
    _patch_state_files(monkeypatch, tmp_path)

    utils.mark_profile_bio_updated("mastodon", "Bio A")
    should_update = utils.should_update_profile_bio("mastodon", "Bio B", cooldown_hours=168)
    assert should_update is True
