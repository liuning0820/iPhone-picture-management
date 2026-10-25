from __future__ import annotations

import tempfile
import unittest
from dataclasses import dataclass
from pathlib import Path

from iphone_copy import (
    IPhoneCopyError,
    Period,
    SourceFile,
    SourceSelection,
    WindowsMtpBackend,
    collect_source_selection,
    copy_selection,
    verify_selection,
)


@dataclass
class FakeItem:
    name: str
    data: bytes | None = None
    children: list["FakeItem"] | None = None

    @classmethod
    def folder(cls, name: str, *children: "FakeItem") -> "FakeItem":
        return cls(name=name, children=list(children))

    @property
    def is_folder(self) -> bool:
        return self.children is not None


class FakeBackend:
    def __init__(self) -> None:
        self.copy_calls: list[str] = []

    def list_items(self, folder: list[FakeItem]):
        return list(folder)

    def item_name(self, item: FakeItem) -> str:
        return item.name

    def item_is_folder(self, item: FakeItem) -> bool:
        return item.is_folder

    def item_size(self, item: FakeItem) -> int:
        assert item.data is not None
        return len(item.data)

    def item_folder(self, item: FakeItem) -> list[FakeItem]:
        assert item.children is not None
        return item.children

    def copy_file(self, item: FakeItem, destination_folder: Path) -> None:
        assert item.data is not None
        self.copy_calls.append(item.name)
        (destination_folder / item.name).write_bytes(item.data)


class PeriodTests(unittest.TestCase):
    def test_month_matches_only_the_requested_month_prefix(self) -> None:
        period = Period.parse("202610")

        self.assertTrue(period.matches("202610_a"))
        self.assertTrue(period.matches("202610_"))
        self.assertFalse(period.matches("202610"))
        self.assertFalse(period.matches("202611_a"))
        self.assertFalse(period.matches("x202610_a"))

    def test_year_matches_only_valid_month_folders(self) -> None:
        period = Period.parse("2026")

        self.assertTrue(period.matches("202601_a"))
        self.assertTrue(period.matches("202612_archive"))
        self.assertFalse(period.matches("202600_a"))
        self.assertFalse(period.matches("202613_a"))
        self.assertFalse(period.matches("20260_a"))
        self.assertFalse(period.matches("202601"))

    def test_rejects_invalid_periods(self) -> None:
        for value in ("", "20261", "202600", "202613", "abcd", "2026_"):
            with self.subTest(value=value), self.assertRaises(ValueError):
                Period.parse(value)


class SelectionTests(unittest.TestCase):
    def test_collects_sorted_matching_folders_and_nested_files(self) -> None:
        backend = FakeBackend()
        storage = [
            FakeItem.folder("202611_a", FakeItem("IMG_3.MOV", b"333")),
            FakeItem.folder(
                "202610_b",
                FakeItem.folder("Edited", FakeItem("IMG_2.JPG", b"22")),
            ),
            FakeItem.folder("202610_a", FakeItem("IMG_1.JPG", b"1")),
            FakeItem.folder("202510_a", FakeItem("OLD.JPG", b"old")),
            FakeItem("202610_not-a-folder", b"ignored"),
        ]

        selection = collect_source_selection(backend, storage, Period.parse("202610"))

        self.assertEqual(selection.folder_names, ("202610_a", "202610_b"))
        self.assertEqual(
            [source.relative_parts for source in selection.files],
            [
                ("202610_a", "IMG_1.JPG"),
                ("202610_b", "Edited", "IMG_2.JPG"),
            ],
        )
        self.assertEqual(selection.total_size, 3)

    def test_raises_when_no_folder_matches(self) -> None:
        with self.assertRaisesRegex(IPhoneCopyError, "没有匹配"):
            collect_source_selection(
                FakeBackend(),
                [FakeItem.folder("202510_a")],
                Period.parse("202610"),
            )

    def test_rejects_case_insensitive_windows_path_collision(self) -> None:
        storage = [
            FakeItem.folder(
                "202610_a",
                FakeItem("IMG.JPG", b"one"),
                FakeItem("img.jpg", b"two"),
            )
        ]

        with self.assertRaisesRegex(IPhoneCopyError, "名称冲突"):
            collect_source_selection(FakeBackend(), storage, Period.parse("202610"))


