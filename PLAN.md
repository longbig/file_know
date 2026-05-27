# 架构优化方案：规则引擎 + LLM 语义判定

## 一、现状问题分析

### 1.1 当前架构

```
PDF → 全文提取 → 整篇全文 + 12,000 token 超长 Prompt 扔给 LLM → LLM 一次性完成所有工作 → 后处理补全字段
```

**LLM 当前承担的职责（过重）：**
- 逐句扫描全文，识别候选评论句
- 匹配 900+ 个标志词
- 判断作者-年份三要素是否齐全
- 执行 9 条一票否决规则（自引检测、括号规则、裸词禁用等）
- 匹配参考文献并填充被评文献详细信息
- 提取施评文献元数据
- 处理特殊情形（independently 拆分、多文献拆分）
- 生成严格 JSON 结构

### 1.2 核心痛点

| 问题 | 根因 | 影响 |
|------|------|------|
| 标志词漏匹配 | 900+ 标志词全靠 LLM 记忆，Prompt 过长注意力稀释 | 漏提评论句 |
| 规则不一致 | 9 条否决规则纯自然语言描述，LLM 理解有偏差 | 误判/漏判 |
| 三要素校验不严格 | LLM 有时从参考文献编号反查补全作者/年份 | 违反准入规则 |
| 自引漏检 | 自引词表嵌在 Prompt 中，LLM 经常忽略 | 把自引当评论句 |
| 非期刊文献混入 | LLM 不擅长判断文献类型 | 后处理才过滤，浪费 token |
| Prompt token 浪费 | 标志词列表占 ~8,000 tokens | 每次调用都重复发送 |
| JSON 输出不稳定 | 结构复杂+字段多，LLM 偶尔格式错误 | 解析失败需重试 |

### 1.3 当前后处理仅覆盖 1/9 条规则

| 一票否决规则 | 本地校验? |
|---|---|
| ① 作者+年份仅在括号内 | ❌ 无 |
| ② 自引检测 | ❌ 无 |
| ③ 非期刊论文 | ✅ ref_parser 类型校验 |
| ④ 标志词描述操作步骤 | ❌ 无（需语义判断） |
| ⑤ 三要素不属同一事件 | ❌ 无（需语义判断） |
| ⑥ 被评对象是机构非作者 | ❌ 无 |
| ⑦ 参考文献年份不匹配 | ✅ find_reference_by_author_year |
| ⑧ 仅标注参考文献编号 | ❌ 无 |
| ⑨ 裸词禁用 | ❌ 无 |

---

## 二、新架构设计

### 2.1 核心理念

**"规则引擎负责确定性逻辑，LLM 只负责语义判断。"**

```
PDF
 │
 ├── [阶段 1] 文本预处理层（纯 Python）
 │     ├── PDF 解析 → 全文 + 按句分割
 │     ├── 参考文献解析 → 结构化 Reference 列表
 │     ├── 施评文献元数据提取
 │     └── 作者-年份索引构建
 │
 ├── [阶段 2] 规则引擎层（纯 Python，零 LLM）
 │     ├── 标志词 regex 扫描 → 候选句标记
 │     ├── 三要素校验（作者+年份+标志词）
 │     ├── 一票否决规则过滤
 │     │     ├── 自引检测
 │     │     ├── 非期刊过滤
 │     │     ├── 括号规则
 │     │     ├── 裸词禁用
 │     │     ├── 仅编号无作者/年份
 │     │     └── 年份不匹配
 │     ├── 多文献拆分 / independently 拆分
 │     └── 输出：候选评论句列表（含预提取的结构化信息）
 │
 ├── [阶段 3] LLM 语义判定层（轻量 Prompt）
 │     ├── 输入：候选句 + 上下文（前后各 1-2 句）
 │     ├── 判断：是否为真正的学术评论句
 │     ├── 判断：标志词是否描述学术贡献事件
 │     └── 输出：accept / reject + 理由
 │
 ├── [阶段 4] 结果组装层（纯 Python）
 │     ├── 合并 LLM 判定结果
 │     ├── 从 Reference 补全被评文献字段
 │     ├── 机构查询（CrossRef 三级回退）
 │     └── JSON schema 校验
 │
 └── [阶段 5] 输出生成层（不变）
       ├── PDF 高亮
       ├── Excel 汇总表
       └── Word 登记表 × N
```

### 2.2 各阶段职责对比

