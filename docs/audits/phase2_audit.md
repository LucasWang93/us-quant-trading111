# Phase 2 审计报告（SPY/QQQ 真实数据回测，PR #2）

- 首次审计：2026-11-10，对象为提交 `480d0ed`，结论 **DO NOT MERGE**。
- 修复后复核：2026-11-10，对象为提交 `9ba20b9` 及其后的结果提交（见第 4 节）。
- 环境：Python 3.13.18、pandas 3.0.5、numpy 2.5.3、yfinance 1.8.0。网络已放行 Yahoo Finance，Stooq 被站点拒绝。
- 未下单，仓库中也没有任何下单代码。

## 1. 首次审计的三个阻断问题

| # | 问题 | 证据 |
|---|---|---|
| B1 | 数据 checksum 无法复现 | `src/usquant/pipeline.py:69` 对内存中的全精度下载帧取 checksum，而 `src/usquant/data/store.py:46` 按 `%.6f` 写缓存。因此第一次运行与之后读缓存的运行得到不同的 checksum（SPY：`b512477…` 对比 `57e1be96…`）。此外，Yahoo 在不同时间返回的 adj_close 有约 1e-6 的差异（实测 SPY 6,955 行中有 5,710 行不同，最大 0.000244 美元），重新下载也无法复现 |
| B2 | 文档宣称可以精确复现，但不成立 | `docs/data.md:27` 写着"Use the cached files to reproduce an exact run"；报告中的 checksum 说明也有同样的问题 |
| B3 | 没有真实结果的回归测试 | 所有测试都是离线的合成数据测试，没有测试把结果和提交的产物做比对 |

## 2. 修复过程中新发现的问题（比 B1 更严重）

| # | 问题 | 证据 |
|---|---|---|
| B4 | PR #2 实际上**从未包含**研究结果 | `.gitignore:224` 的 `results/`（本意是只忽略根目录的运行输出）同时匹配了 `docs/results/`。`git show --stat 480d0ed` 只有 `README.md`，`report.md`、`summary.json`、图表和 `VERIFICATION.md` 都没有提交，README 的链接指向一个不存在的文件。首次审计把本地文件当作"已提交产物"来比对，这一点在此更正 |

## 3. 修复内容

| 问题 | 修复（文件） | 回归测试（`tests/test_reproducibility.py`） |
|---|---|---|
| B1 | `src/usquant/data/store.py`：下载后从缓存文件重新读回，只使用磁盘上的数据；在 provenance 中记录 `cache_file` 和 `cache_sha256`，缓存文件被改动时给出警告。`src/usquant/data/rates.py` 对 ^IRX 做了同样处理。`src/usquant/data/provenance.py` 新增这两个字段 | `test_download_and_cache_hit_return_identical_data`（旧代码下会失败）、`test_provenance_records_cache_file_hash_and_detects_edits`、`test_risk_free_download_and_cache_hit_are_identical` |
| B1 / B3 | 新增 `src/usquant/reproduce.py` 和 `usquant reproduce` 命令，把"输入是否相同"与"数值是否相同"分开判断。整数必须完全相等；浮点数在相同输入下用相对容差 1e-9，加 `--refresh` 时用 1e-4。退出码：0 表示复现成功，1 表示数值不一致，2 表示只有输入不同 | `test_reproduce_exact_then_detects_changed_input`、`test_compare_summaries_tolerance_and_integer_rules` |
| B2 | `docs/data.md` 新增 *Reproducibility* 一节；`README.md` 加入复现说明；`study.py` 生成的报告增加"input file SHA-256"列和 *Reproduce* 说明 | `test_report_documents_input_hashes_and_reproduction_limits`、`test_committed_report_matches_committed_summary_hashes` |
| B3 | 新增 `tests/golden/synthetic_study_summary.json`（不依赖网络，在 CI 中运行），并为真实数据加了精确复现测试（只在本地存在对应哈希的输入文件时运行） | `test_engine_results_match_golden_snapshot`、`test_committed_spy_qqq_study_reproduces_exactly` |
| B4 | `.gitignore` 中的规则改为 `/results/` 和 `/data/cache/`，只作用于根目录，并提交 `docs/results/spy_qqq_study/` | `test_committed_results_are_not_gitignored_but_raw_data_is`、`test_committed_results_are_tracked_by_git` |

