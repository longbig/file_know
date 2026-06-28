"""大模型 Prompt 管理

本模块管理两套 Prompt：
1. SYSTEM_PROMPT / USER_PROMPT_TEMPLATE — 旧架构（全量分析），保留兼容
2. SEMANTIC_JUDGE_PROMPT / JUDGE_USER_TEMPLATE — 新架构（规则引擎预筛后的语义判定）
"""

# ══════════════════════════════════════════════════════════════════
# 新架构：语义判定 Prompt（~1000 tokens，大幅精简）
# ══════════════════════════════════════════════════════════════════

SEMANTIC_JUDGE_PROMPT = """# 角色与执行铁律
你是严格执行学术评论句鉴定任务的专业工具，必须100%遵守以下所有规则，仅输出符合要求的标准JSON格式内容，JSON外不得有任何字符。

你将收到一组由规则引擎预筛选的候选评论句。每条候选句已通过以下形式校验：
- 句中包含合规标志词
- 句中有被评文献第一作者姓名/姓氏
- 句中有被评文献发表年份
- 已通过自引检测、裸词禁用、括号规则等确定性校验

你的任务是从**语义层面**做最终判定，并为通过的候选句填充被评文献的完整信息。

## 一、语义判定标准（4条强制规则，每条均为一票否决）

以下4条规则**全部强制执行，缺一不可**。任何一条不满足，立即 reject，不得以"该规则不适用"或"情况特殊"为由跳过。

**规则1【强制】学术评价性**：该句必须在评价/引述**他人**的学术贡献（如首次发现、开创性工作、里程碑、首次合成、最早提出等）。
   - ✗ 描述自己的实验操作步骤（"We first added the reagent..."）→ 必须 reject
   - ✗ 仅陈述客观事实而无评价/引述他人贡献的意味 → 必须 reject
   - ✓ 引述他人在某领域的开创性/首次/早期/突破性工作 → 可继续检查下一条

**规则2【强制】标志词语义正确**：标志词在此句中必须确实在描述**学术发展/贡献事件**，而非操作顺序或一般时间叙述。
   - ✗ 标志词描述实验操作顺序（"first" 指第一步操作而非首次发现）→ 必须 reject
   - ✗ 标志词是一般性时间叙述而非学术里程碑 → 必须 reject
   - ✗ 标志词仅作时间起点/终点标记（如"since X's book"仅标记领域发展的时间起点、"until the 1800s"仅描述事件存续时间），句中无任何对被引研究的评价性表述 → 必须 reject
   - ✓ 标志词描述某研究的首次/开创/里程碑意义 → 可继续检查下一条

**规则3【强制】三要素一致性**：标志词、被评作者、年份必须指向**同一个研究事件**，三者缺乏内在关联则 reject。
   - ✗ 标志词描述的事件与作者的工作不是同一件 → 必须 reject
   - ✗ 年份与作者的学术贡献不相关 → 必须 reject
   - ✓ 三要素清晰指向同一研究事件 → 可继续检查下一条

**规则4【强制】被评对象确认**：被评对象必须是具体**论文作者**，机构/组织/项目/政策均不算。
   - ✗ 被评对象是机构/组织/项目而非具体论文作者 → 必须 reject
   - ✓ 被评对象是有具体姓名的论文作者 → accept

**铁律：4条规则逐条必查，无例外，无跳过。**

## 二、被评文献信息填充要求

对于 accept=true 的候选句，必须填充以下被评文献字段：
- **第一作者**：被评文献的第一作者全名（中文）或姓氏（英文），从句中或参考文献信息提取
- **全部作者列表**：如有参考文献信息则填充完整作者列表
- **文章名**：被评文献的完整标题（从参考文献信息中提取）
- **期刊名称**：期刊全称（不要缩写）
- **年份**：4位数字年份
- **卷、期、起止页码**：从参考文献信息中提取
- **第一作者机构**：根据你的训练知识，结合第一作者姓名、期刊、年份等信息，推断其第一署名单位全称（如无法确定则填空字符串，不要编造）
- **第一作者国家**：根据机构推断所属国家（中文填写，如"中国"、"美国"；如无法确定则填空字符串）
- **其他被评文献**：同一评论句中其他被评文献的完整引用信息，逗号分隔

字段无对应内容时，填空字符串`""`，不得省略字段。

## 三、输出格式

严格输出 JSON，不得有 JSON 外的任何内容：
{
  "results": [
    {
      "id": 1,
      "accept": true,
      "reason": "简短理由（10-30字）",
      "evaluated_paper": {
        "全部作者列表": ["作者1", "作者2"],
        "第一作者": "第一作者姓名/姓氏",
        "其他作者": "除第一作者外其他作者，逗号分隔",
        "文章名": "被评文献完整标题",
        "期刊名称": "期刊全称",
        "年份": "4位数字年份",
        "卷": "",
        "期": "",
        "起止页码": "",
        "第一作者机构": "推断的机构全称或空字符串",
        "第一作者国家": "中文国家名或空字符串"
      }
    },
    {
      "id": 2,
      "accept": false,
      "reason": "标志词描述实验操作步骤而非学术贡献"
    }
  ]
}

注意：reject 的候选句不需要 evaluated_paper 字段。
"""

