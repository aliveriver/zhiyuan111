import pytest

from x2_ps5_teleop.bridge.hand_poses import HandPoseStore, validate_positions


def pose(name="右手抓握"):
    return {"name": name, "side": "right", "positions": [0.2] * 10,
            "requires_confirmation": False}


def test_pose_library_preserves_trajectories_and_roundtrips(tmp_path):
    trajectory = tmp_path / "trajectories.json"
    trajectory.write_bytes(b'{"existing":[{"t_ms":42}]}')
    original = trajectory.read_bytes()
    path = tmp_path / "hand_poses.json"
    store = HandPoseStore(path)
    store.save(pose())
    with pytest.raises(ValueError, match="不会覆盖"):
        store.save(pose())
    store.save({**pose("松开"), "requires_confirmation": True})
    store.rename("右手抓握", "单手携带")
    reloaded = HandPoseStore(path)
    assert reloaded.poses["松开"]["requires_confirmation"]
    assert reloaded.poses["单手携带"]["positions"] == [0.2] * 10
    reloaded.delete("松开")
    assert len(HandPoseStore(path).poses) == 1
    assert trajectory.read_bytes() == original


@pytest.mark.parametrize("positions", [[0.0] * 9, [True] * 10, [float("nan")] * 10,
                                      [float("inf")] * 10, [4.0] * 10, ["0"] * 10])
def test_rejects_invalid_hand_positions(positions):
    with pytest.raises(ValueError):
        validate_positions(positions)


def test_signed_measured_positions_are_not_mirrored_or_clamped():
    values = [-0.3, 0.2, -0.1, 0, 0, 0, 0, 0, 0, 1.00003173828125]
    assert validate_positions(values) == values


def test_corrupt_pose_library_is_not_silently_replaced(tmp_path):
    path = tmp_path / "hand_poses.json"
    path.write_text('{"version": 1, broken')
    with pytest.raises(ValueError):
        HandPoseStore(path)
    assert path.read_text() == '{"version": 1, broken'


def test_failed_save_leaves_in_memory_library_unchanged(tmp_path, monkeypatch):
    store = HandPoseStore(tmp_path / "hand_poses.json")
    store.save(pose())
    def fail(*args):
        raise OSError("disk full")
    monkeypatch.setattr("x2_ps5_teleop.bridge.hand_poses.os.replace", fail)
    with pytest.raises(OSError):
        store.rename("右手抓握", "new")
    assert list(store.poses) == ["右手抓握"]
    assert len(list(tmp_path.iterdir())) == 1
