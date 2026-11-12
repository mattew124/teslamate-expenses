"""The invoice folder on the server (INVOICES_DIR): the storage itself, no app or database.

Everything it is asked for must stay inside the mounted folder, an upload must never replace
a file, and a folder that has gone missing (an unmounted share) is an error, never recreated.
"""

import os
import threading

import pytest

from tmexpenses import storage
from tests.test_validation import PDF


@pytest.fixture()
def root(tmp_path):
    (tmp_path / "invoices" / "Model 3" / "Tyres").mkdir(parents=True)
    (tmp_path / "outside").mkdir()
    (tmp_path / "outside" / "secret.pdf").write_bytes(PDF)
    return tmp_path / "invoices"


@pytest.fixture()
def local(root):
    return storage.LocalFolder(root)


# ---- staying inside the folder ---------------------------------------------------

@pytest.mark.parametrize("bad", [
    "/../outside", "/Model 3/../../outside", "/..", "/Model 3/./Tyres", "//etc", "Model 3", "../outside",
    "/Model 3//Tyres", "/Model 3\x00", 7,
])
def test_paths_that_leave_the_folder_or_are_malformed_are_refused(local, bad):
    with pytest.raises(storage.StorageError):
        local.folder(bad)
    with pytest.raises(storage.StorageError):
        local.download(bad)


def test_an_absolute_path_is_read_inside_the_folder_not_from_the_server(local):
    with pytest.raises(storage.StorageError):
        local.download("/etc/passwd")
    with pytest.raises(storage.StorageError):
        local.folder("/etc")


def test_a_symlink_out_of_the_folder_is_refused_and_not_listed(local, root, tmp_path):
    (root / "escape").symlink_to(tmp_path / "outside", target_is_directory=True)
    (root / "Model 3" / "leak.pdf").symlink_to(tmp_path / "outside" / "secret.pdf")
    with pytest.raises(storage.StorageError):
        local.folder("/escape")
    with pytest.raises(storage.StorageError):
        local.download("/escape/secret.pdf")
    with pytest.raises(storage.StorageError):
        local.download("/Model 3/leak.pdf")
    with pytest.raises(storage.StorageError):
        local.upload("/escape", "x.pdf", PDF)
    assert [f["name"] for f in local.subfolders(None)] == ["Model 3"]
    assert local.pdfs("/") == []
    assert not (tmp_path / "outside" / "x.pdf").exists()


def test_a_symlink_that_stays_inside_is_followed(local, root):
    (root / "Current").symlink_to(root / "Model 3", target_is_directory=True)
    assert local.folder("/Current")["id"] == "/Model 3"


def test_odd_but_legal_names_work(local, root):
    name = "Çar & Söhne (2026) #1 100%"
    made = local.create_folder("/", name)
    assert made["id"] == f"/{name}" and (root / name).is_dir()
    ref = local.upload(made["id"], "261107 Tyres - O'Brien & Co.pdf", PDF)
    assert local.download(ref) == PDF


# ---- folders ----------------------------------------------------------------------

def test_the_root_and_a_folder_below_it(local):
    assert local.folder(None) == {"id": "/", "name": "/", "path": "/", "parent_id": None, "is_root": True}
    assert local.folder("/Model 3/Tyres") == {"id": "/Model 3/Tyres", "name": "Tyres", "path": "/Model 3/Tyres",
                                              "parent_id": "/Model 3", "is_root": False}
    assert local.folder("/Model 3")["parent_id"] == "/"


def test_listing_shows_folders_and_pdfs_by_name_and_hides_dotfiles(local, root):
    (root / "b.PDF").write_bytes(PDF)
    (root / "a.pdf").write_bytes(PDF)
    (root / "notes.txt").write_text("x")
    (root / ".hidden.pdf").write_bytes(PDF)
    (root / ".recycle").mkdir()
    here, folders, files = local.contents(None)
    assert here["is_root"] and folders == [{"id": "/Model 3", "name": "Model 3"}]
    assert [(f["id"], f["name"], f["size"], f["web_url"]) for f in files] == [
        ("/a.pdf", "a.pdf", len(PDF), "/a.pdf"), ("/b.PDF", "b.PDF", len(PDF), "/b.PDF")]


def test_create_folder_fails_on_a_name_that_is_taken_or_not_a_plain_name(local, root):
    with pytest.raises(storage.StorageError, match="already exists"):
        local.create_folder("/", "Model 3")
    for bad in ("..", ".", "a/b", "", ".hidden"):
        with pytest.raises(storage.StorageError):
            local.create_folder("/", bad)
    assert sorted(p.name for p in root.iterdir()) == ["Model 3"]


def test_a_new_folder_name_that_resolves_out_of_the_folder_is_refused(local, root, tmp_path):
    """A name already taken by a symlink that leads out: refused, and nothing is made where it points."""
    (root / "Model 3" / "away").symlink_to(tmp_path / "outside", target_is_directory=True)
    with pytest.raises(storage.StorageError):
        local.create_folder("/Model 3", "away")
    assert sorted(p.name for p in (tmp_path / "outside").iterdir()) == ["secret.pdf"]


def test_an_error_carries_the_text_written_for_the_user():
    err = storage.StorageError("Choose a folder first.")
    assert err.user_message == "Choose a folder first." == str(err)


def test_ensure_subfolder_finds_any_case_and_makes_what_is_missing(local, root):
    assert local.ensure_subfolder("/Model 3", "tyres") == "/Model 3/Tyres"
    assert local.ensure_subfolder("/Model 3", "Insurance") == "/Model 3/Insurance"
    assert (root / "Model 3" / "Insurance").is_dir()


