# 素材审核与发布排期 Agent

它通过一个有步数上限的“决策—工具—观察”循环，完成素材读取、规则审核、档期查询和排期草稿保存。系统**只保存草稿，不执行真实发布**。

## 评审入口

- 核心循环：`src/schedule_agent/agent.py`
- 模型决策与离线 Mock：`src/schedule_agent/decision.py`
- 业务工具和强制边界：`src/schedule_agent/tools.py`
- 可执行测试：`tests/`
- 实际输出：`examples/outputs/`
- 完整 Trace：`examples/traces/`
- 测试报告：`examples/test-results.xml`
- 需求、方案和评估：`docs/`

## 环境与安装

已验证环境：Python 3.12.4、pytest 7.4.4。运行时代码只使用 Python 标准库。

```powershell
cd material-review-scheduler-agent
python -m venv .venv
.venv\Scripts\Activate.ps1
python -m pip install -e . -r requirements-dev.txt
```

macOS 或 Linux 的激活命令为：

```bash
source .venv/bin/activate
```

## 运行

离线 Mock 会根据工具的实际返回选择下一动作，用于复现流程和测试边界；它不证明真实模型的决策质量。

```powershell
schedule-agent run `
  --input examples/inputs/slot-conflict.json `
  --model mock `
  --state-file data/drafts.local.json `
  --trace-out trace.json
```

也可不安装，直接设置源码路径后运行：

```powershell
$env:PYTHONPATH = "src"
python -m schedule_agent.cli run --input examples/inputs/normal.json --model mock
```

真实模型模式使用 OpenAI 兼容的 `/chat/completions` JSON 接口：

```powershell
$env:MODEL_API_KEY = "your-key"
$env:MODEL_BASE_URL = "https://api.openai.com/v1"
$env:MODEL_NAME = "your-model"

schedule-agent run `
  --input examples/inputs/slot-conflict.json `
  --model api `
  --trace-out trace-api.json
```

密钥只从环境变量或命令行读取，不应提交 `.env`。仓库同时保留离线 Mock Trace 和 `qwen3.5-plus` 真实模型 Trace；真实记录使用 `api-` 文件名前缀，且不包含密钥。

项目启动时会自动读取仓库根目录的 `.env`，并兼容 DashScope/Qwen 的 `DASHSCOPE_API_KEY`、`QWEN_BASE_URL`、`QWEN_MODEL` 变量名。终端中已经存在的环境变量优先，不会被 `.env` 覆盖。

## 测试与复现

```powershell
python -m pytest
python scripts/generate_examples.py
```

当前结果：`15 passed`。示例生成脚本只清理并重建 Mock 使用的合成草稿状态，不修改 `data/` 或 `api-` 开头的真实模型验证状态。

## 已覆盖场景

| 场景 | 输入 | 预期结果 |
|---|---|---|
| 正常排期 | `normal.json` | 审核通过并保存草稿 |
| 信息缺失 | `missing-metadata.json` | 阻断，指出 `rights_status` 缺失 |
| 规则风险 | `rule-risk.json` | 阻断，给出命中规则 |
| 时段冲突 | `slot-conflict.json` | 建议 19:00，等待确认，不保存 |
| 确认替代时段 | `slot-confirmed.json` | 复查 19:00 后保存草稿 |
| 重复执行 | `duplicate.json` | 第二次返回同一草稿，不新增记录 |
| 保存失败 | `save-failure.json` | 明确失败，不虚报成功 |

## 核心边界

- 模型只能从五种动作中选择，不能直接写入草稿文件。
- 替代硬性发布时间前必须取得用户明确确认。
- 规则风险或必填信息缺失时禁止保存。
- 相同素材、原要求时间和选定时间生成稳定幂等键。
- 保存前再次检查档期，降低检查后被占用的风险。
- 默认最多执行 8 步，超限返回 `incomplete`。
- 输出固定包含 `published: false`，避免把草稿描述成已发布。

更多设计取舍和限制见 [解决方案](docs/02-solution.md) 与 [效果评估](docs/03-evaluation.md)。
