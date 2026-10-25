"""Copy year- or month-based folders from an iPhone to a Windows folder.

The iPhone is accessed through the Windows Shell COM API because an MTP device
is not mounted as a normal file-system path.
"""

from __future__ import annotations

import argparse
import ctypes
import gc
import multiprocessing
import os
import re
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Iterable, Protocol, Sequence


class IPhoneCopyError(RuntimeError):
    """A recoverable error that can be shown directly to the user."""


def normalize_skip_extensions(values: Iterable[str]) -> frozenset[str]:
    """Normalize extension filters to case-insensitive values without dots."""

    normalized: set[str] = set()
    for value in values:
        extension = value[1:] if value.startswith(".") else value
        if not extension:
            raise ValueError("跳过扩展名不能为空。")
        normalized.add(extension.casefold())
    return frozenset(normalized)


@dataclass(frozen=True)
class Period:
    """A validated year (YYYY) or month (YYYYMM) selection."""

    value: str
    folder_regex: re.Pattern[str] = field(compare=False, repr=False)

    @classmethod
    def parse(cls, value: str) -> "Period":
        if re.fullmatch(r"\d{4}", value):
            regex = re.compile(rf"^{re.escape(value)}(?:0[1-9]|1[0-2])_.*$")
            return cls(value=value, folder_regex=regex)

        match = re.fullmatch(r"(\d{4})(\d{2})", value)
        if match:
            month = int(match.group(2))
            if 1 <= month <= 12:
                regex = re.compile(rf"^{re.escape(value)}_.*$")
                return cls(value=value, folder_regex=regex)

        raise ValueError("时间必须是有效的 YYYY 或 YYYYMM，例如 2026 或 202610。")

    def matches(self, folder_name: str) -> bool:
        return self.folder_regex.fullmatch(folder_name) is not None


@dataclass(frozen=True)
class SourceFile:
    """One source file and its destination-relative path."""

    relative_parts: tuple[str, ...]
    size: int
    native_item: Any = field(compare=False, repr=False)

    @property
    def display_path(self) -> str:
        return "\\".join(self.relative_parts)


@dataclass(frozen=True)
class SourceSelection:
    folder_names: tuple[str, ...]
    files: tuple[SourceFile, ...]
    skipped_count: int = 0

    @property
    def total_size(self) -> int:
        return sum(source.size for source in self.files)


@dataclass(frozen=True)
class VerificationReport:
    expected_count: int
    actual_count: int
    expected_size: int
    actual_size: int
    missing: tuple[str, ...] = ()
    unexpected: tuple[str, ...] = ()
    wrong_size: tuple[str, ...] = ()

    @property
    def success(self) -> bool:
        return (
            self.expected_count == self.actual_count
            and self.expected_size == self.actual_size
            and not self.missing
            and not self.unexpected
            and not self.wrong_size
        )


class SourceBackend(Protocol):
    """Operations needed to traverse and copy from an MTP-like source."""

    def list_items(self, folder: Any) -> Iterable[Any]: ...

    def item_name(self, item: Any) -> str: ...

    def item_is_folder(self, item: Any) -> bool: ...

    def item_size(self, item: Any) -> int: ...

    def item_folder(self, item: Any) -> Any: ...

    def copy_file(
        self,
        source: SourceFile,
        destination_folder: Path,
        *,
        timeout: float,
        status_callback: Callable[[str], None] | None = None,
    ) -> None: ...