def test_two_uploads_making_the_same_subfolder_at_once_share_it(local, root):
    start, got, errors = threading.Barrier(8), [], []

    def make():
        start.wait()
        try:
            got.append(local.ensure_subfolder("/Model 3", "Maintenance"))
        except Exception as exc:   # noqa: BLE001 - the test reports whatever went wrong
            errors.append(exc)

    threads = [threading.Thread(target=make) for _ in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert errors == [] and set(got) == {"/Model 3/Maintenance"}


# ---- files ------------------------------------------------------------------------

def test_upload_writes_the_file_and_leaves_no_temporary_file(local, root):
    assert local.upload("/Model 3/Tyres", "261107 Tyres.pdf", PDF) == "/Model 3/Tyres/261107 Tyres.pdf"
    assert [p.name for p in (root / "Model 3" / "Tyres").iterdir()] == ["261107 Tyres.pdf"]
    assert (root / "Model 3" / "Tyres" / "261107 Tyres.pdf").read_bytes() == PDF


def test_a_name_clash_gets_a_suffix_and_never_overwrites(local, root):
    first = local.upload("/Model 3", "a.pdf", PDF)
    second = local.upload("/Model 3", "a.pdf", PDF + b"2")
    third = local.upload("/Model 3", "a.pdf", PDF + b"3")
    assert (first, second, third) == ("/Model 3/a.pdf", "/Model 3/a (1).pdf", "/Model 3/a (2).pdf")
    assert local.download(first) == PDF and local.download(second) == PDF + b"2"


def test_upload_refuses_a_name_that_is_not_a_plain_pdf_name(local, root):
    for bad in ("../x.pdf", "a/b.pdf", ".x.pdf", "x.txt", ""):
        with pytest.raises(storage.StorageError):
            local.upload("/Model 3", bad, PDF)


def test_pdfs_are_found_in_subfolders_with_the_folder_they_are_in(local, root):
    (root / "Model 3" / "Tyres" / "t.pdf").write_bytes(PDF)
    (root / "Model 3" / "top.pdf").write_bytes(PDF)
    (root / "Model 3" / "Tyres" / "readme.txt").write_text("x")
    (root / "Other").mkdir()
    (root / "Other" / "o.pdf").write_bytes(PDF)
    found = local.pdfs("/Model 3")
    assert [(f["folder"], f["name"], f["id"], f["web_url"]) for f in found] == [
        ("", "top.pdf", "/Model 3/top.pdf", "/Model 3/top.pdf"),
        ("Tyres", "t.pdf", "/Model 3/Tyres/t.pdf", "/Model 3/Tyres/t.pdf")]


def test_rename_stays_in_the_same_folder_and_never_replaces_a_file(local, root):
    local.upload("/Model 3", "old.pdf", PDF)
    local.upload("/Model 3", "taken.pdf", PDF + b"other")
    with pytest.raises(storage.StorageError, match="already exists"):
        local.rename("/Model 3/old.pdf", "taken.pdf")
    with pytest.raises(storage.StorageError):
        local.rename("/Model 3/old.pdf", "../moved.pdf")
    assert local.rename("/Model 3/old.pdf", "new.pdf") == "/Model 3/new.pdf"
    assert sorted(p.name for p in (root / "Model 3").glob("*.pdf")) == ["new.pdf", "taken.pdf"]
    assert local.download("/Model 3/taken.pdf") == PDF + b"other"


def test_only_pdfs_can_be_read_and_not_ones_over_the_limit(local, root, monkeypatch):
    (root / "notes.txt").write_text("private")
    (root / "big.pdf").write_bytes(PDF)
    with pytest.raises(storage.StorageError):
        local.download("/notes.txt")
    with pytest.raises(storage.StorageError):
        local.download("/Model 3")
    with pytest.raises(storage.StorageError):
        local.download("/missing.pdf")
    monkeypatch.setattr(storage, "MAX_PDF_BYTES", 3)
    with pytest.raises(storage.StorageError, match="20 MB"):
        local.download("/big.pdf")


# ---- the folder itself going away -------------------------------------------------

def test_a_missing_root_is_an_error_and_is_never_recreated(tmp_path):
    gone = storage.LocalFolder(tmp_path / "not-mounted")
    assert gone.available() is False
    for call in (lambda: gone.folder(None), lambda: gone.subfolders(None), lambda: gone.pdfs("/"),
                 lambda: gone.upload("/", "a.pdf", PDF), lambda: gone.create_folder("/", "Cars"),
                 lambda: gone.ensure_subfolder("/", "Cars"), lambda: gone.download("/a.pdf")):
        with pytest.raises(storage.StorageError, match="INVOICES_DIR"):
            call()
    assert not (tmp_path / "not-mounted").exists()


def test_a_chosen_folder_that_was_deleted_is_an_error_not_recreated(local, root):
    with pytest.raises(storage.StorageError):
        local.upload("/Model Y", "a.pdf", PDF)
    with pytest.raises(storage.StorageError):
        local.ensure_subfolder("/Model Y", "Tyres")
    assert not (root / "Model Y").exists()


@pytest.mark.skipif(os.geteuid() == 0, reason="root can write anywhere")
def test_a_folder_the_app_cannot_write_to_says_so(local, root):
    (root / "Model 3").chmod(0o555)
    try:
        with pytest.raises(storage.StorageError, match="permission"):
            local.upload("/Model 3", "a.pdf", PDF)
    finally:
        (root / "Model 3").chmod(0o755)
