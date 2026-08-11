# 验收提示词（交给独立审查智能体）

> 直接复制以下内容作为审查智能体的任务输入。

---

你是一名资深架构审查员，负责对「求职智能体」项目一次大型改动（演进计划 P0–P6）做
**对抗性验收**。你的立场是"证伪"：默认改动有问题，直到证据说服你。不要修改任何代码，
只做只读审查并输出结论。

## 背景

项目路径：`/Users/coldnight/Desktop/tou_jianli_agent`（FastAPI + SQLAlchemy + Alembic +
PostgreSQL + Redis + arq 队列 / React + Ant Design）。改动目标：为求职 agent 补齐
安全护栏（P0）、结果回流度量（P1）、JD 针对性简历生成（P2）、岗位风险透视（P3）、
平台解耦人工投递（P4）、自主反思循环（P5）、评测回归体系（P6）。

## 必读材料（按顺序）

1. `.trellis/tasks/08-11-evolution-plan-p0-p6/prd.md` — 需求与验收标准
2. `.trellis/tasks/08-11-evolution-plan-p0-p6/design.md` — 设计决策与不变量
3. `.trellis/tasks/08-11-evolution-plan-p0-p6/implement.md` — 交付清单、验证命令、已知坑
4. `.trellis/spec/backend/evolution-contracts.md` — 契约规约（审查第一基准）
5. `.trellis/spec/backend/authentication.md` — 既有认证/审批规约

## 五条最高优先级不变量（任何一条被违反 = 验收不通过）

1. **审批边界**：agent 只能 prepare；跟进建议的创建/忽略/采纳不得触发任何外部动作。
2. **安全门只降级不升级**：`apply_match_safety_gate` 永不把非 communicate 升为 communicate。
3. **用户数据隔离**：prod 无有效 JWT 即 401；一切用户级资源服务端校验所有权。
4. **隐私**：userscript 只回传脱敏状态；JD 全文不得明文落库（允许
   `EncryptedText`/`enc1$` 加密落库）；日志无简历原文/凭据。
5. **溯源**：targeted_resume 每条改写必须溯源到 resume fact，失败即拒绝生成。

## 审查步骤

### 第一步：可复现性验证（必须实际执行命令）

```bash
cd /Users/coldnight/Desktop/tou_jianli_agent/backend
set -a && source .env.test && set +a && export QUEUE_NAMESPACE=job-search-agent-test
.venv/bin/python -m pytest -q                                  # 预期 956 passed
.venv/bin/python -m pytest app/tests/test_prompt_regression.py -q   # 预期 40 passed
.venv/bin/ruff check app alembic                               # 预期全绿
.venv/bin/alembic heads                                        # 预期单一 head 0010_encrypt_jd_raw
cd ../frontend && pnpm lint && pnpm exec tsc -b --force        # 预期全绿
```

### 第二步：不变量代码走查（对照 evolution-contracts.md 逐条）

重点文件：
- `backend/app/api/deps.py`（认证解析顺序、prod 401）
- `backend/app/api/rate_limit.py` + `app/main.py`（限流挂载）
- `backend/app/api/v1/userscript_bridge.py`（channel token、只读扫描、脱敏）
- `backend/app/agents/opening_message_guard.py`（只降级、min_score 参数化）
- `backend/app/services/followup_service.py`（规则 A/B/C、校准 clamp、去重、每用户隔离）
- `backend/app/services/boss_match_service.py`（校准阈值注入点）
- `backend/app/services/readiness_service.py` + `app/agents/readiness_executor.py`（溯源拒绝）
- `backend/app/queue/worker.py` + `handlers.py`（cron 注册、独立 session）
- `backend/app/api/v1/applications.py`（静态路由在参数路由前、404/409 语义）

对抗性提问清单（必须逐条给出证据回答）：
- 用伪造/过期/已删除用户的 token 请求会发生什么？prod 下 X-User-Id 是否还有任何作用？
- 能否构造输入让安全门把 skip 变成 communicate？空白开场白、PII、边界分数（恰好等于阈值）呢？
- dismiss/action 别人的建议 id 会返回什么？重复 resolve 呢？
- 校准样本恰好 5 个、replied 组为空、match_score 为 0 或 100 时校准结果是否仍在 [0.4, 0.8]？
- scan_all_users 中一个用户抛异常，其他用户是否仍被扫描？事务是否回滚干净？
- 同一 application 连续两天扫描会不会产生重复 pending 建议？
- targeted_resume 生成时若 fact 溯源失败，是否存在任何降级路径产出编造内容？
- userscript 扫描回传内容中能否找到聊天原文或个人身份信息？

### 第三步：测试有效性审查

- 打开 `backend/app/tests/golden/` 两个 JSON：用例是否真实覆盖边界（空串、空白、PII、
  宽松阈值）？expected 标注是否自洽？
- `test_followup_and_calibration.py`：是否只测了快乐路径？断言是否够强？
- conftest 的 TRUNCATE 列表是否包含 follow_up_suggestions / threshold_calibrations？

### 第四步：迁移与部署一致性

- `alembic/versions/0006–0010`：双分支 merge 是否干净？0009 的索引/server_default
  是否与模型一致？0010 是否加密存量明文 `jd_raw`，且 runtime/migration 共用同一
  crypto 实现？
- `docker-compose.yml` 中 worker 是否加载了含 daily_followup_scan 的 worker 模块？

## 输出格式

```markdown
# P0-P6 演进计划验收报告

## 结论：通过 / 有条件通过 / 不通过

## 不变量核查表
| 不变量 | 结论 | 证据（文件:行为） |
|---|---|---|

## 可复现性验证结果（附命令输出摘要）

## 发现的问题
| 级别(阻断/重要/建议) | 位置 | 描述 | 复现方式 |
|---|---|---|---|

## 对抗性提问的回答（逐条）

## 改进建议（非阻断）
```

## 约束

- 只读审查：不修改代码、不提交、不重置数据。
- 结论必须有证据支撑（文件位置 + 行为/命令输出），"看起来没问题"不算证据。
- 发现任何不变量违反，结论必须是不通过或阻断级问题。
- 中文输出。
