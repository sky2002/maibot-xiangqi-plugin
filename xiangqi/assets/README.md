# 棋盘字体

`MaiBotXiangqi.otf` 是 Noto Sans CJK SC Regular 的字符子集，保留原字体版权信息，字体族名和 PostScript 名改为 MaiBotXiangqi。

- 上游：[notofonts/noto-cjk](https://github.com/notofonts/noto-cjk)
- 原始文件：`Sans/OTF/SimplifiedChinese/NotoSansCJKsc-Regular.otf`
- 许可证：[SIL Open Font License 1.1](OFL.txt)
- 修改：使用 fontTools 子集化，仅保留插件中文文本及基础 ASCII 所需字符；重命名字体。
- 用途：Pillow 离线绘制棋子和固定棋盘文字，不依赖 Linux 系统字体。

字体许可证独立于插件代码的 GPL 许可证。


# 内置象棋引擎

- 文件：`fairy-stockfish-14-largeboard-linux-x86_64`，2,527,680 字节；官方原始二进制，未修改，只适用于 Linux x86_64。
- 上游版本：[Fairy-Stockfish fairy_sf_14](https://github.com/fairy-stockfish/Fairy-Stockfish/releases/tag/fairy_sf_14)。
- 原始下载：[fairy-stockfish-largeboard_x86-64](https://github.com/fairy-stockfish/Fairy-Stockfish/releases/download/fairy_sf_14/fairy-stockfish-largeboard_x86-64)。
- SHA256：`41b8b4d539adfd9924929ee4a948d1a37dd1e9beaa535a811cb5e7fee9e4cb99`。
- 作者：Fairy-Stockfish 和 Stockfish 开发者，完整贡献者与版权信息保留在源码归档中。
- 许可证：[GNU GPL v3](Fairy-Stockfish-Copying.txt)，完整原文同时位于源码归档的 `Copying.txt`。
- 对应源码：[fairy-stockfish-14-source.tar.gz](fairy-stockfish-14-source.tar.gz)，包含源码、构建脚本和许可证，未经修改。
- 源码来源：[官方 fairy_sf_14 标签归档](https://codeload.github.com/fairy-stockfish/Fairy-Stockfish/tar.gz/refs/tags/fairy_sf_14)。
- 源码归档 SHA256：`db5e96cf47faf4bfd4a500f58ae86e46fee92c2f5544e78750fc01ad098cbad2`。

源码解压后可在 `src` 目录按上游 Makefile 使用 `make build ARCH=x86-64 COMP=gcc largeboards=yes` 构建；完整选项见归档中的 README 与 Makefile。此说明不声称不同编译环境会生成逐字节相同的二进制。

发布时必须把二进制、对应源码归档和许可证一起作为普通 Git 文件提交，不能仅在本地保留资源或改成 Git LFS 指针。运行时不解压源码归档，也不下载引擎；它只验证二进制并复制到插件数据目录。SHA256 校验和完整源码归档检查纳入测试。
