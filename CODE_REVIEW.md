# 证件照工具 — 代码审查与 QFluentWidgets 优化报告

审查范围：UI 层全部源码（`app.py`、`styles.py`、`ui/theme.py`、`ui/widgets.py`、
`ui/preview_pane.py`、`ui/process_view.py`、`ui/layout_view.py`、
`ui/layout_left_pane.py`、`ui/queue_pane.py`）。

约束：不新增功能、不改变窗口结构与按钮交互逻辑、运行效果与原版保持一致。

---

## 一、问题与缺陷清单

### A. 逻辑 / 崩溃风险

| # | 位置 | 问题 | 风险 |
|---|------|------|------|
| A1 | `app.py` `_process_worker` / `_batch_process_worker` | 两个 worker 内存有 `if bgr_color is None: self.pages["process"].get_background_color_bgr()` 兜底，属于**在子线程访问 Qt 控件**。实际调用方已主线程传参使该分支成为死代码，但一旦参数漏传就会跨线程操作 QWidget | 死锁 / 随机崩溃（历史版本曾因此出现"处理完成但界面不更新"） |
| A2 | `app.py` `MainThreadInvoker` | 20ms 轮询 QTimer 常驻；窗口关闭不停止定时器、不置 `_stop_batch` | 关闭窗口后线程/定时器残留，进程无法干净退出 |
| A3 | `app.py` `_open_download_dialog` | `dlg.exec()` 后未销毁弹窗对象 | 弹窗对象驻留内存 |
| A4 | `app.py` `_load_image` | 异常分支 `print` 调试信息；函数体内重复 `import cv2` | 调试输出外泄 + 无谓重复导入 |

### B. 冗余 / 死代码

| # | 位置 | 问题 |
|---|------|------|
| B1 | `app.py` | 未使用导入 `qfluentwidgets as fw`、`QPixmap`、`TitleLabel` |
| B2 | `app.py` | `_MODEL_DISPLAY_NAMES` 与 `_current_matting_model_display()` 定义后从未调用 |
| B3 | `ui/widgets.py` | 未使用导入 `QApplication`、`QSizePolicy` |
| B4 | `ui/widgets.py` `_cv2_to_pixmap` | 每次调用都执行 `import cv2` + `from PIL import Image` |
| B5 | `ui/theme.py` | 未使用导入 10 个：`QWidget`、`QApplication`、`QSize`、`QFont`、`FluentIcon`、`BodyLabel`、`CaptionLabel`、`StrongBodyLabel`、`SubtitleLabel`、`TitleLabel` |
| B6 | `ui/preview_pane.py` | 未使用导入 `QSize`、`QPen`、`QImage`、`SubtitleLabel`、`FluentIcon` |
| B7 | `ui/layout_view.py` | 未使用导入 `QGridLayout`（早已改用 QVBoxLayout） |
| B8 | `ui/process_view.py` | 未使用导入 `BodyLabel`；4 个 `_tab_xxx(parent=None)` 的 `parent` 形参从未使用；3 处 `[(val,text) for val,text in [...]]` 恒等列表推导 |
| B9 | `app.py` `_show_page` | if/else 两个分支都以 `setVisible(True)` 收尾，重复 |
| B10 | `app.py` `_refresh_right_previews` | `if layout_page is not None:` 恒真死判断（`self.pages["layout"]` 构造即存在） |

### C. 内存泄漏

| # | 位置 | 问题 |
|---|------|------|
| C1 | `ui/widgets.py` `ThumbnailList.set_items` | **`QListWidget.clear()` 只删除 item，不会销毁 `setItemWidget` 挂上去的 ThumbItem 控件**。每次处理完重建缩略图都会泄漏一批 QWidget —— 这是真实内存泄漏 |
| C2 | `app.py` 下载弹窗 | `exec()` 后未 `deleteLater()`（同 A3） |

### D. 信号槽

| # | 位置 | 问题 |
|---|------|------|
| D1 | `ui/widgets.py` `TabStrip` | `pivot.currentItemChanged` 连到 `switch()`，而 `switch()` 内部又调 `pivot.setCurrentItem()`，形成「setCurrentItem → 信号 → switch → setCurrentItem」回环。当前靠"值未变则不发射信号"侥幸不递归，属隐患 |
| D2 | 全局 | 其余 lambda 均已用默认参数捕获（如 `lambda i=idx: ...`），无闭包陷阱 |

### E. UI / 布局 / 主题

| # | 位置 | 问题 |
|---|------|------|
| E1 | `ui/widgets.py` `ThumbnailList` | 滚动条设为 `ScrollBarAlwaysOff`：内容超出可视区后**完全无法滚动**，属功能缺陷（此前为隐藏"两个杆杆"而设，矫枉过正） |
| E2 | `ui/theme.py` `apply_theme` | 向 `QApplication` 全局样式表追加 SpinBox 修复 QSS。Fluent 控件级样式优先级更高，全局写法**实际不生效**；且污染全局样式、影响主题跟随 —— 无效死代码 |
| E3 | `widgets.py` + `layout_left_pane.py` | SpinBox 配色 QSS 各写一份、几乎完全相同，重复 |
| E4 | `ui/layout_view.py` `_build_export_card` | 两个按钮 QSS 字符串定义在函数体内，每次建卡重复构造 |
| E5 | `ui/theme.py` 各组 QSS、`app.py` `_build_header` | 大量硬编码深色值（`#FAFAFA`、`#71717A`、`#27272A`、`#1F1F23`、`#A1A1AA`、`#22C55E`、`#52525B`、`#18181B` 等）。切浅色主题会失效/看不清。**为保证"运行效果与原版一致"，本次未改动这些配色，仅收敛 SpinBox 一处，其余在此标记** |

