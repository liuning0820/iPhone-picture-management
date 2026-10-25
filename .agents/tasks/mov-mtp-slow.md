# MOV 复制时反复等待 Windows MTP 的调查报告

## 摘要结论

日志中的“仍在等待 Windows MTP（已等待 10 秒/20 秒/30 秒）”是复制流程的通用等待心跳，不是针对 `.MOV` 扩展名的特殊分支。对 `CTVE0260.MOV` 而言，父进程已经启动了一个隔离的 MTP 复制子进程；只要该子进程仍存活，父进程每 10 秒报告一次已等待时间。这个消息本身不能证明复制完全没有进展，也不能单独证明文件已经卡死。

脚本实际通过 Windows Shell 的 `Shell.Application` 和 `CopyHere` 复制文件，而不是把 iPhone 当作普通磁盘直接读取。复制子进程在 `CopyHere` 返回后还会等待目标文件达到源文件的预期字节数，并且等待目标文件不再被其他 Windows 进程独占写入。因此，等待可能发生在 Shell/MTP 调用本身，也可能发生在目标文件仍在生成或仍被占用的阶段。

MOV 通常比照片慢，最有根据的代码层解释是：视频通常需要传输更多字节，而且该程序一次只等待并处理一个文件；但代码没有按扩展名降低 MOV 优先级、转码或采用另一种视频协议。具体文件是否因 iCloud 原片未下载、iPhone/USB/MTP 响应、Windows Shell 行为或其他原因变慢，必须在真实 Windows、真实 iPhone 和该文件上运行时测量，不能仅凭仓库代码确定。

## 证据与调用流程

### 等待消息的准确触发条件

在 `iphone_copy.py:322-376` 的 `WindowsMtpBackend.copy_file` 中，脚本执行以下逻辑：

1. 使用 `multiprocessing` 的 `spawn` 上下文创建子进程，目标是
   `_copy_mtp_file_worker`。
2. 子进程启动后，父进程记录 `started` 时间，并把下一次状态报告安排在
   `started + 10.0` 秒。
3. 父进程进入 `while process.is_alive()` 循环。当超过下一次报告时间且传入了
   `status_callback` 时，它调用回调并生成“仍在等待 Windows MTP”消息，然后把下次
   报告时间再推迟 10 秒。
4. `copy_selection` 在 `iphone_copy.py:601-622` 传入了
   `report_waiting` 回调，最终由 `run` 在 `iphone_copy.py:777-785` 使用
   `_print_progress` 打印，所以用户看到的是带有 `[5/121]`、相对路径和源大小的
   进度行。

因此，示例中的 10 秒、20 秒、30 秒表示同一个文件的 MTP 子进程持续存活了至少这些
时间。它们不是三个重试，也不是脚本已经实际重传了三次；代码没有在这个循环中重启
复制。消息也不依赖文件名后缀，任何耗时超过 10 秒的文件都可以触发同样的提示。

### 子进程到底在等待什么

`iphone_copy.py:403-438` 的 `_copy_mtp_file_worker` 展示了子进程的完整阶段：

- 创建 `WindowsMtpBackend`，初始化 COM，并定位 `Apple iPhone` 和
  `Internal Storage`。
- 按 `relative_parts` 重新打开源文件夹和源文件。
- 用目标目录的 Shell namespace 调用
  `destination.CopyHere(source_item, copy_flags)`。
- `CopyHere` 返回后，以 250 毫秒间隔检查目标文件，直到
  `_file_has_expected_size(target, expected_size)` 返回真。

这意味着“等待”至少有两种可能：

- Windows Shell 的 `CopyHere` 调用自身在 MTP 操作中阻塞。代码把它放在子进程中，
  以便父进程仍能按时报告状态，并在超时后终止它。
- `CopyHere` 已返回，但目标文件尚未创建、仍在增长，或仍被复制进程占用。此时
  `_file_has_expected_size` 会继续返回假。

`_file_has_expected_size` 位于 `iphone_copy.py:550-559`。它同时要求目标是普通文件、
大小等于源清单中的预期大小，并且在 Windows 上可以以“无共享”方式打开；只要复制器
仍持有写入句柄，检查就不会结束。

### 超时和成功条件

父进程的默认单文件超时是 600 秒，定义在 `build_parser` 的
`iphone_copy.py:739-746`。达到超时后，`copy_file` 终止子进程，必要时再调用
`kill`，并抛出“复制超时，已终止卡住的 Windows MTP 调用”。`--timeout 1800` 只
增加等待上限，不会提高 MTP 的实际吞吐量。

子进程只有在目标文件通过预期大小和独占读取检查后才向队列发送 `("ok", "")`。
父进程收到成功后，`copy_selection` 还会再次检查目标文件；所有文件完成后，
`verify_selection` 会按相对路径、数量和字节大小做整批校验。这些行为分别见
`iphone_copy.py:428-431`、`iphone_copy.py:372-390`、`iphone_copy.py:621-629` 和
`iphone_copy.py:641-687`。

### MTP 文件的来源大小和实际复制方式

`WindowsMtpBackend.item_size`（`iphone_copy.py:294-318`）优先读取 Windows 的
`System.Size`，因为代码注释和实现都指出 MTP 的 `FolderItem.Size` 经常对 iPhone
文件报告 0；读取不到扩展属性时才回退到 `item.Size`。这个大小用于建立源清单、
判断已有目标文件能否跳过，也用于判断异步复制是否完成。

源清单由 `collect_source_selection`（`iphone_copy.py:459-507`）递归建立，照片和
视频都作为普通非文件夹 `SourceFile` 处理；没有针对 MOV 的专门复制函数。实际复制
使用 Windows Shell 的 MTP namespace、`CopyHere` 和
`FOF_SILENT | FOF_NOCONFIRMATION | FOF_NOCONFIRMMKDIR | FOF_NOERRORUI` 标志，见
`iphone_copy.py:122-126`、`iphone_copy.py:424-426`。

