# 棋盘字体

`MaiBotXiangqi.otf` 是 Noto Sans CJK SC Regular 的字符子集，保留原字体版权信息，字体族名和 PostScript 名改为 MaiBotXiangqi。

- 上游：[notofonts/noto-cjk](https://github.com/notofonts/noto-cjk)
- 原始文件：`Sans/OTF/SimplifiedChinese/NotoSansCJKsc-Regular.otf`
- 许可证：[SIL Open Font License 1.1](OFL.txt)
- 修改：使用 fontTools 子集化，仅保留插件中文文本及基础 ASCII 所需字符；重命名字体。
- 用途：Pillow 离线绘制棋子和固定棋盘文字，不依赖 Linux 系统字体。

字体许可证独立于插件代码的 GPL 许可证。
