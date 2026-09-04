# -*- coding: utf-8 -*-
"""UI 包：PyQt-Fluent-Widgets 视图层。

本包只负责「界面长什么样、控件怎么摆」，不含任何业务逻辑；
业务逻辑（抠图 / 裁剪 / 排版 / 导出）全部委托给 app 控制器与被复用的
后端模块（inference / photo_processor / layout_engine / image_utils）。
"""
