"""Download one MAYA corpus archive into a gitignored local directory.

The archive and extracted file stay on disk so a later run can reuse them.
Nothing in the returned record is a password. Google Drive ids come from
``data.maya_catalog``.
"""
from __future__ import annotations

import hashlib
import json
import shutil
import zipfile
from pathlib import Path

from core.counted_corpus import CorpusFormatError
from data.maya_catalog import dataset_card, dataset_names


DEFAULT_DATASETS = dataset_names()
MAX_ARCHIVE_BYTES = 4 * 1024 * 1024 * 1024


def ensure_dataset(name: str, root: str | Path, *, max_bytes: int = MAX_ARCHIVE_BYTES) -> dict:
    card = dataset_card(name)
    folder = Path(root) / name
    folder.mkdir(parents=True, exist_ok=True)
    ready_path = folder / "ready.json"
    if ready_path.is_file():
        ready = json.loads(ready_path.read_text(encoding="utf-8"))
        payload = folder / "extracted" / ready["payload_name"]
        if payload.is_file() and _digest_file(payload) == ready["payload_sha256"]:
            ready["reused"] = True
            ready["dataset"] = name
            return ready
    archive = folder / "archive.bin"
    _download(card["drive_id"], archive, max_bytes=max_bytes)
    extracted = folder / "extracted"
    if extracted.exists():
        shutil.rmtree(extracted)
    extracted.mkdir(parents=True)
    _extract(archive, extracted)
    payload = select_payload(extracted)
    relative = payload.relative_to(extracted).as_posix()
    ready = {
        "dataset": name,
        "archive_sha256": _digest_file(archive),
        "archive_bytes": archive.stat().st_size,
        "payload_name": relative,
        "payload_sha256": _digest_file(payload),
        "payload_bytes": payload.stat().st_size,
        "reused": False,
    }
    ready_path.write_text(json.dumps(ready, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    if archive.exists():
        archive.unlink()
    return ready


def select_payload(root: Path) -> Path:
    files = [path for path in root.rglob("*") if path.is_file() and not path.name.startswith(".")]
    if not files:
        raise CorpusFormatError("解压后没有数据文件")
    files.sort(key=lambda path: (-path.stat().st_size, path.name))
    if len(files) > 1 and files[1].stat().st_size > files[0].stat().st_size * 0.5:
        names = ", ".join(path.name[:80] for path in files[:5])
        raise CorpusFormatError(f"解压结果里有多个相近大小的文件，拒绝猜测：{names}")
    return files[0]


def _download(drive_id: str, destination: Path, *, max_bytes: int) -> None:
    try:
        import gdown
    except ImportError as exc:
        raise CorpusFormatError("下载 MAYA 需要可选依赖 gdown") from exc
    destination.parent.mkdir(parents=True, exist_ok=True)
    if destination.exists():
        destination.unlink()
    gdown.download(id=drive_id, output=str(destination), quiet=True, retries=2)
    if not destination.is_file() or destination.stat().st_size == 0:
        raise CorpusFormatError("下载没有得到文件")
    if destination.stat().st_size > max_bytes:
        destination.unlink()
        raise CorpusFormatError(f"压缩包超过本地上限 {max_bytes} 字节，已删除")
    reject_webpage(destination)


def reject_webpage(path: Path) -> None:
    header = path.read_bytes()[:64].lstrip().lower()
    if header.startswith(b"<") or header.startswith(b"<!doctype") or b"<html" in header:
        path.unlink()
        raise CorpusFormatError("Google Drive 返回了网页而不是压缩包")


def _extract(archive: Path, dest: Path) -> None:
    magic = archive.read_bytes()[:8]
    if magic.startswith(b"7z\xbc\xaf'\x1c"):
        try:
            import py7zr
        except ImportError as exc:
            raise CorpusFormatError("解压 7z 需要可选依赖 py7zr") from exc
        with py7zr.SevenZipFile(archive, mode="r") as handle:
            handle.extractall(path=dest)
    elif magic.startswith(b"PK"):
        with zipfile.ZipFile(archive) as handle:
            root = dest.resolve()
            for info in handle.infolist():
                target = (dest / info.filename).resolve()
                if not target.is_relative_to(root):
                    raise CorpusFormatError("压缩包路径越界")
            handle.extractall(dest)
    elif magic.startswith(b"\x1f\x8b"):
        import gzip
        import shutil
        target = dest / "payload.txt"
        with gzip.open(archive, "rb") as source, target.open("wb") as output:
            shutil.copyfileobj(source, output)
    else:
        raise CorpusFormatError(f"无法识别的压缩格式，文件头 {magic[:6].hex()}")
    root = dest.resolve()
    for path in dest.rglob("*"):
        if not path.resolve().is_relative_to(root):
            raise CorpusFormatError("解压结果越出目标目录")


def _digest_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()
