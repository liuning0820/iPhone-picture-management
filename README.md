# iPhone picture management

This Windows command-line tool copies iPhone photo folders for one month or
one year into a local directory. It reads the iPhone through the Windows MTP
shell, preserves each matching folder, and verifies every destination file by
relative path and byte size.

## Features

The tool focuses on the `YYYYMM_*` folder layout shown under **Apple iPhone >
Internal Storage**.

- Select one month with `YYYYMM`, such as `202610`.
- Select all valid months in one year with `YYYY`, such as `2026`.
- Copy nested photos and videos while preserving their folder structure.
- Skip existing files when their sizes match.
- Refuse conflicting files unless you specify `--overwrite`.
- Detect missing, extra, and wrong-size files after the copy.

## Requirements

You must run the tool on Windows because an iPhone connected through MTP isn't
a normal file-system drive.

- Windows 10 or Windows 11
- Python 3.10 or later
- An iPhone and a data-capable USB cable
- An unlocked iPhone that trusts the Windows computer
- `pywin32`, installed from `requirements.txt`

## Installation

Create an isolated Python environment and install the pinned dependency from
the repository directory.

1. Open PowerShell in this repository.
2. Create and activate a virtual environment:

   ```powershell
   py -m venv .venv
   .\.venv\Scripts\Activate.ps1
   ```

3. Install the dependency:

   ```powershell
   python -m pip install -r requirements.txt
   ```

## Usage

Keep the iPhone connected and unlocked until copying and verification both
finish. The default destination is `%USERPROFILE%\Downloads`.

To copy October 2026, run:

```powershell
python iphone_copy.py 202610
```

This example matches folders such as `202610_a`, `202610_b`, and any other
folder named `202610_*`. It creates those folders directly under Downloads,
for example:

```text
C:\Users\<Windows-user>\Downloads\202610_a
C:\Users\<Windows-user>\Downloads\202610_b
```

To copy every matching month in 2026, run:

```powershell
python iphone_copy.py 2026
```

To inspect the matching folder names, file count, and total size without
copying anything, run:

```powershell
python iphone_copy.py 202610 --list-only
```

To use another destination, run:

```powershell
python iphone_copy.py 202610 --destination "D:\iPhone backup"
```

To see all command options, run:

```powershell
python iphone_copy.py --help
```

## Existing files

The default behavior avoids replacing local data. If a destination file exists
with the same byte size, the tool skips it. If its size differs or a folder
exists where a file must be written, the tool stops with an error.

> **Warning:** `--overwrite` deletes each conflicting destination file before
> copying its iPhone counterpart. It doesn't delete unexpected extra files or
> conflicting directories.

After reviewing the destination, you can replace different-size files with:

```powershell
python iphone_copy.py 202610 --overwrite
```

## Verification

The tool records the source manifest before copying starts. After all transfers
finish, it scans the matching destination folders and compares:

- Every file's relative path.
- Every file's byte size.
- The total file count.
- The total byte size.

Verification succeeds only when all four checks match. Extra files already in
a matching destination folder cause verification to fail, even when all source
files copied correctly. The tool reads source byte sizes from the Windows
`System.Size` property because the basic MTP `FolderItem.Size` property often
reports zero for iPhone files. The check uses sizes rather than content hashes
because the Windows MTP shell doesn't expose direct file streams reliably.

If MTP enumeration still fails, the error includes the failing Windows COM
HRESULT and underlying message. Include that complete error when reporting the
problem; it distinguishes a locked device from a stale or busy Shell node.

The process returns exit code `0` after successful verification, `1` for setup
or copy errors, `2` for a verification mismatch, and `130` if you cancel it.

## Troubleshooting

Most access and timeout failures come from the MTP connection. Try these steps
before running the command again.

- Keep the iPhone unlocked and its screen awake.
- Confirm that you selected **Trust** on the iPhone.
- Open **This PC > Apple iPhone > Internal Storage** in File Explorer to verify
  that Windows can see the folders.
- Reconnect the USB cable if the device or storage isn't listed.
- Close File Explorer windows that are actively copying from the iPhone.
- Increase the per-file timeout for a large video, for example,
  `--timeout 1800`.
- Use `--device-name` or `--storage-name` if Windows shows different names.

A failed or canceled run can leave a partial destination file. On the next run,
the tool reports that file as a conflict. Inspect it, and then remove it or use
`--overwrite` to copy that source file again.

## Tests

The unit tests use an in-memory fake MTP source, so they don't require an
iPhone or `pywin32`.

Run the test suite from the repository root:

```powershell
python -m unittest discover -s tests -v
```

## Next steps

Run `--list-only` first to confirm the selection and estimated transfer size.
Then run the same period without `--list-only` and keep the phone connected
until the final success message appears.