| 职责 | 旧架构（谁做） | 新架构（谁做） |
|------|---|---|
| 标志词匹配 | LLM（900+词嵌入 Prompt） | **规则引擎**（正则预编译） |
| 三要素校验 | LLM | **规则引擎**（作者/年份正则） |
| 自引检测 | LLM（经常漏） | **规则引擎**（词表匹配） |
| 非期刊过滤 | LLM + 后处理 | **规则引擎**（ref_parser 类型） |
| 括号规则 | LLM | **规则引擎**（正则分析括号位置） |
| 裸词禁用 | LLM | **规则引擎**（词表检查） |
| 年份匹配 | LLM + 后处理 | **规则引擎**（Reference 精确匹配） |
| 仅编号无作者/年份 | LLM | **规则引擎**（正则检测） |
| independently 拆分 | LLM | **规则引擎**（正则+模式匹配） |
| 多文献拆分 | LLM | **规则引擎**（引用标记解析） |
| **语义判断：是否学术评论** | LLM | **LLM**（保留） |
| **语义判断：学术贡献事件** | LLM | **LLM**（保留） |
| 被评文献字段填充 | LLM | **规则引擎**（从 Reference 映射） |
| 施评文献元数据 | LLM + 正则 | **正则**（已有，增强） |
| JSON 结构化输出 | LLM | **规则引擎**（Python 组装） |

---

## 三、详细模块设计

### 3.1 阶段 1：文本预处理层

#### 3.1.1 句子分割器 `sentence_splitter.py`（新增）

```python
def split_sentences(full_text: str) -> list[Sentence]

@dataclass
class Sentence:
    text: str           # 句子原文
    index: int          # 句子序号
    start_pos: int      # 在全文中的字符起始位置
    end_pos: int        # 字符结束位置
    prev_sentence: str  # 前一句（上下文）
    next_sentence: str  # 后一句（上下文）
```

难点：
- 学术论文中句号不一定是句子结尾（如 `et al.`、`Fig. 1`、`e.g.`、`Dr.`）
- 中文句号 `。` vs 英文句号 `.`
- 需要维护引用标记 `[1]`、`[2,3]` 的位置信息

#### 3.1.2 作者-年份索引构建

```python
def build_author_year_index(references: list[Reference]) -> dict
# 返回: { "Smith-2020": Reference, "张三-2019": Reference, ... }
```

#### 3.1.3 施评文献作者列表标准化

```python
def normalize_authors(metadata: PaperMetadata) -> set[str]
# 返回施评文献所有作者的标准化名称集合（用于自引检测）
```

### 3.2 阶段 2：规则引擎层 `rule_engine.py`（新增，核心）

#### 3.2.1 整体流程

```python
def extract_candidates(
    sentences: list[Sentence],
    references: list[Reference],
    self_authors: set[str],   # 施评文献作者集合
) -> list[CandidateRecord]:
    """
    纯规则筛选，不调用 LLM
    """
    candidates = []
    for sent in sentences:
        # Step 1: 标志词匹配
        markers = match_markers(sent.text)
        if not markers:
            continue

        # Step 2: 提取句中的作者和年份
        authors_in_sent = extract_authors(sent.text)
        years_in_sent = extract_years(sent.text)

        # Step 3: 三要素校验
        if not authors_in_sent or not years_in_sent:
            continue

        # Step 4: 一票否决规则
        if is_self_citation(sent.text, authors_in_sent, self_authors):
            continue
        if is_bracket_only(sent.text, authors_in_sent, years_in_sent):
            continue
        if is_bare_word_only(markers):
            continue

        # Step 5: 匹配参考文献 + 过滤非期刊
        matched_refs = match_references(authors_in_sent, years_in_sent, references)
        if matched_refs and all(not r.is_journal for r in matched_refs):
            continue

        # Step 6: 多文献 / independently 拆分
        records = split_records(sent, markers, authors_in_sent, years_in_sent, matched_refs)
        candidates.extend(records)

    return candidates
```

#### 3.2.2 标志词匹配 `marker_matcher.py`（新增）

```python
# 将 900+ 标志词预编译为正则
MARKER_PATTERNS: list[re.Pattern]  # 从 markers.json 加载并编译

def match_markers(text: str) -> list[MarkerMatch]:
    """返回句中匹配到的所有标志词及其位置"""

@dataclass
class MarkerMatch:
    marker: str       # 匹配到的标志词
    start: int        # 在句中的起始位置
    end: int          # 结束位置
```