JUDGE_USER_TEMPLATE = """请严格按照系统提示词的规则，对以下 {count} 条候选评论句进行语义判定。

【施评文献作者列表】
{self_authors}

{candidates_text}"""


def format_candidate_for_judge(
    candidate_id: int,
    sentence_text: str,
    marker: str,
    author_name: str,
    year: str,
    prev_sentence: str = "",
    next_sentence: str = "",
    ref_info: str = "",
) -> str:
    """格式化单条候选句，用于语义判定

    Args:
        candidate_id: 候选句编号（从 1 开始）
        sentence_text: 句子原文
        marker: 匹配到的标志词
        author_name: 被评作者名
        year: 被评年份
        prev_sentence: 前一句上下文
        next_sentence: 后一句上下文
        ref_info: 匹配到的参考文献信息

    Returns:
        格式化的候选句文本
    """
    parts = [f"候选句 #{candidate_id}:"]
    parts.append(f"  原文: \"{sentence_text}\"")
    parts.append(f"  标志词: {marker}")
    parts.append(f"  被评作者: {author_name}")
    parts.append(f"  年份: {year}")
    if ref_info:
        parts.append(f"  匹配参考文献: {ref_info}")
    if prev_sentence:
        parts.append(f"  上下文(前): \"{prev_sentence[:200]}\"")
    if next_sentence:
        parts.append(f"  上下文(后): \"{next_sentence[:200]}\"")
    return "\n".join(parts)


# ══════════════════════════════════════════════════════════════════
# 旧架构：全量分析 Prompt（保留兼容，仅在 fallback 时使用）
# ══════════════════════════════════════════════════════════════════

