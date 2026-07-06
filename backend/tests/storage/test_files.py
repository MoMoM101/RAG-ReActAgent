from pathlib import Path

from storage.files import delete_file, save_upload


def test_save_and_delete(tmp_path, monkeypatch):
    monkeypatch.setattr("storage.files.UPLOAD_DIR", tmp_path)
    path = save_upload(b"hello world", "test.txt")
    assert Path(path).exists()
    assert Path(path).read_bytes() == b"hello world"

    delete_file(path)
    assert not Path(path).exists()

def test_duplicate_filename(tmp_path, monkeypatch):
    monkeypatch.setattr("storage.files.UPLOAD_DIR", tmp_path)
    path1 = save_upload(b"content1", "test.txt")
    path2 = save_upload(b"content2", "test.txt")
    assert path1 != path2
    assert Path(path1).read_bytes() == b"content1"
    assert Path(path2).read_bytes() == b"content2"
