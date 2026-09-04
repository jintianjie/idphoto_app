# -*- coding: utf-8 -*-
"""core 包：业务逻辑原语层。

包含：
  - secure_json  : 加密 JSON 原语（PBKDF2 + SHA256-CTR + HMAC-SHA256）
  - settings_store: 用户可手动编辑的设置 JSON
  - mirror_manager: 镜像源/加速下载 URL 管理 + 加密 JSON 解锁
"""