class WindowsMtpBackend:
    """Access Windows portable devices through Shell.Application."""

    # ShellSpecialFolderConstants.ssfDRIVES
    SSF_DRIVES = 17
    # FOF_SILENT | FOF_NOCONFIRMATION | FOF_NOCONFIRMMKDIR | FOF_NOERRORUI
    COPY_FLAGS = 0x0004 | 0x0010 | 0x0200 | 0x0400

    def __init__(self) -> None:
        if os.name != "nt":
            raise IPhoneCopyError("MTP 访问只支持 Windows。")
        try:
            import pythoncom  # type: ignore[import-not-found]
            import win32com.client  # type: ignore[import-not-found]
        except ImportError as exc:
            raise IPhoneCopyError(
                "缺少 pywin32。请先运行: python -m pip install -r requirements.txt"
            ) from exc

        self._pythoncom = pythoncom
        self._pythoncom.CoInitialize()
        self._initialized = True
        try:
            self._shell = win32com.client.Dispatch("Shell.Application")
        except Exception:
            self.close()
            raise

    def close(self) -> None:
        if hasattr(self, "_shell"):
            self._shell = None
            gc.collect()
        if getattr(self, "_initialized", False):
            self._pythoncom.CoUninitialize()
            self._initialized = False

    def find_storage(self, device_name: str, storage_name: str) -> Any:
        self._device_name = device_name
        self._storage_name = storage_name
        drives = self._shell.NameSpace(self.SSF_DRIVES)
        if drives is None:
            raise IPhoneCopyError("无法打开 Windows 的“此电脑”。")

        device = self._find_named_item(drives, device_name, "设备")
        try:
            device_folder = self.item_folder(device)
        except Exception as exc:
            raise IPhoneCopyError(
                f"无法打开设备 {device_name!r}。请解锁 iPhone 并选择“信任”。"
            ) from exc

        storage = self._find_named_item(device_folder, storage_name, "存储区")
        try:
            return self.item_folder(storage)
        except Exception as exc:
            raise IPhoneCopyError(f"无法打开存储区 {storage_name!r}。") from exc

    ENUM_RETRIES = 3
    ENUM_RETRY_DELAY = 0.5

    @staticmethod
    def _com_error_detail(exc: BaseException) -> str:
        """Format a pywin32 COM exception without requiring pywin32 in tests."""

        parts: list[str] = []
        hresult = getattr(exc, "hresult", None)
        if isinstance(hresult, int):
            parts.append(f"HRESULT 0x{hresult & 0xFFFFFFFF:08X}")
        message = getattr(exc, "strerror", None) or str(exc)
        if message:
            parts.append(" ".join(str(message).split()))
        return ": ".join(parts) or type(exc).__name__

    def _retry_pause(self) -> None:
        pythoncom = getattr(self, "_pythoncom", None)
        if pythoncom is not None:
            try:
                pythoncom.PumpWaitingMessages()
            except Exception:
                pass
        time.sleep(self.ENUM_RETRY_DELAY)

    @staticmethod
    def _items_collection(folder: Any) -> tuple[Any, int]:
        items = folder.Items()
        if items is None:
            raise RuntimeError("Folder.Items() returned None")
        return items, int(items.Count)

    def _find_named_item(self, folder: Any, wanted: str, kind: str) -> Any:
        """Find a child lazily so broken later Shell nodes aren't accessed."""

        last_error: BaseException | None = None
        for attempt in range(1, self.ENUM_RETRIES + 1):
            try:
                items, count = self._items_collection(folder)
                break
            except Exception as exc:
                last_error = exc
                if attempt < self.ENUM_RETRIES:
                    self._retry_pause()
        else:
            assert last_error is not None
            detail = self._com_error_detail(last_error)
            raise IPhoneCopyError(
                f"无法枚举{kind}列表（已尝试 {self.ENUM_RETRIES} 次；"
                f"Windows COM: {detail}）。"
            ) from last_error

        available: list[str] = []
        inaccessible: list[str] = []
        for index in range(count):
            try:
                item = items.Item(index)
                if item is None:
                    inaccessible.append(f"索引 {index}: 空节点")
                    continue
                name = self.item_name(item)
                available.append(repr(name))
                if name.casefold() == wanted.casefold():
                    # Do not enumerate the rest of "This PC". Windows can report
                    # stale namespace nodes in Count that fail when accessed.
                    return item
            except Exception as exc:
                inaccessible.append(
                    f"索引 {index}: {self._com_error_detail(exc)}"
                )

        visible = ", ".join(available) or "（无）"
        suffix = ""
        if inaccessible:
            suffix = "；不可访问项目: " + "; ".join(inaccessible)
        raise IPhoneCopyError(
            f"找不到{kind} {wanted!r}。当前可见项目: {visible}{suffix}"
        )

    def list_items(self, folder: Any) -> Iterable[Any]:
        """Read a complete folder with retries for transient MTP failures."""

        last_error: BaseException | None = None
        for attempt in range(1, self.ENUM_RETRIES + 1):
            try:
                items, count = self._items_collection(folder)
                result: list[Any] = []
                for index in range(count):
                    item = items.Item(index)
                    if item is None:
                        raise RuntimeError(
                            f"FolderItems.Item({index}) returned None "
                            f"while Count is {count}"
                        )
                    result.append(item)
                return result
            except Exception as exc:
                last_error = exc
                if attempt < self.ENUM_RETRIES:
                    self._retry_pause()

        assert last_error is not None
        detail = self._com_error_detail(last_error)
        raise IPhoneCopyError(
            f"读取文件夹内容失败（已尝试 {self.ENUM_RETRIES} 次；"
            f"Windows COM: {detail}）。请保持 iPhone 解锁，确认已选择“信任”，"
            "然后在文件资源管理器中重新打开 Internal Storage。"
        ) from last_error

    @staticmethod
    def item_name(item: Any) -> str:
        return str(item.Name)

    @staticmethod
    def item_is_folder(item: Any) -> bool:
        return bool(item.IsFolder)

    @staticmethod
    def item_size(item: Any) -> int:
        value: Any = None
        try:
            # MTP FolderItem.Size commonly reports 0 for every iPhone file.
            # The canonical Windows property contains the real byte count.
            value = item.ExtendedProperty("System.Size")
        except Exception:
            pass
        if value in (None, ""):
            try:
                value = item.Size
            except Exception as exc:
                raise IPhoneCopyError(f"无法读取文件大小: {item.Name!r}") from exc
        try:
            size = int(value or 0)
        except (TypeError, ValueError) as exc:
            raise IPhoneCopyError(f"无法读取文件大小: {item.Name!r}") from exc
        if size < 0:
            raise IPhoneCopyError(f"文件大小无效: {item.Name!r}")
        return size

    @staticmethod
    def item_folder(item: Any) -> Any:
        # Shell FolderItem.GetFolder is a property. A pywin32 COM object can
        # report itself as callable, but calling it invokes its default member
        # and may return a string instead of the Folder object.
        return item.GetFolder

    def copy_file(
        self,
        source: SourceFile,
        destination_folder: Path,
        *,
        timeout: float,
        status_callback: Callable[[str], None] | None = None,
    ) -> None:
        device_name = getattr(self, "_device_name", None)
        storage_name = getattr(self, "_storage_name", None)
        if not device_name or not storage_name:
            raise IPhoneCopyError("复制前尚未定位 iPhone 存储区。")

        context = multiprocessing.get_context("spawn")
        result_queue = context.Queue()
        process = context.Process(
            target=_copy_mtp_file_worker,
            args=(
                device_name,
                storage_name,
                source.relative_parts,
                source.size,
                str(destination_folder),
                self.COPY_FLAGS,
                result_queue,
            ),
            name=f"mtp-copy-{source.relative_parts[-1]}",
        )
        started = time.monotonic()
        process.start()
        next_status = started + 10.0
        try:
            while process.is_alive():
                elapsed = time.monotonic() - started
                if elapsed >= timeout:
                    process.terminate()
                    process.join(5)
                    if process.is_alive():
                        process.kill()
                        process.join(5)
                    raise IPhoneCopyError(
                        f"复制超时，已终止卡住的 Windows MTP 调用: "
                        f"{source.display_path}（等待 {timeout:g} 秒）。"
                    )
                if status_callback and time.monotonic() >= next_status:
                    status_callback(f"仍在等待 Windows MTP（已等待 {elapsed:.0f} 秒）")
                    next_status += 10.0
                process.join(min(0.5, max(0.0, timeout - elapsed)))

            message: tuple[str, str] | None = None
            try:
                message = result_queue.get(timeout=1)
            except Exception:
                pass
            if message and message[0] == "ok":
                return
            if message and message[0] == "error":
                raise IPhoneCopyError(
                    f"复制失败: {source.display_path}；{message[1]}"
                )
            raise IPhoneCopyError(
                f"复制进程异常结束: {source.display_path}；"
                f"退出码 {process.exitcode}。"
            )
        finally:
            if process.is_alive():
                process.terminate()
                process.join(5)
            result_queue.close()
            result_queue.join_thread()