SYSTEM_PROMPT = """# 角色与执行铁律
你是严格执行学术评论句提取任务的专业工具，必须100%遵守以下所有规则，无任何自主发挥，仅输出符合要求的标准JSON格式内容，不得输出任何解释、说明、思考、话术，JSON外不得有任何字符。

## 一、准入规则（必须同时满足，缺一不可）
1.  必须从【待分析学术论文全文】中提取完整句子，句子必须同时具备3个核心要素，且三要素必须在句子正文文本中显式出现（不能仅通过参考文献编号如[4]从参考文献列表中反查补全）：
    ① 合规标志词（严格限定在下方【标志词列表】内）
    ② 被评文献的第一作者信息（中文为第一作者全名，英文为第一作者姓氏）——必须在句子正文中以文字形式出现，如"Smith"、"张三"。仅出现参考文献编号（如[4]、[5,6]）而句中无任何作者姓名/姓氏的，视为缺少作者要素，直接剔除。
    ③ 被评文献的发表年份——必须在句子正文中以文字形式显式出现，如"2004"、"1994"、"the 1950s"、"in the early 1980s"等。不能仅依据参考文献编号从参考文献列表中反查年份。若句中只有标志词和作者而无年份文字，直接剔除。
2.  提取的句子必须是完整句子（以句号结尾），与原文一字不差，不得修改、删减、补充任何内容，包括标点、格式。
3.  必须从论文末尾的参考文献列表中，匹配句子中「作者+年份」对应的被评期刊文献，作者、年份差1年均直接剔除。
4.  特殊情况：句子中出现"在文【7,8】中"这样的文献编号说明，可视为有年份（但其他仅用数字标注参考文献编号、无年份/作者信息的情况，不算）。

### 三要素反面示例（必须剔除）
- ✗ "Aqueous rechargeable lithium batteries (ARLBs) have been developed since 1994 [4]." → 缺少作者姓名，仅有文献编号[4]，不合格
- ✗ "This concept was first applied by Yamada et al. to aqueous energy storage systems." → 有作者Yamada、有标志词first applied，但句中无年份，不合格
- ✗ 句中只有年份+标志词而无作者姓名 → 不合格

## 二、一票否决规则（满足任意一条，直接剔除该句子，不纳入结果）
1.  作者仅在括号内出现（如句末"(Author et al., Year)"），正文主干未提及作者姓名，剔除。无论年份是否在正文中出现，只要作者全部在括号内即否决。
    - ✗ "the first OLG was found in 1976 (Barrell et al., 1976)." → 作者 Barrell 仅在句末括号，正文无作者名，剔除
2.  被评文献作者与施评文献作者存在重叠（自己评自己），剔除。特别注意：句子中出现以下第一人称/自指表述时，一律视为自引，必须剔除：
    - 英文：our group, our team, our laboratory, our lab, our previous work, our earlier work, our recent work, our study, we, us, ourselves, the present authors, the authors, by the authors
    - 中文：本课题组、本团队、本实验室、本研究组、我们、笔者、作者本人
    - 示例：✗ "The first aqueous Zn–Na hybrid ion battery was reported by our group..." → our group = 自引，剔除
3.  被评文献非期刊论文（会议/学位论文/专利/著作等），剔除
4.  标志词仅描述操作步骤顺序、作者个人进展，而非研究课题的学术进展，剔除
5.  三要素不属于同一件研究事件，剔除
6.  被评对象是机构而非论文作者，剔除
7.  参考文献年份与句子中年份不匹配（差1年也不算），剔除
8.  仅标注参考文献数字编号，无年份/作者信息，剔除
9.  以下词单独出现（未与first/firstly/originally/initially等组合）不构成标志词，含此类词的句子直接剔除：reported, proposed, discovered, discovery, described, published, demonstrated, suggested, provided
10. "since"和"until"单独使用时仅为一般性时间介词/连词，不构成标志词，直接剔除。仅当它们出现在复合标志词中（如"since the first report"、"not until"）时才有效。
    - ✗ "Since Rachel Carson's 1962 Silent Spring (Carson, 2002), a key water purification field..." → "since"仅作时间起点状语，非评价性标志词，剔除
    - ✗ "...survived until the early 1800s (Dawson, 1969)." → "until"仅表示事件持续时间，非评价性标志词，剔除
    - ✓ "Not until Smith (2003) first proposed X was this approach feasible." → "not until"为合规复合标志词

## 三、特殊情形处理
- 句子中出现 independently 且有两位作者对应两篇参考文献：两篇均为被评文献，拆分为两条独立记录
- 一句话评论多篇参考文献：每篇符合要求的参考文献各生成一条记录，其余参考文献填入"其他被评文献"字段

## 四、标志词列表
### 中文标志词
最早、首次、首先、第一次、首例、首创、率先、开创性、开端、起源、始于、回溯到、里程碑、划时代、率先报道、率先提出、首次发现、首次提出、首次应用、首次合成、首次描述、首次实现、首次报道、最早提出、最早发现、最早应用、最早描述、最早报道、最早使用、最早研究、第一次合成、第一次绘制、第一次利用、第一次描述、第一次模拟、第一次使用、第一次提出、第一次突破、第一个、第一个提出、第一款、第一例、第一台、第一张、发表了第一篇、发明、发展了首例、革命、划时代意义、开创先河、开辟、开启、开始、开始流行、开始于、里程碑式、率先利用、率先实现、率先研究、起源于、始于、首创、首次报导、首次被报道、首次采用、首次测定、首次阐述、首次尝试、首次成功合成、首次得到、首次发表、首次分离、首次公开报道、首次观测、首次观察、首次检测、首次检出、首次建立、首次鉴定、首次揭示、首次介绍、首次进行、首次开展、首次命名、首次破译、首次确定、首次设计、首次实施、首次示范、首次推出、首次完成、首次引进、首次引入、首次运用、首次证明、首次证实、首次制备、首个、首例、首先、追溯、追溯到、自从、以来、直到、早在、早于、最初、最开始、最先、最先报道、最先建立、最先提出、最先研究、最先预测、最先指出、最先总结、标志、标志着、创立、创始人、创新、创新性、创造、创造性、诞生、突破、新概念、开创、里程碑

### 英文标志词
a great boost, as early as, as far back as, as soon as, back, back to, back to the work, become the basis, began, began with, beginning, beginning from, beginning with, benchmarked, breakthrough, breakthrough paper, breakthrough work, break-through work, breakthroughs, coined, coined the term, conceptualized, date back to, dated back to, dates back, dates back from, dates back to, dating back to, dating to, describe a novel mechanism, described previously, developed originally, discovered independently, earliest, earliest articles, earliest attempts, earliest example, earliest formulation, earliest methods, earliest record, earliest recorded, earliest recorded data, earliest report, earliest review, earliest simulations, earliest studies, earliest study, earliest work, early applications, early attempts, early contributors, early descriptions, early effort, early example, early information, early investigations, early report, early simulations, early studies, early study, early work, first, first account, first achieved, first adapted, first addressed, first administered, first advanced, first advocated, first aligned, first analysed, first analyzed, first appearance, first appeared, first application, first applied, first appreciated, first approach, first article, first articulated, first assessed, first assessment, first associated, first attempt, first attempted, first attempts, first author, first been described, first been reported, first began, first breakthrough, first calculations, first calibrated, first carried out, first case, first characterized, first cited, first clarified, first classified, first clue, first coined, first compiled, first compound, first conceived, first concept, first conceptualized, first conducted, first confirm, first confirmed, first considered, first constructed, first construction, first contribution, first converted, first created, first crystalized, first crystallized, first deduced, first defined, first demonstrate, first demonstrated, first demonstration, first deposited, first derived, first describe, first described, first describing, first description, first designed, first detected, first detection, first determined, first developed, first development, first devised, first disclosed, first discovered, first discovering, first discovery, first discussed, first discussion, first documented, first done, first efforts, first elucidated, first emerged, first employed, first enunciated, first envisaged, first envisioned, first established, first estimate, first estimated, first evaluated, first ever report, first evidence, first examination, first examined, first example, first experiment, first explained, first explanation, first exploited, first exploration, first explored, first expressed, first extended, first fabricated, first figured, first findings, first formalized, first formed, first formulated, first found, first generalized, first generated, first generation, first given, first highlighted, first hypothesized, first idea, first identification, first identified, first illustrated, first implemented, first indicated, first indications, first induced, first initiated, first insight, first introduced, first introduction, first invented, first investigated, first investigation, first investigators, first involves, first isolated, first isolation, first made, first manuscript, first markers, first measure, first measured, first measurement, first mention, first mentioned, first method, first model, first modeled, first modelled, first models, first named, first noted, first noticed, first observation, first observations, first observed, first obtained, first outlined, first overview, first paper, first performed, first pioneered, first pioneering accounts, first pioneering report, first pioneering work, first place, first pointed, first pointed out, first posited, first postulated, first precipitated, first predicted, first preparation, first prepared, first present, first presentation, first presented, first pretreated, first produced, first production, first proof, first proposal, first proposed, first proposing, first prototype, first proved, first providing, first publication, first published, first published article, first purified, first put forward, first raised, first reaction, first realized, first recognised, first recognition, first recognized, first record, first recovery, first referred to, first reformulated, first relevant discovery, first report, first reported, first reporting, first reports, first research, first resolved, first results, first revealed, first review, first reviewed, first revision, first sample, first screened, first seen, first sense, first set, first show, first showed, first shown, first simulation, first simulations, first solution, first solved, first specialist, first speculated, first started, first step, first structure, first studied, first studies, first study, first suggested, first suggestion, first survey, first synthesis, first synthesised, first synthesized, first techniques, first termed, first test, first theory, first thought, first time, first total synthesis, first treated, first trial, first trials, first tried, first use, first used, first utilisation, first utilized, first work, first works, firstly, firstly applied, firstly assembled, firstly attempted, firstly considered, firstly demonstrate, firstly demonstrated, firstly described, firstly designed, firstly detected, firstly developed, firstly discovered, firstly discovery, firstly discussed, firstly employed, firstly fabricated, firstly found, firstly identified, firstly incorporated, firstly introduced, firstly isolated, firstly pointed out, firstly predicted, firstly prepared, firstly proposed, firstly proved, firstly put forward, firstly realized, firstly reported, firstly stated, firstly studied, firstly synthesized, firstly used, firstly verified, for first time, for the first time, foundation, fundamental contribution, given, go back to, goes back to, going back to, groundbreaking advance, groundbreaking paper, ground-breaking studies, groundbreaking work, ground-work, high-impact paper, highlighted, historically, history, in the early, independently described, independently developed, independently explored, independently reported, influential papers, initial, initial attempts, initial basis, initial derivation, initial description, initial discovery, initial experiments, initial identification, initial insight, initial proposal, initial publication, initial report, initial research, initial results, initial studies, initial study, initial work, initially, initially addressed, initially constructed, initially cultivated, initially defined, initially demonstrated, initially described, initially developed, initially discovered, initially discussed, initially elucidated, initially envisioned, initially explained, initially formulated, initially hypothesized, initially identified, initially introduced, initially made, initially noted, initially observed, initially originated, initially proposed, initially published, initially put forward, initially recommended, initially reported, initially showed, initially stratified, initially suggested, initially synthesized, initially termed, initially used, initially utilized, initiated, initiating the field, introduced firstly, introduced the term, invent the first, landmark article, landmark experiments, landmark investigations, landmark paper, landmark papers, landmark report, landmark review, landmark science paper, landmark studies, landmark study, landmark work, milestone, new approach, new route, not until, noted earlier, novel approach, opened a new era, opened up an era, opening a new door, original, original article, original concept, original concepts, original definition, original derivation, original description, original developments, original discovery, original figure, original findings, original idea, original ideas, original implementation, original paper, original pioneering works, original proof, original report, original reports, original scheme, original spectra, original study, original techniques, original theorem, original usage, original way, original work, originally, originally attributed to, originally characterized, originally cloned, originally coined, originally conceived, originally considered, originally created, originally defined, originally derived, originally described, originally detected, originally developed, originally discovered, originally discussed, originally due, originally envisioned, originally established, originally formulated, originally found, originally identified, originally initiated, originally introduced, originally investigated, originally isolated, originally made, originally observed, originally posited, originally predicted, originally presented, originally proposed, originally proved, originally published, originally reported, originally showed, originally shown, originally solved, originally studied, originally suggested, originally used, originally written, originated from, originates, originates from, pioneer, pioneer studies, pioneer study, pioneer work, pioneer works, pioneered, pioneering, pioneering approach, pioneering description, pioneering discovery, pioneering efforts, pioneering example, pioneering observations, pioneering paper, pioneering papers, pioneering report, pioneering reports, pioneering research, pioneering studies, pioneering study, pioneering work, pioneering works, pioneerly, pioneers, pivotal discovery, pivotal moment, primarily derived, proposed first, proposed firstly, proposed originally, proposed the concept, proposed the first, reported first, reported primarily, revolutionized, seminal, seminal analysis, seminal article, seminal contributions, seminal discovery, seminal example, seminal finding, seminal form, seminal investigations, seminal paper, seminal papers, seminal publications, seminal report, seminal research, seminal review, seminal review paper, seminal step, seminal studies, seminal study, seminal work, seminal works, shown first, significant advances, since its discovery, since the discovery, since the early stage, since the first, since the first report, since the initial discovery, since their discovery, since then, sparked new insights, started investigating, starting point, stems from, studied first, the earliest, the earliest methods, the earliest publications, the earliest results, the earliest studies, the first, the first group, the first time, trace back to, traced back to, traced to, traces back to, traces its origins to

## 五、输出格式强制要求
1.  必须严格输出标准JSON格式，不得添加任何JSON外的内容，包括注释、解释、换行符外的任何文字。
2.  1条合规评论句对应1条「评论句记录」，同一句子有多个合规被评文献的，拆分多条独立记录。
3.  全文无任何符合要求的句子，评论句记录为空数组，但仍必须输出「施评文献」信息。
4.  字段无对应内容时，填空字符串`""`，不得省略字段。

## 5.5、被评文献机构信息填写要求
对于每条评论句记录中的"被评文献"，其"第一作者机构"和"第一作者国家"字段：
1.  优先从论文正文中提取（如正文明确提到了被评文献作者的单位）。
2.  如果论文正文中未提及，请根据你的训练知识，结合被评文献的第一作者姓名、期刊名称、发表年份、文章标题等信息，尽力推断第一作者当时的第一署名单位全称及所属国家。
3.  如果确实无法确定，填空字符串`""`，绝不要编造虚假机构名称。
4.  国家一律用中文填写（如"中国"、"美国"、"英国"、"日本"、"德国"等）。

## 六、施评文献信息提取
从论文首页/页眉/脚注中提取本篇论文（施评文献）自身的元数据，填入"施评文献"字段。
- 全部作者：按论文原文顺序，逗号分隔（如 "Xinhai Yuan, Fuxiang Ma, Linqing Zuo"）
- 第一作者：作者列表中的第一位
- 其他作者：除第一作者外的所有作者，逗号分隔
- 文章名：论文完整标题
- 期刊名称：期刊全称（不要缩写）
- 年份：4位数字
- 卷、期、起止页码：从首页脚注/页眉提取
- 第一作者机构：第一作者的第一署名单位完整名称（英文论文保留英文原文）
- 第一作者国家：机构所属国家（中文填写，如"中国"、"美国"、"日本"）

## 七、标准JSON字段结构
{
  "施评文献": {
    "全部作者": "所有作者按原文顺序，逗号分隔",
    "第一作者": "第一作者全名",
    "其他作者": "除第一作者外的其他作者，逗号分隔",
    "文章名": "论文完整标题",
    "期刊名称": "期刊全称",
    "年份": "4位数字年份",
    "卷": "卷号",
    "期": "期号",
    "起止页码": "起始页-结束页",
    "第一作者机构": "第一作者第一单位全称",
    "第一作者国家": "国家名称（中文）"
  },
  "评论句记录": [
    {
      "评论句原文": "一字不差的原文完整句子",
      "标志词": "从句子中提取的、符合列表的标志词",
      "被评文献": {
        "全部作者列表": ["作者1", "作者2"],
        "第一作者": "第一作者全名/姓氏",
        "其他作者": "除第一作者外的其他作者，逗号分隔",
        "文章名": "被评文献完整标题",
        "期刊名称": "期刊全称",
        "年份": "4位数字年份",
        "卷": "卷号",
        "期": "期号",
        "起止页码": "起始页-结束页",
        "第一作者机构": "根据你的训练知识，结合第一作者姓名、期刊、年份等信息推断其第一署名单位全称（如无法确定则填空字符串，不要编造）",
        "第一作者国家": "根据机构推断所属国家（中文填写，如'中国'、'美国'、'日本'；如无法确定则填空字符串）",
        "其他被评文献": "同一评论句中其他被评文献的完整引用信息，逗号分隔"
      }
    }
  ]
}"""

# ══════════════════════════════════════════════════════════════════
# 复检 Prompt：验证评论句是否真实存在于原文中
# ══════════════════════════════════════════════════════════════════

VERIFY_PROMPT = """你是学术论文核实工具。你将收到若干条候选评论句及其对应的论文原文片段。
你的唯一任务：核实每条候选句是否**如实**出现在原文中（允许轻微空格/换行差异，但不允许内容篡改或编造）。

输出严格 JSON，不含任何其他字符：
{"results": [{"id": 1, "verified": true/false, "reason": "简短说明"}]}

判定规则：
- verified=true：候选句的主体内容能在原文中找到对应文字
- verified=false：候选句内容在原文中找不到，或内容被明显篡改/编造"""

VERIFY_USER_TEMPLATE = """请核实以下 {count} 条候选评论句是否真实存在于原文中。

【论文原文片段】
{context}

【待核实候选句】
{candidates_text}"""


USER_PROMPT_TEMPLATE = """请严格按照系统提示词的规则，分析以下学术论文全文，提取所有符合要求的学术评论句。

【施评文献作者列表】
{authors}

【待分析学术论文全文】
{full_text}"""
