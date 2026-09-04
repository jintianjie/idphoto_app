# -*- coding: utf-8 -*-
"""
排版引擎模块
支持单一尺寸排版和混合尺寸排版（尺寸混拼），输出多页画布列表。
业务逻辑层：不包含任何 UI 代码。
"""
import numpy as np
import cv2
from collections import defaultdict

from styles import (
    LAYOUT_CANVAS_WIDTH,
    LAYOUT_CANVAS_HEIGHT,
    LAYOUT_PHOTO_INTERVAL_H,
    LAYOUT_PHOTO_INTERVAL_V,
    LAYOUT_SIDE_INTERVAL_W,
    LAYOUT_SIDE_INTERVAL_H,
)


# 不同尺寸辅助切割线配色（BGR）。在白底画布上需足够醒目、互相区分。
SIZE_LINE_COLORS = [
    (0, 0, 255),     # 红
    (255, 0, 0),     # 蓝
    (0, 200, 0),     # 绿
    (0, 165, 255),   # 橙
    (255, 0, 255),   # 品红
    (255, 255, 0),   # 青
    (128, 0, 128),   # 紫
    (42, 42, 165),   # 棕
]


class LayoutItem:
    """排版中的一个照片条目。"""

    def __init__(self, image: np.ndarray, width: int, height: int, label: str = ""):
        self.image = image        # 已裁好的标准照（BGR 或 BGRA）
        self.width = int(width)   # 照片宽度（像素）
        self.height = int(height) # 照片高度（像素）
        self.label = label        # 尺寸标签（如 "一寸"）
        self.placed_x = 0         # 排版后的 x 坐标
        self.placed_y = 0         # 排版后的 y 坐标
        self.placed_page = 0      # 所在页码
        self.rotated = False      # 是否旋转 90° 放置