def _copy_mtp_file_worker(
    device_name: str,
    storage_name: str,
    relative_parts: tuple[str, ...],
    expected_size: int,
    destination_folder: str,
    copy_flags: int,
    result_queue: Any,
) -> None:
    """Run one potentially blocking Shell CopyHere call in isolation."""

    backend: WindowsMtpBackend | None = None
    storage: Any = None
    current_folder: Any = None
    source_item: Any = None
    try:
        backend = WindowsMtpBackend()
        storage = backend.find_storage(device_name, storage_name)
        current_folder = storage
        for component in relative_parts[:-1]:
            folder_item = backend._find_named_item(current_folder, component, "源文件夹")
            if not backend.item_is_folder(folder_item):
                raise IPhoneCopyError(f"源路径组件不是文件夹: {component!r}")
            current_folder = backend.item_folder(folder_item)
        source_item = backend._find_named_item(
            current_folder, relative_parts[-1], "源文件"
        )

        destination = backend._shell.NameSpace(destination_folder)
        if destination is None:
            raise IPhoneCopyError(f"Windows Shell 无法打开目标目录: {destination_folder}")
        destination.CopyHere(source_item, copy_flags)

        target = Path(destination_folder) / relative_parts[-1]
        while not _file_has_expected_size(target, expected_size):
            time.sleep(0.25)
        result_queue.put(("ok", ""))
    except BaseException as exc:
        detail = (
            WindowsMtpBackend._com_error_detail(exc)
            if backend is not None
            else " ".join(str(exc).split())
        )
        try:
            result_queue.put(("error", detail))
        except Exception:
            pass
    finally:
        source_item = None
        current_folder = None
        storage = None
        gc.collect()
        if backend is not None:
            backend.close()