标志词存储方式：
- 从 `prompts.py` 中提取标志词列表 → 迁移到独立文件 `markers.json`
- 按长度降序排列（优先匹配长标志词，避免 `first` 吃掉 `first reported`）
- 预编译正则，使用 `\b` 词边界（英文）和前后断言（中文）

#### 3.2.3 作者提取 `author_extractor.py`（新增）

```python
def extract_authors(text: str) -> list[AuthorMention]:
    """从句子中提取作者姓名提及"""

@dataclass
class AuthorMention:
    name: str             # 作者名
    position: int         # 在句中的位置
    in_bracket: bool      # 是否在括号内
    with_et_al: bool      # 是否有 "et al."
    ref_numbers: list[int]  # 关联的引用编号 [1,2]
```

匹配模式：
- 英文：`Smith et al.`、`Smith and Jones`、`Smith (2020)`
- 中文：`张三等`、`张三和李四`
- 行内引用：`(Smith, 2020)`、`(Smith et al., 2020; Jones, 2019)`

#### 3.2.4 年份提取

```python
def extract_years(text: str) -> list[YearMention]:
    """提取句中所有年份"""

@dataclass
class YearMention:
    year: str         # "2020"
    position: int
    in_bracket: bool  # 是否在括号内
    is_decade: bool   # 是否为年代词 "the 1950s"
```

匹配模式：
- 精确年份：`2020`、`1999`
- 年代词：`the 1950s`、`the late 1970s`
- 时间定位：`as early as 1960`
- 中文编号说明：`在文【7,8】中`（视为有年份）

#### 3.2.5 一票否决规则

```python
# ── 自引检测 ──
SELF_CITATION_EN = {"our group", "our team", "our lab", "our laboratory",
                     "our previous work", "our earlier work", "our recent work",
                     "our study", "we", "us", "ourselves",
                     "the present authors", "the authors", "by the authors"}
SELF_CITATION_CN = {"本课题组", "本团队", "本实验室", "本研究组", "我们", "笔者", "作者本人"}

def is_self_citation(text, authors_in_sent, self_authors) -> bool:
    """检查是否为自引"""
    # 1. 自引词表匹配
    # 2. 作者名与施评文献作者重叠

# ── 括号规则 ──
def is_bracket_only(text, authors, years) -> bool:
    """检查作者+年份是否仅在括号内"""
    # 分析括号内外的作者和年份分布

# ── 裸词禁用 ──
BARE_WORDS = {"reported", "proposed", "discovered", "discovery",
              "described", "published", "demonstrated", "suggested", "provided"}

def is_bare_word_only(markers: list[MarkerMatch]) -> bool:
    """检查标志词是否全为裸词"""
```

### 3.3 阶段 3：LLM 语义判定层

#### 3.3.1 新 Prompt 设计（大幅精简）

**旧 Prompt**：~12,000 tokens（含 900+ 标志词、9 条规则、JSON 格式定义……）
**新 Prompt**：~800-1,200 tokens

```python
SEMANTIC_JUDGE_PROMPT = """你是学术评论句鉴定专家。

我会给你一组候选评论句，每条已由规则引擎预筛选（标志词、作者、年份已确认），
你只需做语义层面的最终判定。

## 判定标准

对每条候选句，判断：
1. 该句是否在**评价他人的学术贡献**（如首次发现、开创性工作、里程碑等）
2. 标志词是否确实在描述**学术发展事件**（而非实验操作步骤、一般叙述）
3. 三要素（标志词+作者+年份）是否指向**同一个研究事件**

## 判定结果

对每条候选句返回 JSON：
{
  "results": [
    {
      "id": 1,
      "accept": true/false,
      "reason": "简短理由"
    }
  ]
}

accept=true: 确认是学术评论句
accept=false: 不是学术评论句，给出理由
"""
```

#### 3.3.2 批量判定

```python
def judge_candidates(
    candidates: list[CandidateRecord],
    config: LLMConfig,
) -> list[JudgeResult]:
    """
    将候选句分批发送给 LLM 做语义判定
    每批 ~20 条，避免单次请求过大
    """
```

每条候选句发送给 LLM 的信息：

```
候选句 #1:
  原文: "Smith et al. first reported the synthesis of X in 2010 [3]."
  标志词: first reported
  被评作者: Smith
  年份: 2010
  上下文(前): "..."
  上下文(后): "..."
```

### 3.4 阶段 4：结果组装层

