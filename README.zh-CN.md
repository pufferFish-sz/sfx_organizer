# sfx_organizer

[English](README.md) | **简体中文**

Python GUI 工具,按 **UCS v8.2.1**(Universal Category System,753 条目 / 82 大类)自动整理音效素材库。
支持中文 / 英文混合文件名,输出统一英文 UCS 分类目录。

详细需求见 [`sfx_organizer_spec.md`](../sfx_organizer_spec.md)。

---

## 安装

需要 Python ≥ 3.10。

```bash
cd sfx_organizer
pip install -r requirements.txt
```

可选依赖会在缺失时降级而不报错:

| 包 | 用途 | 缺失时的行为 |
|----|------|--------------|
| `jieba` | 中文分词 | 降级为「词典最长匹配」算法 |
| `mutagen` | 读音频 metadata(BWF/ID3/Vorbis) | 跳过 P3 兜底,纯文件名分类 |
| `py7zr` | 解压 `.7z` | `.7z` 文件被记为「不支持」 |
| `rarfile` | 解压 `.rar` | `.rar` 文件被记为「不支持」 |
| `pyzipper` | 加密 zip 写入(仅测试用) | 加密 zip 测试被跳过 |

## 启动

```bash
python main.py
```

## 三种工作模式

GUI 上以单选钮提供:

- **直接整理(不解压)** — 音频和压缩包都按文件名分类、移动/复制到目标
- **先解压后整理** — 先把所有压缩包解到临时目录,再分类每个音频,完成后清理临时目录
- **增量整理到现有库** — 源 + 已存在的目标库根;复用既有目录结构,自动 ignore 目标防递归扫描

## 分类策略(spec 2.5)

四级优先级,**命中即停止**:

1. **CatID 前缀直接命中** —— 文件名以 UCS 标准 CatID(如 `AMBLake_xxx`, `GUNAntq_001`)开头,100% 进对应子类
2. **加权关键词匹配** —— 文件名经九步预处理(剥序号 / 拆中英 / 中文分词 / 中英映射 / 去停用词)后得到 token 集,对每个 UCS 条目打分:
   - 大类名 exact 命中:`4.0 × len`
   - 子类名 exact 命中:`3.0 × len`
   - 子类名子串命中(len ≥ 5):`2.0 × len`
   - CatShort 命中:`2.0 × len`
   - 同义词命中:`1.0 × len`

   每个 (token, entry) 取 *最高* 权命中。然后:
   - 若**最佳条目所属大类** ✓ 等于**大类聚合得分最高的大类**,且条目分 ≥ 4.0,且同大类内对第二名领先 ≥ 1.5 → **归子类**
   - 否则若大类聚合分 ≥ 4.0 且对第二大类领先 ≥ 10.0 → **归大类根**
   - 否则 → `_misc/`

3. **音频 metadata 兜底** —— 当上述均失败且文件是音频时,读取 BWF/ID3/Vorbis 的 Title/Description/Comment/Subject/Keywords,把文本当作新文件名再走第 2 步
4. **全部失败** → `_misc/`

此外内置一个 garbage-filename 预过滤:`untitled_001.wav` / `track01.wav` 这类默认命名直送 `_misc/`,避免被某个偶然的 subname 抢走。

## 数据文件

均在 `data/` 下,均可手工编辑:

| 文件 | 内容 |
|------|------|
| `ucs_v8_2_1.csv` | UCS 表(753 条) — 项目自带,可换成自己版本 |
| `zh_en_dict.json` | 中英文同义词词典(282 条,覆盖 82 大类),`_` 开头的 key 是注释 |
| `stopwords.json` | 行业噪声词(SFX/Sample/Pack/音效/采样/...) |
| `brands.txt` | 厂商品牌名(一行一个,`#` 开头是注释)— 用户应按自己常用厂商扩展 |
| `extra_synonyms.json` | UCS 同义词补充(不改原表),键格式 `"CATEGORY/SUBCATEGORY"` |

## 测试

```bash
python -m pytest tests/
```

112 个用例(其中 4 个需可选依赖)。涵盖:
- `ucs_loader` / `tokenizer` / `preprocessor` / `classifier`(覆盖 spec 2.6 真实样本)
- `scanner` / `organizer` / `disk_checker` / `extractor`

测试 fixture `tests/fixtures/real_filenames.txt` 是 spec 提供的真实样本,**任何分类逻辑改动都必须保证这 13 条仍通过**。

