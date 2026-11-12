"""Where invoice PDFs are kept: OneDrive, or a folder on the server (INVOICES_DIR).

Both are used through the same few calls, so uploading into a category's subfolder, the import
preview and learning from corrections are written once:

    folder(id) subfolders(id) contents(id) create_folder(parent, name) ensure_subfolder(parent, name)
    upload(folder, name, bytes) pdfs(folder) download(id) rename(id, name)

`upload` and `rename` return the file's reference, the thing an expense stores: for OneDrive its
web address (expense.invoice_url), for the server folder its path below the root
(expense.invoice_path), which the app serves itself. A listed file carries that same reference as
`web_url`, whichever storage it is in.

In the server folder an id is the path below INVOICES_DIR, always starting with '/' ('/' is the
root itself). Nothing outside the root is ever read or written: every id is resolved, symlinks
included, and refused unless it lands inside. The root is never created: if it is missing (a share
that didn't mount), that is an error to show, not a folder to quietly fill on the wrong disk.
"""

import errno
import logging
import os
import secrets
from pathlib import Path

log = logging.getLogger("tmexpenses.storage")

MAX_DEPTH = 4    # subfolders below a car's folder that are searched for PDFs
MAX_PDF_BYTES = 20 * 1024 * 1024
MAX_SUFFIX = 1000   # "name (999).pdf": far more clashes than one name will ever see


class StorageError(Exception):
    """A failure to tell the person using the app about. `user_message` is the text written for them, and
    the only part of an error a page or reply may show: never str() of it, which a subclass or a library
    could fill with internals."""

    def __init__(self, user_message):
        super().__init__(user_message)
        self.user_message = user_message


class OneDriveStore:
    """A OneDrive (onedrive.OneDrive) as a store. The access token is fetched when first needed
    and reused for the rest of the request."""

    kind = "onedrive"
    name = "OneDrive"
    ref_field = "invoice_url"
    _CALLS = ("folder", "subfolders", "contents", "create_folder", "ensure_subfolder", "upload",
              "pdfs", "download", "rename")

    def __init__(self, drive, access_token):
        self.drive, self._access_token, self._token = drive, access_token, None

    def __getattr__(self, name):
        if name not in self._CALLS:
            raise AttributeError(name)
        call = getattr(self.drive, name)

        def with_token(*args, **kwargs):
            if self._token is None:
                self._token = self._access_token()
            return call(self._token, *args, **kwargs)
        return with_token

    @staticmethod
    def link(ref):
        return ref


def _plain_name(name):
    return (isinstance(name, str) and name not in ("", ".", "..") and not name.startswith(".")
            and "/" not in name and "\x00" not in name)