def _validate_windows_component(name: str) -> None:
    if not name or name in {".", ".."}:
        raise IPhoneCopyError(f"源项目名称无效: {name!r}")
    if any(character in name for character in '<>:"/\\|?*'):
        raise IPhoneCopyError(f"源项目名称不能写入 Windows 文件夹: {name!r}")
    if name.endswith((" ", ".")):
        raise IPhoneCopyError(f"源项目名称不能写入 Windows 文件夹: {name!r}")


def collect_source_selection(
    backend: SourceBackend,
    storage_folder: Any,
    period: Period,
    skip_extensions: Iterable[str] = (),
) -> SourceSelection:
    """Collect a stable source manifest before copying starts."""

    normalized_skip_extensions = normalize_skip_extensions(skip_extensions)

    top_folders: list[Any] = []
    for item in backend.list_items(storage_folder):
        if backend.item_is_folder(item) and period.matches(backend.item_name(item)):
            top_folders.append(item)

    top_folders.sort(key=lambda item: backend.item_name(item).casefold())
    if not top_folders:
        raise IPhoneCopyError(
            f"Internal Storage 中没有匹配 {period.value!r} 的文件夹。"
        )

    files: list[SourceFile] = []
    skipped_count = 0
    seen_paths: dict[tuple[str, ...], str] = {}

    def walk(folder: Any, parent_parts: tuple[str, ...]) -> None:
        nonlocal skipped_count
        children = list(backend.list_items(folder))
        children.sort(key=lambda item: backend.item_name(item).casefold())
        for child in children:
            name = backend.item_name(child)
            _validate_windows_component(name)
            parts = parent_parts + (name,)
            if backend.item_is_folder(child):
                walk(backend.item_folder(child), parts)
                continue

            extension = name.rsplit(".", 1)[-1] if "." in name else ""
            if extension and extension.casefold() in normalized_skip_extensions:
                skipped_count += 1
                continue

            folded = tuple(part.casefold() for part in parts)
            if folded in seen_paths:
                conflict_path = "\\".join(parts)
                raise IPhoneCopyError(
                    "源文件在 Windows 上会发生名称冲突: "
                    f"{seen_paths[folded]!r} 与 {conflict_path!r}"
                )
            seen_paths[folded] = "\\".join(parts)
            files.append(
                SourceFile(
                    relative_parts=parts,
                    size=backend.item_size(child),
                    native_item=child,
                )
            )

    folder_names: list[str] = []
    for top_folder in top_folders:
        folder_name = backend.item_name(top_folder)
        _validate_windows_component(folder_name)
        folder_names.append(folder_name)
        walk(backend.item_folder(top_folder), (folder_name,))

    return SourceSelection(
        folder_names=tuple(folder_names),
        files=tuple(files),
        skipped_count=skipped_count,
    )