```python
def assemble_results(
    accepted_candidates: list[CandidateRecord],
    references: list[Reference],
    metadata: PaperMetadata,
) -> AnalysisResult:
    """
    将通过 LLM 语义判定的候选句组装为最终结果
    - 从 Reference 填充被评文献完整信息
    - 组装 JSON 结构
    - Pydantic 校验
    """
```

---

## 四、新增/修改文件清单

### 4.1 新增文件

| 文件 | 用途 |
|------|------|
| `core/sentence_splitter.py` | 学术论文句子分割 |
| `core/rule_engine.py` | 规则引擎主流程 |
| `core/marker_matcher.py` | 标志词正则匹配（预编译） |
| `core/author_extractor.py` | 句中作者名提取 |
| `core/year_extractor.py` | 句中年份提取 |
| `core/veto_rules.py` | 一票否决规则（自引/括号/裸词等） |
| `core/record_splitter.py` | 多文献拆分 / independently 拆分 |
| `core/result_assembler.py` | 结果组装（Reference 映射+字段填充） |
| `markers.json` | 标志词列表（从 prompts.py 迁出） |

### 4.2 修改文件

| 文件 | 改动 |
|------|------|
| `core/pipeline.py` | 重写 7 步流程为新 5 阶段流程 |
| `core/prompts.py` | 大幅精简，仅保留语义判定 Prompt |
| `core/llm_analyzer.py` | 改为语义判定接口（批量候选句→accept/reject） |
| `core/ref_parser.py` | 增强：支持更多引用格式 |

### 4.3 不变文件

| 文件 | 原因 |
|------|------|
| `core/pdf_parser.py` | 保持现有逻辑 |
| `core/pdf_highlighter.py` | 输入接口不变 |
| `core/excel_writer.py` | 输入接口不变 |
| `core/word_writer.py` | 输入接口不变 |
| `core/institution_lookup.py` | 保持现有逻辑 |
| `app.py` | 接口不变 |
| `config.py` | 接口不变 |

---

## 五、收益预估

| 维度 | 旧架构 | 新架构 | 改善 |
|------|--------|--------|------|
| LLM Prompt 大小 | ~12,000 tokens | ~1,000 tokens | **-92%** |
| LLM 输入总量 | Prompt + 全文（50-100k） | Prompt + 候选句（5-20k） | **-70~80%** |
| 标志词匹配准确率 | LLM 记忆（易漏） | 正则精确匹配 | **100% 召回** |
| 规则执行一致性 | LLM 理解偏差 | 确定性代码 | **100% 一致** |
| 自引检测 | 经常漏 | 确定性词表+作者匹配 | **显著提升** |
| API 成本 | 高（全文+长 Prompt） | 低（仅候选句+短 Prompt） | **降 60-80%** |
| 可调试性 | 黑盒 | 每步可追溯 | **大幅提升** |
| 新规则添加 | 改 Prompt 重新测试 | 加 Python 函数 | **更可控** |

---

## 六、实施计划

### 阶段一：基础设施（2-3 天）
1. 标志词列表迁移到 `markers.json` + 预编译正则
2. 实现句子分割器 `sentence_splitter.py`
3. 实现作者提取器 `author_extractor.py`
4. 实现年份提取器 `year_extractor.py`

### 阶段二：规则引擎（3-4 天）
5. 实现标志词匹配 `marker_matcher.py`
6. 实现一票否决规则 `veto_rules.py`
7. 实现多文献拆分 `record_splitter.py`
8. 实现规则引擎主流程 `rule_engine.py`

### 阶段三：LLM 语义判定（1-2 天）
9. 设计精简 Prompt
10. 实现批量语义判定接口
11. 实现结果组装 `result_assembler.py`

### 阶段四：集成 + 验证（2-3 天）
12. 重写 `pipeline.py` 串联新流程
13. 用旧架构结果作为 baseline 对比测试
14. 调优规则参数

### 总计：约 8-12 天

---

## 七、风险与缓解

| 风险 | 缓解措施 |
|------|----------|
| 句子分割不准确（`et al.` 等干扰） | 维护常见缩写词表，用负向前瞻排除 |
| 作者名提取误匹配（短姓氏如 Li、Wu） | 结合引用编号和参考文献列表交叉验证 |
| 规则引擎过于严格导致召回率下降 | 设"待人工审核"兜底通道，规则引擎不确定的也送 LLM 判定 |
| 中英文混合论文 | 作者/年份提取器需同时支持中英文模式 |
| 新架构与旧架构结果差异大 | 并行运行对比测试，逐步切换 |
