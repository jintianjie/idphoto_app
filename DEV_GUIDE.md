# ID 证件照制作工具 · 开发者交接文档（DEV_GUIDE）

> 适用对象：接手本工程继续开发的开发者 / 另一套 AI。
> 最后更新：2026-08-19。覆盖到：顶部导航双页（证件照处理 / 排版打印）、统一右侧单预览窗、处理只产「通用照」（尺寸下放到排版页）、排版覆盖裁切不变形、背景线程刷新打印机、标题栏模型状态显示、成品证件照自动导入/去尺寸、按规格分组排版、出血线、空白区切割线开关、批量处理完自动跳转排版页、删除「一键排版」、窗口不撑大、缩略图统一 100×100 / 右侧列宽 120。
> 阅读完本文即可独立运行、测试、修改本工程，无需回溯历史对话。

---

## 1. 项目概述

一个**纯本地离线**的证件照制作桌面工具（Windows），基于 [HivisionIDPhotos](https://github.com/Zeyi-Lin/HivisionIDPhotos) 的推理思路自行实现：

- 输入：用户照片（支持常见格式批量导入）
- 处理：AI 抠图（MODNet / Hivision-MODNet / RMBG-1.4 / BiRefNet-lite 任选）→ 人脸检测（RetinaFace 默认 / 可选 MTCNN）→ 裁剪对齐 → 换底色 / 渐变背景 / HEX 自由底色 → 美颜 / 水印 / 横向翻转
- 输出：**通用照 + 高清照**（处理阶段不分尺寸，一次处理产一张固定构图的「通用照」1400×1000；尺寸在排版页按规格设置，排版时**覆盖式居中裁切**，只裁溢出、绝不拉伸变形）；单一尺寸排版 / 混合尺寸排版（按规格分组、货架式铺排、可带裁剪线）；导出 PNG（带 DPI）/ JPEG（按 KB 限制）
- 技术栈：`tkinter` + `ttk`（clam 主题）做 GUI；`onnxruntime` 跑 ONNX 模型；`opencv-python` / `numpy` / `Pillow` 做图像处理。无网络请求、无第三方云服务。

**用户铁律（必须遵守，来自需求方，优先级高于一切美观诉求）：**
1. **业务逻辑不动**，只做 UI 优化 / 样式修复 / 布局调整。
2. **深色主题**：全局背景 `#18181B`，橙色强调色 `#F97316`。
3. **不使用纯白边框**——边框统一用灰白（`#3F3F46` 等），避免刺眼。
4. **按钮要有 4 态**（默认 / 悬停 / 按下 / 禁用），禁用态不能用「黑字深底」这种看不清的组合。
5. **沙箱无 DISPLAY**：GUI 无法在 WorkBuddy 沙箱里直接 `python app.py` 跑，必须在用户真机（Python 3.10 + tkinter）验证视觉效果。

---

## 2. 目录结构与各文件职责

```
idphoto_app/
├── app.py                  # 【核心】主程序入口。IDPhotoApp 类：建窗、配样式、页面路由、事件分发、
│                           #         并统一持有「右侧单预览窗 + 视图切换」(right_pane) 及其刷新逻辑。
├── pages.py                # 四个页面视图类 + 缩略图队列控件 + 通用辅助函数。
├── ui_widgets.py           # 自定义控件：TopNav / SidebarNav / ColorSwatch / ColorSwatchGroup /
│                           #         draw_empty_placeholder；高清图标（PIL 4× 超采样）绘制。
├── styles.py               # 【样式唯一来源】所有颜色 / 字体 / 间距 / 尺寸常量集中在此。
│                           #         禁止在其它文件散写样式数值。
├── inference.py            # 推理层：MattingEngine（4 种抠图模型）、FaceDetector（RetinaFace）、
│                           #         EngineManager（懒加载 + 会话管理）。
├── photo_processor.py      # 业务逻辑层：PhotoProcessor.process_id_photo / process_matting_only。
├── layout_engine.py        # 排版引擎：LayoutItem + LayoutEngine（单一/混合尺寸排版，货架式铺排）。
├── image_utils.py          # 图像处理工具：加背景、渐变、美颜、水印、DPI/KB 导出。
├── test_all.py             # 7 项综合测试（见 §7）。注意：模型/测试图路径硬编码在桌面，需改成本机路径。
├── requirements.txt        # numpy / opencv-python / onnxruntime / Pillow
├── run.bat                 # Windows 启动脚本，自动按优先级定位 Python 解释器后跑 app.py。
├── idphoto_workflow.svg    # 工作原理思维图（人像→抠图→检测→裁剪→换底→排版→导出）。
├── model/                  # 【必须随工程一起】5 个 ONNX 模型（见 §6）。
└── test_output/            # 测试生成的预览/排版图（可忽略 / 重新生成）。
```

### 2.1 各模块关键符号（接手时直接按名定位）

**app.py —— `class IDPhotoApp`**（重点：右侧预览由 app 统一管理，页面只调刷新）
- 窗口/样式：`_setup_window`、`_setup_styles`、`_build_titlebar`（含右上角模型状态）、`_build_titlebar_divider`
- 导航：`_build_top_nav`（顶部水平导航，**2 个入口**：证件照处理 / 排版打印，右侧「v1.0·本地离线」）、`_show_page(page_id)`
- 内容区：`_build_content_area`（拆成 `main_pane` 左/中功能面板 + `right_pane` 右侧单预览窗 + 视图切换）
- 预览核心：`_build_right_previews(parent)`、`_update_preview_canvas(canvas, image, placeholder)`、`_refresh_right_previews(page_id=None)`
- 状态/队列：`original_image` / `original_file_path` 属性、`on_add_images`、`_load_image`、`on_select_queue_image`、`_select_image_at`
- 处理：`on_process`（处理选中）、`on_batch_process`（批量全部）、`_batch_process_worker`、`_process_one`、`_on_process_done`
- 排版/导出：`on_generate_mixed_layout`（基于排版页「规格/宽/高/数量」配置 + `ready_photos` 成品列表覆盖裁切生成）、`_rebuild_layout_from_sources`、`_generate_layout_from_queue`、`on_export`
- 模型状态：`_MODEL_DISPLAY_NAMES`、`_current_matting_model_display()`、`_update_model_status(in_use=False)`（标题栏「模型: MODNet 已就绪/使用中/未找到」，绿/橙/红点）
- 打印机：`_list_printers`（Windows 调 `win32print.EnumPrinters`，枚举本地+连接/网络打印机）、`on_printer_properties`；刷新入口在 `pages.py` 的 `_refresh_printer_list` + `_apply_printer_list`（**后台线程枚举，避免网络打印机阻塞 UI**，见 §4.7）
- 成品证件照：`ready_photos` 状态列表、`on_add_ready_photos`、`on_remove_ready_photo`、`on_clear_ready_photos`、`_center_crop_to_size`（覆盖+居中裁切，无损不变形）；处理好的通用照自动导入成品列表，尺寸完全由排版页控制

**pages.py**
- 辅助：`_ratio_fit`、`_make_thumbnail`、`_normalize_queue_items`、`add_slider_row`（抽出的滑块行，全工程复用）
- `class ThumbnailList(ttk.Frame)`：左侧缩略图队列（单击选中 / 双击放大）
- `class ProcessPage`：`_build_left_column` / `_build_middle_column`；
  `show_result_gallery` / `_hide_result_gallery`（中列底部缩略图画廊，双击放大调 `app._refresh_right_previews`）；
  `update_previews(std, hd)`（→ `app._refresh_right_previews`）；`get_*` 系列取参数
- `class LayoutPage`：`_build_left_column` / `_build_middle_column`（混合尺寸排版 + 成品证件照面板 + 导出）；
  `set_layout_pages`、`_refresh_page`、`update_preview`（均 → `app._refresh_right_previews`）；
  `get_mixed_items`、`get_paper_px`；成品面板：`refresh_ready_list`、`get_selected_ready_idx`
  （成品尺寸已移除：尺寸统一由排版页规格控制，处理好的通用照自动进成品列表后由排版覆盖裁切）

**ui_widgets.py**
- `TopNav(tk.Frame)`：顶部水平导航，API 与 `SidebarNav` 一致（`set_active` / `_hover` / `_draw_icon`），选中项底部橙色强调条 + 暖色高亮背景。
- `SidebarNav(tk.Frame)`：早期左侧导航（现已不用，保留为可复用组件）。
- `ColorSwatch` / `ColorSwatchGroup`：圆形色板（高清绘制）。
- `draw_empty_placeholder(canvas, text, icon)`：预览窗空状态占位图。
- 图标：`_create_icon_photo` / `create_color_swatch_photo`（PIL 4× 超采样 + LANCZOS，保证清晰）。

**styles.py**：常量全在这。常用：`COLOR_BG=#18181B`、`COLOR_PRIMARY=#F97316`、`COLOR_SURFACE=#27272A`、`COLOR_BORDER=#3F3F46`；
尺寸：`TOP_NAV_HEIGHT=56`、`PREVIEW_COL_WIDTH=470`、`THUMB_COL_WIDTH=324`、`THUMB_IMG_WIDTH=140`、`THUMB_IMG_WIDTH_RIGHT=280`（最右缩略图列专用更大边长）、`PREVIEW_A4_WIDTH=440/HEIGHT=622`（右侧预览画布，A4 竖版比例）；
预设：`PRESET_SIZES`（一寸/二寸…）、`PRESET_COLORS`、`MATTING_MODELS`、`FACE_MODELS`、`PAPER_PRESETS`。

**inference.py**
- `MattingEngine`：`process(input_image_bgr) -> BGRA`；支持 4 种模型的前/后处理；`_hollow_out_fix` 修复发丝空洞。
- `FaceDetector`（RetinaFace）：`detect(image_bgr, confidence_threshold) -> list[[x1,y1,x2,y2,score, lm×10], ...]`。
- `MTCNNFaceDetector`（可选，需 `pip install mtcnn-runtime`）：基于 mtcnn-runtime，输出格式与 `FaceDetector` 完全一致；无人脸时返回 `[]`（上层按"无人脸"报错）。
- `EngineManager`：`set_matting_model`、`set_face_model`、`get_matting_engine`、`get_face_detector`（按 `face_model_name` 构建对应检测器）、`preload`（懒加载，不阻塞启动）。`face_model_var` 选项已接通 command，切换即生效。

**photo_processor.py**：`PhotoProcessor.process_id_photo(image, size=None)` → `PhotoResult(standard, hd, matting, face_info)`；
`size=None` 时产出固定构图的「通用照」`UNIVERSAL_PHOTO_SIZE=(1400,1000)`，人像居中、头顶留安全边距，**不绑定任何规格**；传入 `(h,w)` 仍可用（兼容显式尺寸，`bg_only` 模式只换底不裁剪）。
`process_matting_only(image) -> BGRA`；`FaceError`、人脸距离/倾斜校正等内部方法。

**layout_engine.py**：`LayoutItem(image, width, height, label)`；
`LayoutEngine.generate_single_size(img, h, w, crop_line)` / `generate_photos(photos, w, h, crop_line)` /
`generate_mixed_size(items, crop_line)` → `list[ndarray]`（多页）。混合排版按 `(w,h)` 分组、组间强制分页；
尺寸超过画布的组自动跳过并记入 `engine.last_skipped_sizes`（全部过大时抛 ValueError）。

**color_utils.py**：零依赖的共享颜色转换工具。`hex_to_rgb_tuple` 由 `image_utils` 和 `ui_widgets` 共用，避免两处重复实现。

**image_utils.py**：`add_background_to_image`（纯色/渐变）、`apply_beauty`、`apply_watermark`、
`save_image_with_dpi`、`save_image_to_jpeg_kb`、`resize_image_esp`、`numpy_to_photo`。

---

## 3. 架构与数据流

```
app.py (IDPhotoApp)
  ├─ 持有全部业务状态：batch_images / current_image_idx / standard_image / hd_image /
  │                    standard_matting / layout_pages / processed_results / layout_queue ...
  ├─ 持有 UI 容器：root → titlebar → top_nav → [ main_pane | right_pane ]
  ├─ right_pane 内含 1 个固定预览画布 + 顶部视图切换（自动/原图/证件照/排版），由 app 统一管理：
  │     _build_right_previews() 只建一次；_refresh_right_previews(page_id) 按 self.preview_view 刷新内容，
  │     切换页面时只更新内容、不重命名、不重排（用户硬要求）。
  └─ 页面（pages.py）是「视图」：构建左/中功能控件，需要更新预览时统一调 app._refresh_right_previews()。

引擎层（与 UI 解耦）：
  app.engine_manager → MattingEngine / FaceDetector
  app.photo_processor (PhotoProcessor)  ← 业务裁剪/换底
  app.layout_engine   (LayoutEngine)    ← 排版
  image_utils         ← 纯函数图像处理

事件流（典型）：
  选图 → on_select_queue_image → _select_image_at → 刷新右侧「原图预览」
  处理 → on_process → 后台线程 _batch_process_worker → _process_one（调 photo_processor）
        → _on_process_done → 存 standard_image/hd_image → app._refresh_right_previews（更新右侧预览）
  排版 → on_generate_*_layout → layout_engine → set_layout_pages → app._refresh_right_previews（更新右侧预览）
```

**关键约定（影响所有页面修改）：**
- 页面**不再自建**大预览画布。`pages.py` 里 `ProcessPage._build_right_column` / `LayoutPage._build_right_column` 已是**空实现**，
  `UploadPage` 也删掉了原图 Canvas。任何「在右侧看预览」的需求都走 `app._refresh_right_previews()`。
- 切页面时只换 `main_pane` 内容；`right_pane` 三个窗口始终在，只刷新内容。

---

## 4. 关键设计决策（为什么是这样）

1. **顶部导航 + 右侧单预览窗 + 视图切换**（2026-08-16 重构，2026-08-17 三窗合并为一）
   - 需求：导航从左侧栏改到**顶部**；右侧建立**单个固定预览画布**，顶部用分段按钮切换要看哪类图像
     （自动/原图/证件照/排版），默认「自动」跟随当前页面。切换页面时只更新内容、不重命名、不重排。
   - 实现：app 层统一持有 `right_pane`、`preview_canvas` 与视图状态 `preview_view`；页面层调用 `app._refresh_right_previews()`。
   - 风险点：若有人在页面里又 new 一个预览 Canvas，会破坏「固定不变」的约束，请勿这样做。

2. **深色主题 + 橙色强调**：所有颜色集中在 `styles.py`，改色只动这里。

3. **模型路径相对化**（为「回家/换机继续开发」铺路）
   ```python
   _BASE_DIR = os.path.dirname(os.path.abspath(__file__))
   DEFAULT_MODEL_DIR = os.path.join(_BASE_DIR, "model")
   ```
   模型目录直接使用工程内 `model/`（相对路径），无硬编码回退。**换机只需保证 `idphoto_app/model/` 下 5 个 onnx 齐全。**

4. **懒加载 + 后台线程 + `_safe_after` 守卫**：模型不阻塞启动；耗时处理放线程，结果用
   `self._safe_after(ms, func, *args)`（主线程安全调度）回写 UI，避免 Tk 跨线程崩溃。

5. **自定义控件 + 高清图标**：`ui_widgets.py` 统一封装导航/色板/占位；图标用 PIL 4× 超采样 + LANCZOS，解决缩放锯齿。

6. **处理只产「通用照」，尺寸下放排版页（2026-08-19 重构）**
   - 动机：原来处理页要选「一寸/二寸/自定义」具体尺寸，`standard_image` 是精确尺寸照；排版页再用 `_resize_photo_for_layout` **直接拉伸**到规格尺寸，长宽比一不同（一寸 295×413≈1.4 vs 二寸 354×472≈1.29）就**变形**。
   - 新流程：
     1. 处理页**移除尺寸框**（预设下拉 + 宽/高输入、`get_size`/`_on_size_changed` 全删），只留底色/美颜/水水印/翻转/高级头部参数——这些只影响通用照构图，不影响尺寸。
     2. `process_id_photo(size=None)` 默认产固定构图「通用照」`UNIVERSAL_PHOTO_SIZE=(1400,1000)`，人像居中、头顶留安全边距，一次处理可切任意尺寸。
     3. 排版页统一用 `_center_crop_to_size`（等比放大→居中取目标比例区域→精确缩放）**覆盖裁切**，只裁溢出、绝不拉伸；`_resize_photo_for_layout` 已删除。
     4. 「一键排版」「排版」均基于排版页「规格/宽/高/数量」配置，尺寸唯一入口在排版页。
   - 风险点：不要在处理页重新加回尺寸框；不要在排版主流程恢复拉伸式缩放（会变回变形）。

7. **启动期打印机枚举放后台线程（2026-08-19 修复）**
   - 现象：8/18 加了「启动自动刷新打印机列表」后，启动变慢/窗口卡顿。根因：`pages.py` 在 UI 线程调 `win32print.EnumPrinters(PRINTER_ENUM_CONNECTIONS)` 枚举**网络/连接打印机**，有离线/慢速网络打印机时阻塞数秒。
   - 修复：`_refresh_printer_list` 把枚举放进 daemon 线程，结果经 `self.after(0, self._apply_printer_list, printers)` 回主线程写下拉框（加 `winfo_exists()` 守卫）。窗口立即可操作，打印机列表后台就绪后自动填充。
   - 风险点：枚举结果回写 UI 必须走 `after(0, …)`（主线程），切勿在子线程直接 `config()` 控件。

---

## 5. 已踩过的坑（别再踩）

| 坑 | 现象 | 修复 |
|---|---|---|
| 模型路径写死桌面 | 换机必报「模型未找到」 | 改为相对 `model/` + 桌面回退（见 §4.3） |
| BGRA 通道判断语法 | `if image.shape[2]==4 if len(shape)==3 else False` 非法嵌套 | 改为 `if len(image.shape)==3 and image.shape[2]==4:` |
| `ImageTk` 未导入 | `_update_preview_canvas` 用 `PhotoImage` 报错 | 顶部 `from PIL import Image, ImageTk` |
| `scrollbar` 引用错 | `show_result_gallery` 里 `ttk.Scrollbar(self.result_gallery,...)` | 应为 `self.batch_gallery` |
| 窗口最小宽度不足 | 压窄后标签页/按钮重叠裁切 | `_setup_window` 设 `minsize(PREVIEW_COL_WIDTH + THUMB_COL_WIDTH + 380 + 120, 680)` |
| 测试脚本硬编码桌面 | ~~旧版 `test_all.py`~~ 已修复：`MODEL_DIR` 相对化、`TEST_IMAGE` 由 `find_test_image()` 自动定位 `test_images/` | 已修复，仅需在 `test_images/` 放一张含人脸图 |
| 沙箱无 DISPLAY | 沙箱里 `python app.py` 起不来 | 视觉验证必须在真机 Python 3.10 跑 |
| 启动卡顿（打印机枚举阻塞 UI） | 8/18 加「启动自动刷新打印机」后窗口卡数秒 | 枚举放 daemon 线程，`after(0,…)` 回主线程写下拉框（见 §4.7） |
| 排版拉伸变形 | 用 `_resize_photo_for_layout` 直接拉伸到规格尺寸，长宽比不同即变形 | 改用 `_center_crop_to_size` 覆盖式居中裁切（见 §4.6） |
| 一键排版与排版页配置冲突 | 旧流程存在「一键排版」按钮，尺寸来源混乱 | 已删除「一键排版」按钮，统一走 `on_generate_mixed_layout`：读排版页 `get_mixed_items()` 规格/数量 + `ready_photos` 成品列表（见 §2.1 app.py） |
| 移除中间照片后结果错位 | `on_remove_queue_image` pop 后 `processed_results` 索引未前移 | pop 后统一重排：被移除结果丢弃、后续索引前移 1（8/18） |
| 缩略图滚轮互相抢全局绑定 | `bind_all("<MouseWheel>")` 多实例覆盖，后创建抢走 handler | 改控件级绑定 + `return "break"`（8/18） |

---

## 6. 模型文件（务必随工程一起打包）

`idphoto_app/model/` 下需 5 个 ONNX：
- `modnet_photographic_portrait_matting.onnx`（推荐默认，速度快）
- `hivision_modnet.onnx`（兼容性好，修复发丝空洞）
- `rmbg-1.4.onnx`（精度高但慢）
- `birefnet-v1-lite.onnx`（精度最高但最慢）
- `retinaface-resnet50.onnx`（人脸检测，必选；默认）
- MTCNN 为可选人脸检测器：选「MTCNN」时需 `pip install mtcnn-runtime`（首次运行下载权重，非仓库内置）

注意：模型共约 **536 MB**，会使打包 zip 体积很大（≈540 MB+）。打包时对模型用 `ZIP_STORED`（已压缩过，再压无意义且慢）。

---

## 7. 如何运行与测试

### 运行
```bash
# 1) 安装依赖（一次性）
pip install -r requirements.txt
# 2) 启动（确保 model/ 下 5 个 onnx 就位）
python app.py
#    或直接双击 run.bat（会自动定位 Python 解释器）
```
- **必须在有显示器的机器 + tkinter 可用的 Python 上跑**（本机为 Python 3.10）。
- 沙箱（无 DISPLAY）只能做「语法编译 / import 冒烟 / 无 GUI 的引擎测试」，不能看界面。

### 语法校验（可在沙箱做）
```bash
python -m py_compile app.py pages.py ui_widgets.py styles.py inference.py photo_processor.py layout_engine.py image_utils.py
# 期望输出：ALL_COMPILE_OK（无报错即通过）
```

### 综合测试（test_all.py，7 项）
> 注意：脚本里 `MODEL_DIR`、`TEST_IMAGE` 当前硬编码为桌面路径，换机后**先改成本机路径**再跑：
```python
MODEL_DIR = r"<你的工程>/idphoto_app/model"
TEST_IMAGE = r"<一张含人脸的测试图绝对路径>"
```
```bash
python test_all.py
# 期望：通过 7/7（模型加载 / 人脸检测 / 抠图 / 证件照处理 / 单一排版 / 混合排版 / 导出）
```

### import 冒烟（确认无循环依赖 / 缺模块）
```bash
python -c "import sys; sys.path.insert(0,'.'); import app; print('IMPORT_APP_OK')"
```

---

## 8. 已知限制

1. `test_all.py` 的 `TEST_IMAGE` 需自行准备含人脸的测试图（放 `idphoto_app/test_images/` 即可，已支持自动定位）；`run.bat` 已改为 `py`/`python` 自动定位解释器，无硬编码路径。
   换机接手需改 `MODEL_DIR` / `TEST_IMAGE` / `run.bat` 里的提示路径。
2. GUI 无法在 WorkBuddy 沙箱（无 DISPLAY）直接运行，视觉改动必须真机验证。
3. 打包体积大（模型 536MB），传输慢；如只需改代码可只传源码，到目标机再放 `model/`。
4. `SidebarNav` 已弃用（被 `TopNav` 取代），仍保留在 `ui_widgets.py` 中作为可复用组件，勿删以免误伤。

---

## 9. 接手后建议的检查点

- 想改**导航/顶部栏**：`app.py` 的 `_build_top_nav` + `ui_widgets.TopNav`。
- 想改**右侧预览**：只动 `app.py` 的 `_build_right_previews` / `_refresh_right_previews`；页面侧不要新建 Canvas。
- 想改**颜色/字体/间距**：只动 `styles.py`。
- 想改**处理算法/排版规则**：动 `inference.py` / `photo_processor.py` / `layout_engine.py` / `image_utils.py`，UI 层只做调用。
- 想加**新页面**：在 `pages.py` 加 Page 类 → 在 `app.py` 的 `_build_content_area` 注册 → 在 `TopNav` 的 `items` 加入口。
- **尺寸约定（2026-08-19 起）**：处理页**不再有尺寸框**，尺寸唯一入口在排版页（规格/宽/高/数量）。处理只产「通用照」，排版阶段按规格覆盖裁切——不要在处理页重新加尺寸、不要恢复拉伸式缩放（会变回变形）。
- **缩略图约定**：三处缩略图统一用 `THUMB_IMG_WIDTH=140`（左侧队列子列较窄，受此值限制）；最右侧「缩略图」列专用 `THUMB_IMG_WIDTH_RIGHT=280` 填充加宽后的列（约 27% 窗口宽、列宽 `THUMB_COL_WIDTH=324`），占位提示 `wraplength` 同步保持 ≤ 列宽，避免文字请求额外宽度。
- 任何视觉改动完成后：跑 §7 的编译 + 真机 `python app.py` 肉眼确认，再跑 `test_all.py` 防回归。

---

## 10. 一句话总结

本工程是「本地离线证件照工具」，UI 用 tkinter、推理用 ONNX。最新状态：**顶部双页导航（证件照处理 / 排版打印）+ 右侧单预览窗（视图切换）**已就位；
处理只产**通用照**（不分尺寸），尺寸在**排版页**按规格设置并**覆盖裁切不变形**；打印机枚举放后台线程、标题栏显示当前模型；**工程已打包含全部模型**（≈540MB zip），**模型路径已相对化可换机运行**。
接手时遵守 §1 用户铁律，改样式只动 `styles.py`，预览只走 `app._refresh_right_previews()`，尺寸只动排版页，改完务必真机验证。