class LayoutEngine:
    """
    排版引擎，将多张证件照排列到打印画布上。
    支持单一尺寸和混合尺寸排版，可输出多页。
    """

    def __init__(
        self,
        canvas_width: int = LAYOUT_CANVAS_WIDTH,
        canvas_height: int = LAYOUT_CANVAS_HEIGHT,
        photo_interval_h: int = LAYOUT_PHOTO_INTERVAL_H,
        photo_interval_v: int = LAYOUT_PHOTO_INTERVAL_V,
        side_interval_w: int = LAYOUT_SIDE_INTERVAL_W,
        side_interval_h: int = LAYOUT_SIDE_INTERVAL_H,
    ):
        self.canvas_width = int(canvas_width)
        self.canvas_height = int(canvas_height)
        self.photo_interval_h = int(photo_interval_h)
        self.photo_interval_v = int(photo_interval_v)
        self.side_interval_w = int(side_interval_w)
        self.side_interval_h = int(side_interval_h)
        # 混合排版时因尺寸过大被跳过的规格（(w, h) 列表），供调用方提示用户
        self.last_skipped_sizes = []
        # 最近一次混合排版中，尺寸 (w,h) -> 裁剪线颜色（BGR），供 UI 生成图例
        self.last_size_colors = {}

    def generate_single_size(
        self,
        photo: np.ndarray,
        photo_width: int,
        photo_height: int,
        crop_line: bool = False,
        crop_line_full_page: bool = True,
    ) -> list:
        """
        生成单一尺寸的排版图（单张照片重复多份，常用于「一版多张相同照」）。
        自动计算最优排列方式（横排/竖排），照片过多时自动分页。
        """
        return self._layout_photos(
            [photo], photo_width, photo_height, crop_line, repeat=True,
            crop_line_full_page=crop_line_full_page,
        )

    def generate_photos(
        self,
        photos: list,
        photo_width: int,
        photo_height: int,
        crop_line: bool = False,
        crop_line_full_page: bool = True,
    ) -> list:
        """
        生成单一尺寸的排版图（多张不同照片各排一份，自动分页）。

        用于「一键排版 / 发送到排版」等批量场景：队列里每张照片各排一份，
        照片数量超过一页容量时自动翻页，避免只排第一张的「只有一版」问题。
        """
        return self._layout_photos(
            list(photos), photo_width, photo_height, crop_line, repeat=False,
            crop_line_full_page=crop_line_full_page,
        )

    def _layout_photos(
        self,
        photos: list,
        photo_width: int,
        photo_height: int,
        crop_line: bool,
        repeat: bool,
        crop_line_full_page: bool = True,
    ) -> list:
        """核心排版：把多张照片按网格铺到画布（可多页）。

        repeat=True : 仅有一张照片时将其重复填满每一页（一版多张相同照）。
        repeat=False: 每张照片各排一份，用完即止，超出单页容量自动翻页。
        """
        if not photos:
            raise ValueError("输入照片为空")
        # 校验并统一为 BGR；显式转 Python 标量，杜绝 numpy 布尔歧义
        norm_photos = []
        for p in photos:
            if p is None or not isinstance(p, np.ndarray) or int(p.size) == 0:
                raise ValueError("输入照片为空")
            norm_photos.append(self._flatten_to_bgr(p))
        photo_width = int(photo_width)
        photo_height = int(photo_height)
        if photo_width <= 0 or photo_height <= 0:
            raise ValueError(f"无效的照片尺寸: {photo_width}x{photo_height}")

        avail_w = self.canvas_width - 2 * self.side_interval_w
        avail_h = self.canvas_height - 2 * self.side_interval_h

        # 尝试两种排列方式
        best = self._best_orientation(photo_width, photo_height, avail_w, avail_h)
        place_w, place_h, cols, rows, count_per_page = best

        if cols == 0 or rows == 0 or count_per_page == 0:
            raise ValueError("照片尺寸过大，无法在画布上排版")

        # 构建待排序列
        if repeat:
            # 单张重复：每页排满 count_per_page 份相同照
            to_place = [norm_photos[0]] * count_per_page
            total = count_per_page
        else:
            to_place = norm_photos
            total = len(norm_photos)

        pages = []
        for start in range(0, total, count_per_page):
            chunk = to_place[start:start + count_per_page]
            canvas = self._create_blank_canvas()
            positions = []
            block_w = cols * place_w + (cols - 1) * self.photo_interval_h
            block_h = rows * place_h + (rows - 1) * self.photo_interval_v
            start_x = (self.canvas_width - block_w) // 2
            start_y = (self.canvas_height - block_h) // 2

            for k in range(len(chunk)):
                r = k // cols
                c = k % cols
                x = start_x + c * (place_w + self.photo_interval_h)
                y = start_y + r * (place_h + self.photo_interval_v)
                positions.append((x, y, place_w, place_h))

            # 先画裁剪线（置于照片下方），再贴照片，避免线条压在照片上
            if crop_line:
                if crop_line_full_page:
                    self._draw_crop_lines(canvas, positions)
                else:
                    clip = self._positions_bbox(positions)
                    self._draw_crop_lines(canvas, positions, clip_bbox=clip)
            for k, slot_photo in enumerate(chunk):
                placed = self._fit_photo(slot_photo, place_w, place_h)
                x, y, w, h = positions[k]
                self._paste_image(canvas, placed, x, y)
            pages.append(canvas)

        return pages

    def _fit_photo(self, photo: np.ndarray, place_w: int, place_h: int) -> np.ndarray:
        """把照片适配到 (place_w, place_h) 槽位：必要时旋转 90° 或缩放。"""
        if place_w != int(photo.shape[1]) or place_h != int(photo.shape[0]):
            # 方向不同，旋转 90°
            placed = cv2.transpose(photo)
            placed = cv2.flip(placed, 0)
        else:
            placed = photo
        if int(placed.shape[1]) != place_w or int(placed.shape[0]) != place_h:
            placed = cv2.resize(placed, (place_w, place_h))
        return placed

    def generate_mixed_size(
        self,
        items: list,
        crop_line: bool = False,
        crop_line_full_page: bool = True,
    ) -> list:
        """
        生成混合尺寸排版图（同纸混排，货架式铺排，节约纸张）。

        设计原则：
        1. 尺寸顺序严格遵循配置（如「先 1寸、后 2寸」）—— 全局先排完所有
           一寸，再排所有二寸（与用户「先排一寸再排二寸」的要求一致）。
        2. 同一张纸上：先沿顶部铺满一寸行，一寸行下方的剩余空间紧接着铺
           二寸行（尺寸换行），一张纸塞满才换下一张 —— 不强制按尺寸硬分页，
           避免「一寸占几整页 + 二寸占几整页」造成的留白浪费。
        3. 单张都放不下的过大尺寸：整组跳过并计入 last_skipped_sizes，
           不影响其余尺寸正常排版。

        例如「5人各 1寸8张 + 2寸5张」：第 1 页顶部若干行一寸，底部若还有
        空间则接若干行二寸；一寸没排完的部分顺延到第 2 页继续，二寸在最后一
        寸行之后接排，整体页数通常少于「按尺寸独占整页」的方案。
        """
        if not items:
            raise ValueError("排版条目列表为空")

        # 校验并统一尺寸，按尺寸分组（首次出现顺序 = 配置顺序）
        cleaned = []
        for item in items:
            if not isinstance(item, LayoutItem):
                raise ValueError("排版条目必须是 LayoutItem 类型")
            item.width = int(item.width)
            item.height = int(item.height)
            cleaned.append(item)

        groups = defaultdict(list)
        size_order = []
        for item in cleaned:
            key = (item.width, item.height)
            if key not in groups:
                size_order.append(key)
            groups[key].append(item)

        avail_w = self.canvas_width - 2 * self.side_interval_w
        avail_h = self.canvas_height - 2 * self.side_interval_h
        start_x = self.side_interval_w
        start_y = self.side_interval_h
        bottom = start_y + avail_h

        # 单张都放不下的尺寸（过宽或过高）→ 整组跳过
        def size_fits(w, h):
            return w <= avail_w and h <= avail_h

        skipped = []
        pages = []

        # 不同尺寸 -> 不同颜色辅助切割线（按尺寸首次出现顺序分配，循环取色板）
        size_colors = {
            key: SIZE_LINE_COLORS[i % len(SIZE_LINE_COLORS)]
            for i, key in enumerate(size_order)
        }
        self.last_size_colors = size_colors

        page_canvas = self._create_blank_canvas()
        page_placements = []   # (x, y, w, h, photo_bgr)：先收集，最后统一画线再贴图
        # 当前行的状态
        y = start_y           # 下一行的顶部 y
        row_size = None       # 当前行尺寸 (w, h)
        row_w = 0             # 当前行总宽（含间距）
        row_h = 0             # 当前行高度（= 照片高）
        row_start_x = start_x # 当前行起始 x（已水平居中）
        x = start_x           # 当前待放槽位 x

        def start_row(w, h):
            """在 y 处开启（或重开）一行尺寸 (w,h) 的照片，计算水平居中布局。"""
            nonlocal row_w, row_h, row_size, row_start_x, x
            cols = self._count_fit(avail_w, w, self.photo_interval_h)
            if cols <= 0:
                # 已在 size_fits 过滤，这里兜底
                raise ValueError(f"照片尺寸过大，无法在画布上排版（{w}x{h}）")
            rw = cols * w + (cols - 1) * self.photo_interval_h
            row_start_x = start_x + (avail_w - rw) // 2
            row_w, row_h = rw, h
            row_size = (w, h)
            x = row_start_x

        def flush_page():
            """收尾当前页：先按尺寸分组画裁剪线（置于照片下方），再贴照片。"""
            nonlocal page_canvas, page_placements
            if crop_line and page_placements:
                if crop_line_full_page:
                    self._draw_crop_lines_grouped(page_canvas, page_placements, size_colors)
                else:
                    clip = self._placements_bbox(page_placements)
                    self._draw_crop_lines_grouped(page_canvas, page_placements, size_colors, clip_bbox=clip)
            for (px, py, pw, ph, pphoto) in page_placements:
                self._paste_image(page_canvas, pphoto, px, py)
            pages.append(page_canvas)

        def new_page():
            """收尾当前页并开新空白页，重置行/光标状态。"""
            nonlocal page_canvas, page_placements, y, row_size, row_w, row_h, row_start_x, x
            if page_placements:
                flush_page()
            page_canvas = self._create_blank_canvas()
            page_placements = []
            y = start_y
            row_size = None
            row_w = row_h = 0
            row_start_x = start_x
            x = start_x

        for size_key in size_order:
            item_w, item_h = size_key
            if not size_fits(item_w, item_h):
                skipped.append(size_key)
                continue
            for it in groups[size_key]:
                w, h = item_w, item_h
                if row_size != (w, h):
                    # 尺寸变化 → 另起一行（不同尺寸不混在同一行内）
                    if row_size is not None:
                        y += row_h + self.photo_interval_v
                    if y + h > bottom:
                        new_page()
                    start_row(w, h)
                else:
                    # 同尺寸：当前行横向放不下 → 换行（同页或换页）
                    if x + w > row_start_x + row_w:
                        y += row_h + self.photo_interval_v
                        if y + h > bottom:
                            new_page()
                            start_row(w, h)
                        else:
                            x = row_start_x

                photo = self._flatten_to_bgr(it.image)
                page_placements.append((x, y, w, h, photo))
                x += w + self.photo_interval_h

        # 收尾最后一页
        if page_placements:
            flush_page()

        self.last_skipped_sizes = skipped
        if not pages:
            detail = "、".join(f"{w}x{h}" for w, h in skipped) if skipped else "未知"
            raise ValueError(f"所选照片尺寸过大，无法在画布上排版（{detail}）")
        return pages

    def _best_orientation(self, photo_w: int, photo_h: int, avail_w: int, avail_h: int) -> tuple:
        """计算最佳放置方向，返回 (place_w, place_h, cols, rows, count_per_page)。"""
        candidates = []
        for rotate in (False, True):
            pw, ph = (photo_h, photo_w) if rotate else (photo_w, photo_h)
            cols = self._count_fit(avail_w, pw, self.photo_interval_h)
            rows = self._count_fit(avail_h, ph, self.photo_interval_v)
            count = cols * rows
            candidates.append((pw, ph, cols, rows, count, rotate))

        # 选择数量最多的方向
        best = max(candidates, key=lambda c: (c[4], c[2] * c[3]))
        return best[:5]

    def _create_blank_canvas(self) -> np.ndarray:
        """创建白色画布。"""
        return np.full(
            (self.canvas_height, self.canvas_width, 3), 255, dtype=np.uint8
        )

    def _paste_image(self, canvas: np.ndarray, photo: np.ndarray, x: int, y: int):
        """将照片粘贴到画布指定位置，带边界与形状检查。

        边界以实际传入的 canvas 数组尺寸为准（而非引擎配置尺寸），
        避免因画布与引擎尺寸不一致导致切片越界或形状不匹配。
        """
        x = int(x)
        y = int(y)
        ch, cw = canvas.shape[:2]
        ph, pw = photo.shape[:2]
        y_end = min(y + ph, ch)
        x_end = min(x + pw, cw)
        if y >= ch or x >= cw or y_end <= y or x_end <= x:
            return
        # 照片尺寸小于目标槽位（旋转/缩放取整误差）或通道数不匹配时跳过，
        # 防止 numpy 形状不匹配或通道数不一致导致崩溃。
        if ph < (y_end - y) or pw < (x_end - x):
            return
        if len(photo.shape) == 3 and len(canvas.shape) == 3 and photo.shape[2] != canvas.shape[2]:
            return
        canvas[y:y_end, x:x_end] = photo[:y_end - y, :x_end - x]

    @staticmethod
    def _flatten_to_bgr(image) -> np.ndarray:
        """将 BGRA 图像合成为白底 BGR；3 通道或灰度直接转 BGR。"""
        if not isinstance(image, np.ndarray):
            raise ValueError("image 必须是 numpy.ndarray")
        if image.size == 0:
            raise ValueError("image 为空")
        if len(image.shape) == 2:
            return cv2.cvtColor(image, cv2.COLOR_GRAY2BGR)
        if len(image.shape) == 3 and int(image.shape[2]) == 4:
            b, g, r, a = cv2.split(image)
            alpha = a.astype(np.float32) / 255.0
            b_flat = (b.astype(np.float32) * alpha + 255.0 * (1.0 - alpha)).astype(np.uint8)
            g_flat = (g.astype(np.float32) * alpha + 255.0 * (1.0 - alpha)).astype(np.uint8)
            r_flat = (r.astype(np.float32) * alpha + 255.0 * (1.0 - alpha)).astype(np.uint8)
            return cv2.merge((b_flat, g_flat, r_flat))
        if len(image.shape) == 3 and int(image.shape[2]) == 3:
            return image
        raise ValueError(f"不支持的图像通道数: {image.shape}")

    @staticmethod
    def _positions_bbox(positions: list) -> tuple:
        """由 (x, y, w, h) 列表计算内容包围盒 (xmin, ymin, xmax, ymax)。"""
        if not positions:
            return (0, 0, 0, 0)
        xmin = min(int(x) for x, y, w, h in positions)
        ymin = min(int(y) for x, y, w, h in positions)
        xmax = max(int(x + w) for x, y, w, h in positions)
        ymax = max(int(y + h) for x, y, w, h in positions)
        return (xmin, ymin, xmax, ymax)

    @staticmethod
    def _placements_bbox(placements: list) -> tuple:
        """由 (x, y, w, h, photo) 列表计算内容包围盒。"""
        if not placements:
            return (0, 0, 0, 0)
        xmin = min(int(px) for px, py, pw, ph, _ in placements)
        ymin = min(int(py) for px, py, pw, ph, _ in placements)
        xmax = max(int(px + pw) for px, py, pw, ph, _ in placements)
        ymax = max(int(py + ph) for px, py, pw, ph, _ in placements)
        return (xmin, ymin, xmax, ymax)

    @staticmethod
    def _count_fit(available: int, item_size: int, gap: int) -> int:
        """计算在可用空间内能放多少个物品（含间距）。"""
        available = int(available)
        item_size = int(item_size)
        gap = int(gap)
        if item_size <= 0:
            return 0
        if item_size > available:
            return 0
        return int((available + gap) / (item_size + gap))

    def _draw_crop_lines(
        self,
        canvas: np.ndarray,
        positions: list,
        color=(200, 200, 200),
        clip_bbox: tuple = None,
    ):
        """在画布上绘制裁剪线（线绘制在照片下方，仅露在白边上，不会压住照片）。

        clip_bbox=(xmin, ymin, xmax, ymax)：当 crop_line_full_page=False 时，
        只绘制到内容包围盒内，避免空白区出现多余切割线，节省纸张。
        """
        line_thickness = 1

        vertical_lines = set()
        horizontal_lines = set()

        for x, y, w, h in positions:
            vertical_lines.add(int(x))
            vertical_lines.add(int(x + w))
            horizontal_lines.add(int(y))
            horizontal_lines.add(int(y + h))

        if clip_bbox:
            cxmin, cymin, cxmax, cymax = clip_bbox
        else:
            cxmin, cymin, cxmax, cymax = 0, 0, self.canvas_width, self.canvas_height

        for x in vertical_lines:
            if cxmin <= x <= cxmax:
                cv2.line(canvas, (x, max(cymin, 0)), (x, min(cymax, self.canvas_height)), color, line_thickness)
        for y in horizontal_lines:
            if cymin <= y <= cymax:
                cv2.line(canvas, (max(cxmin, 0), y), (min(cxmax, self.canvas_width), y), color, line_thickness)

    def _draw_crop_lines_grouped(
        self,
        canvas: np.ndarray,
        placements: list,
        size_colors: dict,
        clip_bbox: tuple = None,
    ):
        """按尺寸分组绘制裁剪线，每组使用其尺寸对应的颜色（线在照片下方）。

        placements 元素为 (x, y, w, h, photo_bgr)。不同 (w, h) 取 size_colors
        中对应颜色，使混合排版中每种尺寸拥有独立颜色的辅助切割线。
        """
        groups = defaultdict(list)
        for (x, y, w, h, _photo) in placements:
            groups[(w, h)].append((x, y, w, h))
        for (w, h), pos in groups.items():
            color = size_colors.get((w, h), (200, 200, 200))
            self._draw_crop_lines(canvas, pos, color=color, clip_bbox=clip_bbox)

    def generate_photos_in_order(self, items, crop_line=False, crop_line_full_page=True):
        """按 items 原始顺序，按 (w,h) 连续段分组分页排版。

        适用于「每人一版」场景：同一人同规格的照片在 items 中连续出现，
        被分到同一组独立排版；不同组之间强制分页，保证每个人的每个规格
        单独成页（或连续几页，若张数超过单页容量）。

        返回：(pages: list[np.ndarray], skipped: list[(w,h)])
        """
        pages = []
        skipped = []
        i = 0
        while i < len(items):
            w = int(items[i].width)
            h = int(items[i].height)
            group = []
            while i < len(items) and int(items[i].width) == w and int(items[i].height) == h:
                group.append(items[i])
                i += 1
            photos = [self._flatten_to_bgr(it.image) for it in group]
            try:
                group_pages = self._layout_photos(
                    photos, w, h, crop_line, repeat=False,
                    crop_line_full_page=crop_line_full_page,
                )
                pages.extend(group_pages)
            except ValueError:
                skipped.append((w, h))
        return pages, skipped