回测假设和策略都没有改动：引擎、成本、策略、指标的代码未修改。

## 4. 执行证据（修复后）

| 命令 | 退出码 | 结果 |
|---|---|---|
| `usquant -v study --config configs/study_spy_qqq.yaml --run-dir <scratch>/regen`，在干净工作区、提交 `9ba20b9` 上运行，再复制到 `docs/results/spy_qqq_study/` | 0 | 下载次数为 0，全部来自缓存（数据于 08:31Z 下载）。报告头部写的是 `git 9ba20b9…`，不带 `-dirty` |
| 新产物与修复前的本地产物对比 | — | 705 个值全部在 1e-6 相对误差内；整数不一致 0 个；最大绝对差 0.01 美元（最终净值约 125 万美元）；`report.md` 的结果表文本完全相同 |
| `usquant reproduce --summary docs/results/spy_qqq_study/summary.json` | 0 | 输入相同；比较 705 个值；最大差 0.0 |
| `usquant reproduce ... --refresh --cache-dir <scratch>/fresh_cache` | 0 | 输入不同（符合预期）；705 个值全部在 1e-4 容差内；整数不一致 0 个；最大绝对差 4.04 美元 |
| `usquant reproduce ... --cache-dir <scratch>/fresh_cache`（严格模式，输入不同） | 1 | 正确报告"numbers do NOT reproduce" |
| `python -m pytest -rs` | 见第 6 节 | — |

新的输入文件哈希：SPY `bf74ffe799578f81…`、QQQ `a00f630db3fd8f33…`、IRX `0aeade617417a3c7…`。新的数据 checksum：SPY `57e1be96812c81af`、QQQ `f1c50600ac1624f8`，与首次审计时从缓存重跑得到的值一致。

## 5. 复核表

| 检查项 | 证据 | 结论 | 剩余问题 | 处理方式 |
|---|---|---|---|---|
| 真实数据 | `summary.json` 中的 `data_provenance`；`report.md` 的 provenance 表 | Pass | 只有单一供应商 | 非阻断：后续用授权数据通过 `import-csv` 复核 |
| 无前视偏差 | `src/usquant/backtest/engine.py:134-165`；`tests/test_engine.py:19,107`；`tests/test_strategies.py:80`；`VERIFICATION.md` 第 5 项 | Pass | 下单数量用当日开盘价计算（`engine.py:62-63,135-144`），略偏乐观 | 非阻断：后续加敏感性测试 |
| 成本与净值计算 | `src/usquant/backtest/costs.py:30-37,60-65`；`VERIFICATION.md` 第 6 项 | Pass | — | — |
| checksum 与 provenance 可复现 | `store.py`、`rates.py`、`reproduce.py`；第 4 节 | Pass | 原始数据不能再分发，所以没有原始文件的人只能在 1e-4 容差内复现 | 已在 `docs/data.md` 中写明 |
| 文档准确性 | `docs/data.md` 的 *Reproducibility* 一节；`report.md` 的 *Reproduce* 一节 | Pass | — | — |
| 回归测试 | `tests/test_reproducibility.py`、`tests/golden/` | Pass | 真实数据测试在 CI 中会跳过，因为 CI 没有原始文件 | 已写明；golden 快照测试在 CI 中运行 |
| 结果已提交 | `.gitignore:223-224`；`git ls-files docs/results` | Pass | — | — |
| 实盘交易 | `src/usquant/broker/base.py` | Pass | — | — |

## 6. 测试总数

第 4 节的命令全部执行完毕。最后一次 `python -m pytest -rs` 的结果和 CI 状态见 PR #2 的提交记录和 CI 页面；提交时的本地结果为 **74 passed, 0 skipped, 0 failed（退出码 0）**，其中包括真实数据的精确复现测试。

## 7. 建议

**MERGE（以 CI 通过为前提）。** 四个阻断问题（B1 至 B4）都已修复，并各有回归测试。研究结论和数值保持不变（误差不超过 1e-6，整数完全一致）。剩余两项（单一数据源、开盘价定量的乐观偏差）不阻断合并，留作后续工作。按要求，本次没有执行合并，也没有下单。