class LocalFolder:
    kind = "local"
    name = "the invoice folder"
    ref_field = "invoice_path"

    def __init__(self, root, link=None):
        """`link(ref)` gives the address the app serves a file at."""
        self.root = Path(root)
        self.link = link or (lambda ref: ref)

    # ---- paths ----------------------------------------------------------------

    def available(self):
        try:
            self._root()
        except StorageError:
            return False
        return True

    def _root(self):
        try:
            root = self.root.resolve(strict=True)
        except (OSError, RuntimeError):
            root = None
        if root is None or not root.is_dir():
            raise StorageError("The invoice folder (INVOICES_DIR) isn't there: is it mounted into the "
                               "container, and is the share it's on connected? Nothing was changed.")
        return root

    def _resolve(self, item_id):
        """-> (root, the real path of `item_id`), which is inside the root or the root itself."""
        root = self._root()
        if item_id in (None, "", "/"):
            return root, root
        outside = StorageError("That isn't a path inside the invoice folder.")
        if not isinstance(item_id, str) or not item_id.startswith("/") or "\x00" in item_id:
            raise outside
        parts = item_id[1:].split("/")
        if any(part in ("", ".", "..") for part in parts):
            raise outside
        try:
            found = root.joinpath(*parts).resolve()
        except (OSError, RuntimeError):     # a symlink loop, or a name the filesystem refuses
            raise outside from None
        if root not in found.parents:       # '..' is already refused, so this is a symlink leading out
            raise outside
        return root, found

    @staticmethod
    def _id(root, path):
        return "/" if path == root else "/" + path.relative_to(root).as_posix()

    def _dir(self, folder_id):
        root, path = self._resolve(folder_id)
        if not path.is_dir():
            raise StorageError("That folder isn't in the invoice folder any more: choose the car's folder again.")
        return root, path

    def _inside(self, root, path):
        """Is this directory entry really inside the root? (a symlink may point anywhere)"""
        try:
            return root in path.resolve().parents
        except (OSError, RuntimeError):
            return False

    def file(self, item_id):
        """-> the path of a PDF, for reading or sending. Only PDFs, and never over the size limit."""
        _, path = self._resolve(item_id)
        with _os_errors("read"):
            if path.suffix.lower() != ".pdf" or not path.is_file():
                raise StorageError("That file isn't in the invoice folder (moved, renamed or deleted?).")
            if path.stat().st_size > MAX_PDF_BYTES:
                raise StorageError("That file can't be read (over 20 MB).")
        return path

    # ---- folders --------------------------------------------------------------

    def folder(self, folder_id=None):
        """-> {id, name, path, parent_id, is_root} of a folder (the root when None)."""
        root, path = self._dir(folder_id)
        here = self._id(root, path)
        return {"id": here, "name": "/" if path == root else path.name, "path": here,
                "parent_id": None if path == root else self._id(root, path.parent), "is_root": path == root}

    def _children(self, root, path):
        """-> (folders, PDFs) directly inside, as paths, by name. Dotfiles (our own temporary files,
        a NAS's recycle bin) and anything that leads out of the root are left out."""
        folders, files = [], []
        with _os_errors("read"):
            for child in path.iterdir():
                if child.name.startswith(".") or not self._inside(root, child):
                    continue
                if child.is_dir():
                    folders.append(child)
                elif child.is_file() and child.suffix.lower() == ".pdf":
                    files.append(child)
        by_name = lambda p: p.name.lower()   # noqa: E731
        return sorted(folders, key=by_name), sorted(files, key=by_name)

    def _file_entry(self, root, path, **more):
        # Reached through a symlink or not, a file is known by the path it was listed at.
        ref = self._id(root, path)
        return {"id": ref, "name": path.name, "size": path.stat().st_size, "web_url": ref, **more}

    def subfolders(self, folder_id=None):
        """Folders directly inside a folder, by name: [{id, name}]."""
        root, path = self._dir(folder_id)
        return [{"id": self._id(root, f), "name": f.name} for f in self._children(root, path)[0]]

    def contents(self, folder_id=None):
        """What a file picker shows for one folder: (folder, [{id, name}], [{id, name, size, web_url}])."""
        root, path = self._dir(folder_id)
        folders, files = self._children(root, path)
        with _os_errors("read"):
            return (self.folder(self._id(root, path)), [{"id": self._id(root, f), "name": f.name} for f in folders],
                    [self._file_entry(root, f) for f in files])

    def create_folder(self, parent_id, name):
        """New folder inside parent_id (the root when None). Fails if the name is taken."""
        root, parent = self._dir(parent_id)
        if not _plain_name(name):
            raise StorageError("That isn't a name a folder can have.")
        # The name comes from a form: resolve it and make sure it lands directly in the parent, as every
        # other path here is checked against the root, before anything is made.
        target = os.path.realpath(os.path.join(parent, name))
        if not target.startswith(str(parent) + os.sep) or os.path.dirname(target) != str(parent):
            raise StorageError("That isn't a name a folder can have.")
        with _os_errors("write"):
            try:
                os.mkdir(target)
            except FileExistsError:
                raise StorageError(f"A folder called “{name}” already exists here.") from None
        log.debug("folder created name=%r parent=%s", name, self._id(root, parent))
        return self.folder(self._id(root, Path(target)))

    def ensure_subfolder(self, parent_id, name):
        """The folder called `name` (any case) directly inside parent_id, made if it isn't
        there. -> its id. Two uploads racing to make it both end up with the one folder."""
        root, parent = self._dir(parent_id)
        if not _plain_name(name):
            raise StorageError("That isn't a name a folder can have.")
        found = next((f for f in self._children(root, parent)[0] if f.name.lower() == name.lower()), None)
        if found is None:
            found = parent / name
            with _os_errors("write"):
                found.mkdir(exist_ok=True)
        return self._id(root, found)

    # ---- files ----------------------------------------------------------------

    def upload(self, folder_id, filename, content):
        """Save a PDF into a folder. Written to a temporary file beside it and moved into place, so
        a half-written file is never seen under the real name; a name that is taken gets ' (1)'
        rather than being overwritten. -> the file's reference."""
        root, folder = self._dir(folder_id)
        if not _plain_name(filename) or not filename.lower().endswith(".pdf"):
            raise StorageError("That isn't a name an invoice PDF can have.")
        stem, ext = filename[:-4], filename[-4:]
        tmp = folder / f".upload-{secrets.token_hex(8)}.tmp"
        with _os_errors("write"):
            try:
                with open(os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o666), "wb") as handle:
                    handle.write(content)
                    handle.flush()
                    os.fsync(handle.fileno())
                target = _claim(folder, [filename] + [f"{stem} ({n}){ext}" for n in range(1, MAX_SUFFIX)])
                os.replace(tmp, target)
            finally:
                tmp.unlink(missing_ok=True)
        return self._id(root, target)

    def rename(self, item_id, new_name):
        """Rename a file where it is; fails rather than replacing a file with that name.
        -> its new reference."""
        root, _ = self._resolve(item_id)
        path = self.file(item_id)
        if not _plain_name(new_name) or not new_name.lower().endswith(".pdf"):
            raise StorageError("That isn't a name an invoice PDF can have.")
        if new_name == path.name:
            return self._id(root, path)
        with _os_errors("write"):
            try:
                target = _claim(path.parent, [new_name])
            except FileExistsError:
                raise StorageError(f"A file called “{new_name}” already exists there.") from None
            os.replace(path, target)
        log.debug("rename item=%s to=%r", item_id, new_name)
        return self._id(root, target)

    def pdfs(self, folder_id):
        """Every PDF in a folder and the folders below it (MAX_DEPTH deep):
        [{id, name, size, web_url, folder}], `folder` being the path below the one given."""
        root, top = self._dir(folder_id)
        found = []

        def walk(path, below, depth):
            folders, files = self._children(root, path)
            found.extend(self._file_entry(root, f, folder=below) for f in files)
            if depth < MAX_DEPTH:
                for f in folders:
                    walk(f, f"{below}/{f.name}" if below else f.name, depth + 1)

        with _os_errors("read"):
            walk(top, "", 0)
        log.debug("list folder=%s pdfs=%d", self._id(root, top), len(found))
        return sorted(found, key=lambda f: (f["folder"].lower(), f["name"].lower()))

    def download(self, item_id):
        path = self.file(item_id)
        with _os_errors("read"):
            return path.read_bytes()