复制是串行的：`copy_selection` 在 `iphone_copy.py:561-629` 中按源清单顺序逐个调用
`backend.copy_file`，当前文件成功前不会开始下一个文件。因此一个视频的 MTP 延迟会
直接阻塞后续 `[6/121]` 等文件。

## 为什么 MOV 可能比照片慢

以下是可以从代码和运行模型合理得出的结论：

- 视频通常比单张照片大，MTP 需要传输的字节更多；8.64 MiB 并不保证设备端、USB
  链路或 Shell 端能在几秒内完成。
- 脚本对每个文件都重新启动复制子进程、初始化 COM、重新定位设备/存储和源路径，
  这些固定开销对大文件相对较小，但会叠加在每个文件上。
- Windows Shell 的 MTP 复制是异步接口，程序只能等待 `CopyHere` 和目标文件状态，
  没有可见的字节级进度，也没有并行复制。视频文件因此更容易让这段等待超过 10 秒。
- iPhone 通过 Windows MTP 暴露的文件可能需要设备端准备或从 iCloud 获取原片；仓库
  README 的故障排除明确要求先让 iPhone 上的全分辨率原片下载完成。这是运行环境因素，
  不是 `iphone_copy.py` 对 MOV 做了转码的证据。
- 代码没有按 `.MOV` 触发转码、格式转换或特殊重试；也没有证据表明 8.64 MiB 的
  `CTVE0260.MOV` 一定比某张照片慢，只有实际耗时数据才能确认具体瓶颈。

## 测试和验证范围

现有测试位于 `tests/test_iphone_copy.py`。它覆盖了清单收集、路径和大小校验、跳过
同大小文件、覆盖冲突、MTP 大小属性回退、枚举重试，以及
`test_windows_copy_terminates_a_worker_blocked_inside_copyhere` 对阻塞 `CopyHere` 的
超时终止行为。测试使用内存假对象，不连接真实 iPhone，也不调用真实
`Shell.Application`，所以不能验证真实 MTP 吞吐量、设备端等待、USB 速度、iCloud
下载或 MOV 与照片的相对速度。

本次只读运行结果为：

```text
python -m unittest discover -s tests -v
Ran 16 tests in 0.052s
OK
```

README 的运行和故障排除说明（`README.md` 的“Usage”“Verification”和
“Troubleshooting”部分）与上述实现一致：程序要求 iPhone 解锁并信任 Windows，默认
每个文件等待 600 秒，可用 `--timeout 1800` 延长等待；重试运行时，已经有相同字节数
的目标文件会跳过。

## 结论和可操作建议

### 现在即可尝试的操作

1. 保持 iPhone 解锁、屏幕唤醒，并确认已经选择“信任”；不要在复制期间断开 USB。
2. 按 README 的建议，在文件资源管理器中打开
   `This PC > Apple iPhone > Internal Storage`，确认相同文件夹可见；关闭正在主动
   复制 iPhone 文件的资源管理器窗口。
3. 如果该 MOV 只在 iCloud 中而未在手机本地，先在 iPhone 上打开或下载它，等原片
   完整可用后再复制。
4. 用文件资源管理器或 Windows Photos 复制同一个 `CTVE0260.MOV` 做对照。如果对照
   也慢，问题更可能在设备、USB、iCloud 原片准备或 Windows MTP/Shell；如果对照很快
   而脚本慢，则需要进一步测量脚本的子进程启动、源路径定位和目标文件收尾阶段。
5. 需要允许单个大文件更长时间时使用 `--timeout 1800`。这只是避免过早失败；如果
   一直没有目标文件或 MTP 永久阻塞，不应把增大超时当作性能修复。
6. 对未来使用 Windows 的兼容性，可评估 README 提到的 iPhone
   `Settings > Camera > Formats > Most Compatible`。这可能改善后续文件兼容性，但
   不能保证修复当前文件的 MTP 等待。

### 值得考虑的代码改进

- 将“正在执行 Shell/MTP 调用”和“目标文件已创建但仍在传输/仍被占用”分成不同的
  状态消息，并记录 `CopyHere` 返回时间、目标文件首次出现时间和最终完成时间。这样
  才能解释 10/20/30 秒具体耗在哪个阶段；当前代码只能统一显示“等待 Windows MTP”。
- 在进度输出中增加目标文件当前大小或最近一次大小变化时间（若安全可读），但必须保留
  最终大小和独占读取检查，否则可能在文件尚未真正完成时误报成功。
- 可减少每个文件重复初始化设备和 COM 的固定开销，但必须重新设计 MTP/COM 对象的
  线程或进程生命周期，并验证 Shell 对象在同一 COM apartment 中安全使用；这不是仅改
  日志即可完成的低风险优化。
- 不建议仅因为扩展名是 `.MOV` 就盲目并行复制。多个并行 `CopyHere` 可能增加 iPhone
  和 Windows Shell 的争用；任何并行方案都必须用真实设备测量，并继续保证目标文件
  完整性和最终清单校验。
- 如果希望改变底层传输方式，应先确认 Windows MTP 是否提供可靠的直接流接口；README
  已说明当前 Shell/MTP 环境不能可靠暴露直接文件流，因此不能仅凭现有单元测试安全地
  替换为普通 `shutil.copy`。

上述代码路径和现有测试可以确认“消息何时出现”和“复制何时成功/超时”；无法仅从仓库
确认 `CTVE0260.MOV` 的具体慢因。需要在同一设备和连接上采集分阶段时间，才能进一步
区分文件大小、iCloud 原片准备、MTP 响应、USB 链路或 Windows Shell 的影响。
