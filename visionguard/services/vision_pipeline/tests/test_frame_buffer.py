from vision_pipeline.frame_buffer import InMemoryFrameBuffer


def test_evicts_oldest_and_retrieves_by_id():
    b = InMemoryFrameBuffer(3)
    for i in range(1, 6):
        b.put("CAM", i, bytes([i]))
    assert b.get("CAM", 1) is None and b.get("CAM", 2) is None
    assert b.get("CAM", 5) == bytes([5])
    assert b.latest_id("CAM") == 5
    assert sorted(b.get_range("CAM", 3, 5)) == [3, 4, 5]


def test_cameras_are_isolated():
    b = InMemoryFrameBuffer(2)
    b.put("A", 1, b"a")
    b.put("B", 1, b"b")
    assert b.get("A", 1) == b"a" and b.get("B", 1) == b"b" and b.get("C", 1) is None