def _claim(folder, names):
    """Create the first of `names` that isn't taken, empty, and return its path: the name is then
    ours to move a file onto. Raises FileExistsError when every one is taken."""
    for name in names:
        try:
            os.close(os.open(folder / name, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o666))
        except FileExistsError:
            continue
        return folder / name
    raise FileExistsError(names[0])


class _os_errors:
    """Turn what the filesystem refuses into a message that says what to fix."""

    def __init__(self, doing):
        self.doing = doing

    def __enter__(self):
        return self

    def __exit__(self, kind, exc, _tb):
        if exc is None or isinstance(exc, StorageError) or not isinstance(exc, OSError):
            return False
        log.warning("invoice folder %s failed: %s", self.doing, exc)
        if isinstance(exc, PermissionError) or exc.errno == errno.EROFS:
            raise StorageError(f"The app has no permission to {self.doing} in the invoice folder: the folder's "
                               "owner must match the user the container runs as (see the documentation).") from None
        if exc.errno == errno.ENOSPC:
            raise StorageError("The disk the invoice folder is on is full.") from None
        if isinstance(exc, FileNotFoundError):
            raise StorageError("That isn't in the invoice folder any more (moved, renamed or deleted?).") from None
        raise StorageError(f"Couldn't {self.doing} the invoice folder: {exc.strerror or exc}.") from None