class CopyAndVerificationTests(unittest.TestCase):
    def _selection(self) -> tuple[FakeBackend, SourceSelection]:
        backend = FakeBackend()
        first = FakeItem("IMG_1.JPG", b"photo")
        second = FakeItem("IMG_2.MOV", b"video-data")
        selection = SourceSelection(
            folder_names=("202610_a",),
            files=(
                SourceFile(("202610_a", first.name), len(first.data or b""), first),
                SourceFile(
                    ("202610_a", "Edited", second.name),
                    len(second.data or b""),
                    second,
                ),
            ),
        )
        return backend, selection

    def test_copies_files_then_verifies_paths_count_and_size(self) -> None:
        backend, selection = self._selection()
        with tempfile.TemporaryDirectory() as temporary_directory:
            destination = Path(temporary_directory)

            copied, skipped = copy_selection(
                backend,
                selection,
                destination,
                timeout=1,
                poll_interval=0.001,
            )
            report = verify_selection(selection, destination)

            self.assertEqual((copied, skipped), (2, 0))
            self.assertEqual(backend.copy_calls, ["IMG_1.JPG", "IMG_2.MOV"])
            self.assertTrue(report.success)
            self.assertEqual(report.expected_count, 2)
            self.assertEqual(report.actual_count, 2)
            self.assertEqual(report.expected_size, 15)
            self.assertEqual(report.actual_size, 15)

    def test_skips_an_existing_file_with_the_same_size(self) -> None:
        item = FakeItem("IMG.JPG", b"new")
        backend = FakeBackend()
        selection = SourceSelection(
            ("202610_a",),
            (SourceFile(("202610_a", item.name), 3, item),),
        )
        with tempfile.TemporaryDirectory() as temporary_directory:
            destination = Path(temporary_directory)
            target_folder = destination / "202610_a"
            target_folder.mkdir()
            (target_folder / item.name).write_bytes(b"old")

            copied, skipped = copy_selection(backend, selection, destination)

            self.assertEqual((copied, skipped), (0, 1))
            self.assertEqual(backend.copy_calls, [])

    def test_requires_overwrite_for_a_different_existing_file(self) -> None:
        item = FakeItem("IMG.JPG", b"replacement")
        backend = FakeBackend()
        selection = SourceSelection(
            ("202610_a",),
            (SourceFile(("202610_a", item.name), len(item.data or b""), item),),
        )
        with tempfile.TemporaryDirectory() as temporary_directory:
            destination = Path(temporary_directory)
            target_folder = destination / "202610_a"
            target_folder.mkdir()
            target = target_folder / item.name
            target.write_bytes(b"short")

            with self.assertRaisesRegex(IPhoneCopyError, "--overwrite"):
                copy_selection(backend, selection, destination)

            copied, skipped = copy_selection(
                backend,
                selection,
                destination,
                overwrite=True,
                timeout=1,
                poll_interval=0.001,
            )
            self.assertEqual((copied, skipped), (1, 0))
            self.assertEqual(target.read_bytes(), b"replacement")

    def test_verification_detects_missing_extra_and_wrong_size_files(self) -> None:
        _, selection = self._selection()
        with tempfile.TemporaryDirectory() as temporary_directory:
            destination = Path(temporary_directory)
            root = destination / "202610_a"
            root.mkdir()
            (root / "IMG_1.JPG").write_bytes(b"wrong-size")
            (root / "EXTRA.JPG").write_bytes(b"extra")

            report = verify_selection(selection, destination)

            self.assertFalse(report.success)
            self.assertEqual(report.missing, ("202610_a\\Edited\\IMG_2.MOV",))
            self.assertEqual(report.unexpected, ("202610_a\\EXTRA.JPG",))
            self.assertEqual(len(report.wrong_size), 1)


class WindowsMtpBackendCompatibilityTests(unittest.TestCase):
    def _backend_without_com(self) -> WindowsMtpBackend:
        backend = object.__new__(WindowsMtpBackend)
        backend.ENUM_RETRY_DELAY = 0
        return backend

    def test_named_item_search_stops_before_broken_later_nodes(self) -> None:
        class Item:
            def __init__(self, name: str) -> None:
                self.Name = name

        class Items:
            Count = 3

            def __init__(self) -> None:
                self.requested: list[int] = []

            def Item(self, index: int):
                self.requested.append(index)
                if index == 2:
                    raise AssertionError("stale namespace node was accessed")
                return (Item("Local drive"), Item("Apple iPhone"))[index]

        items = Items()

        class Folder:
            def Items(self):
                return items

        result = self._backend_without_com()._find_named_item(
            Folder(), "Apple iPhone", "设备"
        )

        self.assertEqual(result.Name, "Apple iPhone")
        self.assertEqual(items.requested, [0, 1])

    def test_get_folder_property_is_not_called_when_com_object_is_callable(self) -> None:
        class CallableFolder:
            def __init__(self) -> None:
                self.call_count = 0

            def __call__(self):
                self.call_count += 1
                return "wrong default member"

        folder = CallableFolder()
        item = type("Item", (), {"GetFolder": folder})()

        result = WindowsMtpBackend.item_folder(item)

        self.assertIs(result, folder)
        self.assertEqual(folder.call_count, 0)

    def test_item_size_prefers_mtp_system_size_property(self) -> None:
        class Item:
            Name = "IMG_0001.JPG"
            Size = 0

            @staticmethod
            def ExtendedProperty(name: str):
                if name == "System.Size":
                    return 2_621_093
                return None

        self.assertEqual(WindowsMtpBackend.item_size(Item()), 2_621_093)

    def test_item_size_falls_back_when_extended_property_is_unavailable(self) -> None:
        class Item:
            Name = "local.bin"
            Size = 42

            @staticmethod
            def ExtendedProperty(name: str):
                raise AttributeError(name)

        self.assertEqual(WindowsMtpBackend.item_size(Item()), 42)

    def test_complete_folder_enumeration_retries_transient_failure(self) -> None:
        good_item = type("Item", (), {"Name": "IMG.JPG"})()

        class BrokenItems:
            Count = 1

            @staticmethod
            def Item(index: int):
                raise RuntimeError("device busy")

        class GoodItems:
            Count = 1

            @staticmethod
            def Item(index: int):
                return good_item

        class Folder:
            def __init__(self) -> None:
                self.calls = 0

            def Items(self):
                self.calls += 1
                return BrokenItems() if self.calls == 1 else GoodItems()

        folder = Folder()
        result = list(self._backend_without_com().list_items(folder))

        self.assertEqual(result, [good_item])
        self.assertEqual(folder.calls, 2)


if __name__ == "__main__":
    unittest.main()