---

## 二、改动汇总说明

### 1. 删除的代码

- 删除 `_process_worker` / `_batch_process_worker` 中跨线程访问 UI 控件的兜底分支，`bgr_color` / `render_mode` 改为**必传参数**，并加注释警示禁止在子线程访问 Qt 控件。
- 删除 `theme.apply_theme()` 中向 `QApplication` 全局样式表追加的 SpinBox QSS（无效且污染主题）。
- 删除调试 `print` 2 处（`_check_models` 模型缺失警告、`_load_image` 读取失败）。
- 删除死代码 `_MODEL_DISPLAY_NAMES`、`_current_matting_model_display()`。
- 删除未使用导入共 20 个（app.py 3、widgets.py 2、theme.py 10、preview_pane.py 5、layout_view.py 1、process_view.py 1，含跨文件重复计数）。
- 删除 `_refresh_right_previews` 中恒真的 `if layout_page is not None:` 嵌套。
- 删除 `ui/process_view.py` 4 个未使用的 `parent=None` 形参。

### 2. 合并 / 封装

| 新增 | 作用 |
|------|------|
| `app.py` `_prepare_process(require_selection)` | 抽出 `on_process` / `on_batch_process` 完全相同的前置校验（提示"请先上传图片"→ 模型检查 → 取参），以 `require_selection` 区分"需选中单张"与"队列非空即可" |
| `app.py` `_open_download_dialog()` | 两个下载入口（检查下载 / 高速下载）原代码逐字重复，合并为同一实现 |
| `ui/theme.py` `spinbox_qss(cls)` | 统一 SpinBox / DoubleSpinBox 配色样式，供 `SliderRow` 与排版"数量"框共用，消除重复硬编码 |
| `ui/widgets.py` `_HIDDEN_SCROLLBAR_QSS` | 滚动条压成 0 尺寸的样式常量 |
| `ui/widgets.py` `ThumbnailList._release_item_widgets()` | 释放上一批 item 上挂的控件 |
| `ui/widgets.py` `TabStrip._apply()` | 只切内容栈的纯切换函数，与 `switch()` 分工 |
| `ui/layout_view.py` `_BLOCK_PRIMARY_QSS` / `_BLOCK_SECONDARY_QSS` | 从函数体内提到模块级常量 |
| `ui/process_view.py` 复用 `styles.PROCESS_MODES` | 消除与 styles 常量重复的硬编码列表 |

### 3. 修复项

**内存**
- 修 C1 真实泄漏：`set_items` 前调用 `_release_item_widgets()`，对每个旧 ThumbItem 执行 `setParent(None)` + `deleteLater()`。
- 修 A3/C2：弹窗改为 `try: dlg.exec() finally: dlg.deleteLater()`。

**窗口 / 线程**
- 新增 `closeEvent`：置 `_stop_batch=True`、`processing=False`、调用 `_invoker.stop()`，再交回父类。
- `MainThreadInvoker` 新增 `stop()`：停止轮询定时器并清空待执行回调队列。

**信号槽**
- 修 D1：`pivot.currentItemChanged` 改连 `_apply`（只切 stack，不回头改 Pivot）；`switch()` 保留给外部/初始化调用，`setCurrentItem` 后再调一次 `_apply` 兜底，避免"值未变→信号不发射→内容不同步"。

**UI / 滚动**
- 修 E1：滚动条策略由 `ScrollBarAlwaysOff` 改为 `ScrollBarAsNeeded`，并用 `_HIDDEN_SCROLLBAR_QSS` 把滚动条压成 0 尺寸 —— 视觉上仍看不见"两个杆杆"，但内容超出时可以正常滚动。

**布局 / 结构**
- 窗口、控件位置、按钮交互逻辑**一处未动**；`setContentsMargins` / `setSpacing` / `sizePolicy` 保持原值。
- 仅去除 `_show_page` 中重复的 `setVisible(True)`、去除恒真判断这类不改变行为的冗余。

### 4. 验证结果

offscreen 冒烟测试全部通过：

```
[1] launch OK
[2] TabStrip switch x4 OK（stack 与 Pivot 同步，无回环）
[3] ThumbnailList rebuild x3 OK, count = 3 | hidden-scroll QSS: True
[4] spinbox QSS 由 theme 统一提供，SliderRow 正确引用: True
[5] preview view start = auto；手动设为 original 并刷新后仍为 original（不被强制跳走）
[6] closeEvent OK, invoker timer active = False
```

---

## 三、遗留事项（需人工决策，未自动修改）

1. **主题硬编码**：`theme.py` 各按钮 QSS 与 `app.py` 头部状态区仍为写死深色值。要真正支持浅色主题跟随，需改为读取 `qfluentwidgets` 的 `themeColor()` / `isDarkTheme()` 动态生成，或改用 Fluent 原生控件属性。本次为严守"运行效果与原版一致"未改动。
2. **`_stop_batch` 仅用于批量中止**：界面上没有"停止"按钮，该标志目前只在窗口关闭时使用，属预留字段。