def _windows_file_is_exclusively_readable(path: Path) -> bool:
    """Return False while another Windows process still has the file open."""

    if os.name != "nt":
        return True

    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    create_file = kernel32.CreateFileW
    create_file.argtypes = [
        ctypes.c_wchar_p,
        ctypes.c_uint32,
        ctypes.c_uint32,
        ctypes.c_void_p,
        ctypes.c_uint32,
        ctypes.c_uint32,
        ctypes.c_void_p,
    ]
    create_file.restype = ctypes.c_void_p
    handle = create_file(
        str(path),
        0x80000000,  # GENERIC_READ
        0,  # No sharing: fails while the copy engine is writing.
        None,
        3,  # OPEN_EXISTING
        0x80,  # FILE_ATTRIBUTE_NORMAL
        None,
    )
    invalid_handle = ctypes.c_void_p(-1).value
    if handle == invalid_handle:
        return False
    kernel32.CloseHandle.argtypes = [ctypes.c_void_p]
    kernel32.CloseHandle(handle)
    return True


def _file_has_expected_size(path: Path, expected_size: int) -> bool:
    try:
        return (
            path.is_file()
            and path.stat().st_size == expected_size
            and _windows_file_is_exclusively_readable(path)
        )
    except OSError:
        return False


def copy_selection(
    backend: SourceBackend,
    selection: SourceSelection,
    destination: Path,
    *,
    overwrite: bool = False,
    timeout: float = 600.0,
    poll_interval: float = 0.5,
    progress: Callable[[int, int, SourceFile, str], None] | None = None,
) -> tuple[int, int]:
    """Copy every selected file and wait for each asynchronous MTP transfer."""

    if timeout <= 0:
        raise ValueError("timeout must be greater than zero")
    destination.mkdir(parents=True, exist_ok=True)
    if not destination.is_dir():
        raise IPhoneCopyError(f"目标路径不是文件夹: {destination}")

    copied = 0
    skipped = 0
    total = len(selection.files)

    # Create matching roots even when a source folder is empty.
    for folder_name in selection.folder_names:
        (destination / folder_name).mkdir(parents=True, exist_ok=True)

    for index, source in enumerate(selection.files, start=1):
        target = destination.joinpath(*source.relative_parts)
        target.parent.mkdir(parents=True, exist_ok=True)

        if target.exists():
            if target.is_file() and target.stat().st_size == source.size:
                skipped += 1
                if progress:
                    progress(index, total, source, "跳过（已存在且大小一致）")
                continue
            if not overwrite:
                raise IPhoneCopyError(
                    f"目标已存在但大小或类型不同: {target}\n"
                    "确认可以替换后，重新运行并添加 --overwrite。"
                )
            if target.is_dir():
                raise IPhoneCopyError(f"目标位置存在同名文件夹，无法覆盖为文件: {target}")
            try:
                target.unlink()
            except OSError as exc:
                raise IPhoneCopyError(f"无法删除待覆盖文件: {target}") from exc

        if progress:
            progress(index, total, source, "复制中")

        def report_waiting(status: str) -> None:
            if progress:
                progress(index, total, source, status)

        backend.copy_file(
            source,
            target.parent,
            timeout=timeout,
            status_callback=report_waiting,
        )
        if not _file_has_expected_size(target, source.size):
            observed = target.stat().st_size if target.is_file() else "不存在"
            raise IPhoneCopyError(
                f"复制调用结束但目标文件不完整: {source.display_path}；"
                f"期望 {source.size} 字节，当前 {observed}。"
            )
        copied += 1

    return copied, skipped