## 打包成 .exe(PyInstaller)

```bash
pip install pyinstaller
python build.py             # 默认 onedir,产物在 dist/sfx_organizer/
python build.py --onefile   # 单文件 .exe(分发一个文件,启动慢、每次解压到 TEMP)
python build.py --skip-tests --clean   # 透传选项
```

打包细节都在 [`sfx_organizer.spec`](sfx_organizer.spec) 里,可直接 `pyinstaller sfx_organizer.spec`。spec 会:

- 把 `data/` 下五个资源文件(UCS / 词典 / 停用词 / 品牌 / extra_synonyms)打进 bundle
- 自动检测 jieba(已装则带上它的 `dict.txt`,未装则跳过)
- 排除 matplotlib / numpy / pandas / Qt 这些用不到的大块头
- `console=False` 双击运行不弹黑窗

打出来的 .exe 里 `data/*.json/.csv/.txt` 是只读的(在临时解压目录)。**用户要扩展词典 / 加品牌名 / 调 extra_synonyms,可通过 GUI 的「选择...」按钮指定一份外部副本**(复制 bundle 里的同名文件到任意可写目录即可)。

资源路径解析见 [`utils/paths.py`](utils/paths.py) —— `sys._MEIPASS` 优先,dev 模式回落到 `__file__.parent.parent`,代码层面无需感知是否 frozen。

## 项目结构

```
sfx_organizer/
├── main.py
├── data/
│   ├── ucs_v8_2_1.csv          # UCS 表(项目附带)
│   ├── zh_en_dict.json         # 中英文映射词典
│   ├── stopwords.json          # 行业噪声词
│   ├── brands.txt              # 品牌名(可编辑)
│   └── extra_synonyms.json     # UCS 同义词补充
├── core/
│   ├── ucs_loader.py           # CSV 加载 + 索引
│   ├── preprocessor.py         # ★ 文件名九步清洗
│   ├── tokenizer.py            # 切分 + 中文分词 + 中英映射
│   ├── classifier.py           # ★ 四级优先级 + 加权打分
│   ├── extractor.py            # 压缩包处理(zip/rar/7z + 密码)
│   ├── organizer.py            # 文件移动 + 目录创建 + 冲突
│   ├── scanner.py              # 递归扫描
│   └── disk_checker.py         # 磁盘空间预估
├── gui/
│   ├── main_window.py          # 主窗口(spec 第六节布局)
│   ├── worker.py               # 后台 worker 线程 + 事件
│   ├── password_dialog.py      # 加密包密码弹窗 + 会话缓存
│   └── preview_dialog.py       # 分类预览对照表
├── utils/
│   ├── logger.py               # 文件 + GUI 双通道日志
│   ├── metadata_reader.py      # mutagen 包装
│   └── paths.py                # 资源路径(dev / PyInstaller 兼容)
├── sfx_organizer.spec          # PyInstaller 规格(见上「打包成 .exe」)
├── build.py                    # 一键打包脚本
└── tests/
    ├── test_*.py               # 112 用例
    └── fixtures/
        └── real_filenames.txt
```

## 常见问题

**Q: 分类不准 / 一直进 `_misc`?**
- 检查 `data/brands.txt` 是否过滤了你不想过滤的词(厂商名误杀)
- 检查 `data/zh_en_dict.json` 是否覆盖了你常用的中文词;添加映射可立即生效
- 对实在难分的子类,可在 `data/extra_synonyms.json` 添加同义词补充

**Q: 整理速度?**
- 单线程 1000 文件约几秒(评分是纯内存运算,瓶颈是 IO 复制)
- 多线程化在 spec V2 中作为增强暂未实现

**Q: 加密 zip 怎么处理?**
- GUI 弹密码框,3 次重试,勾「记住该密码」可在本次会话内被后续加密包自动复用
- 用户点跳过 → 该文件记入日志后继续

**Q: Windows 中文路径报错?**
- 项目全程用 `pathlib.Path` + UTF-8,经过测试。若仍报错请检查源文件夹是否在被其他进程占用

## 限制(将在 V2 处理)

- AI 语义分类(本地模型 / API)
- 音频指纹去重
- 自动重命名为 UCS 标准命名
- SQLite 索引便于检索
- 多线程化处理(当前 worker 是单线程串行)
