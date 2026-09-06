# 版本与打包

个人项目，直接用 Git 记录修改即可。有值得记下的变化就更新 CHANGELOG；需要发一个版本时，再更新 VERSION 并加对应的版本标签。无需固定分支或 PR 流程。

## 打包源码

提交修改后运行：

```sh
.venv/bin/python -m unittest -q
.venv/bin/python scripts/check_public.py
.venv/bin/python scripts/build_release.py
```

输出在 `dist/`，包含源码压缩包和 SHA256 校验文件。只打包当前提交的文件，不带 `.git` 历史、录屏、转写、模型或本机配置。

旧私有历史中有个人信息，首次公开时用这个包在新目录初始化 Git，不要直接公开旧仓库或推送它的历史。提交邮箱可使用 GitHub 的 noreply 地址。

保留的 CI 只是自动跑测试和文件检查，不涉及审批或发布。它不验证 Apple GPU 推理；模型效果仍需在本机确认。