def _local_manifest(
    destination: Path, folder_names: Sequence[str]
) -> dict[tuple[str, ...], int]:
    manifest: dict[tuple[str, ...], int] = {}
    for folder_name in folder_names:
        root = destination / folder_name
        if not root.is_dir():
            continue
        try:
            paths = sorted(root.rglob("*"), key=lambda path: str(path).casefold())
            for path in paths:
                if path.is_file():
                    relative = path.relative_to(destination)
                    manifest[tuple(relative.parts)] = path.stat().st_size
        except OSError as exc:
            raise IPhoneCopyError(f"读取目标目录进行校验时失败: {root}") from exc
    return manifest


def verify_selection(
    selection: SourceSelection, destination: Path
) -> VerificationReport:
    """Compare source and destination by relative path and byte size."""

    expected = {source.relative_parts: source.size for source in selection.files}
    actual = _local_manifest(destination, selection.folder_names)

    expected_keys = set(expected)
    actual_keys = set(actual)
    missing = tuple("\\".join(parts) for parts in sorted(expected_keys - actual_keys))
    unexpected = tuple("\\".join(parts) for parts in sorted(actual_keys - expected_keys))
    wrong_size = tuple(
        "{}（源 {}，目标 {} 字节）".format(
            "\\".join(parts), expected[parts], actual[parts]
        )
        for parts in sorted(expected_keys & actual_keys)
        if expected[parts] != actual[parts]
    )

    return VerificationReport(
        expected_count=len(expected),
        actual_count=len(actual),
        expected_size=sum(expected.values()),
        actual_size=sum(actual.values()),
        missing=missing,
        unexpected=unexpected,
        wrong_size=wrong_size,
    )


def format_bytes(size: int) -> str:
    units = ("B", "KiB", "MiB", "GiB", "TiB")
    value = float(size)
    for unit in units:
        if value < 1024 or unit == units[-1]:
            return f"{value:.0f} {unit}" if unit == "B" else f"{value:.2f} {unit}"
        value /= 1024
    return f"{size} B"


def _print_progress(index: int, total: int, source: SourceFile, status: str) -> None:
    print(
        f"[{index}/{total}] {status}: {source.display_path} "
        f"({format_bytes(source.size)})",
        flush=True,
    )


def _print_report(report: VerificationReport) -> None:
    print("\n校验结果:")
    print(f"  源文件: {report.expected_count} 个，{format_bytes(report.expected_size)}")
    print(f"  目标文件: {report.actual_count} 个，{format_bytes(report.actual_size)}")
    for label, values in (
        ("缺少", report.missing),
        ("多出", report.unexpected),
        ("大小不同", report.wrong_size),
    ):
        if values:
            print(f"  {label} ({len(values)}):")
            for value in values[:20]:
                print(f"    - {value}")
            if len(values) > 20:
                print(f"    - ……另外 {len(values) - 20} 项")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "从 Windows 中的 Apple iPhone/Internal Storage 复制 YYYYMM_* "
            "文件夹，并按文件数量和大小校验。"
        )
    )
    parser.add_argument("period", help="年份 YYYY 或月份 YYYYMM，例如 2026 或 202610")
    parser.add_argument(
        "--destination",
        type=Path,
        default=Path.home() / "Downloads",
        help="目标目录（默认: %(default)s）",
    )
    parser.add_argument("--device-name", default="Apple iPhone", help="设备显示名称")
    parser.add_argument("--storage-name", default="Internal Storage", help="存储区显示名称")
    parser.add_argument(
        "--overwrite",
        action="store_true",
        help="覆盖目标中同名但大小不同的文件",
    )
    parser.add_argument(
        "--timeout",
        type=float,
        default=600.0,
        help="等待每个文件复制完成的秒数（默认: %(default)s）",
    )
    parser.add_argument(
        "--skip-extension",
        action="append",
        default=[],
        metavar="EXTENSION",
        help="按扩展名跳过源文件；可重复指定，例如 .MOV 或 MOV",
    )
    parser.add_argument(
        "--list-only",
        action="store_true",
        help="只显示匹配结果，不复制",
    )
    return parser


def run(args: argparse.Namespace) -> int:
    try:
        period = Period.parse(args.period)
    except ValueError as exc:
        raise IPhoneCopyError(str(exc)) from exc
    if args.timeout <= 0:
        raise IPhoneCopyError("--timeout 必须大于 0。")
    try:
        skip_extensions = normalize_skip_extensions(
            getattr(args, "skip_extension", ())
        )
    except ValueError as exc:
        raise IPhoneCopyError(str(exc)) from exc

    backend = WindowsMtpBackend()
    storage: Any = None
    selection: SourceSelection | None = None
    try:
        storage = backend.find_storage(args.device_name, args.storage_name)
        print("正在读取 iPhone 文件清单……", flush=True)
        selection = collect_source_selection(
            backend, storage, period, skip_extensions
        )
        print(f"匹配文件夹: {', '.join(selection.folder_names)}")
        print(
            f"源文件合计: {len(selection.files)} 个，"
            f"{format_bytes(selection.total_size)}"
        )
        if skip_extensions:
            display_extensions = ", ".join(
                f".{extension.upper()}" for extension in sorted(skip_extensions)
            )
            print(
                f"按扩展名跳过: {selection.skipped_count} 个 "
                f"（{display_extensions}）"
            )

        if args.list_only:
            return 0

        destination = args.destination.expanduser().resolve()
        print(f"目标目录: {destination}")
        copied, skipped = copy_selection(
            backend,
            selection,
            destination,
            overwrite=args.overwrite,
            timeout=args.timeout,
            progress=_print_progress,
        )
        print(f"\n复制阶段完成: 新复制 {copied} 个，跳过 {skipped} 个。")
        report = verify_selection(selection, destination)
        _print_report(report)
        if report.success:
            print("校验成功：相对路径、文件数量和文件大小全部一致。")
            return 0
        print("校验失败：源和目标不一致。", file=sys.stderr)
        return 2
    finally:
        # Release FolderItem references before leaving the COM apartment.
        selection = None
        storage = None
        gc.collect()
        backend.close()


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        return run(args)
    except IPhoneCopyError as exc:
        print(f"错误: {exc}", file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        print("\n已取消。", file=sys.stderr)
        return 130


if __name__ == "__main__":
    multiprocessing.freeze_support()
    raise SystemExit(main())
