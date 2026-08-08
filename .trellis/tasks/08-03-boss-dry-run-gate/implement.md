# 10 次 dry-run 门槛验证 Execution Plan

## Checklist

- [ ] 确认所有前置任务已归档：pilot panel、observability、devex、e2e smoke。
- [ ] 正常本地环境重启：
  `docker compose up -d --build`。
- [ ] 确认 backend health、frontend、worker 均可用。
- [ ] 确认最新 userscript 已安装并在 BOSS 页面发 heartbeat。
- [ ] 每次 dry-run 前记录目标职位 hash 和 page_id。
- [ ] 使用前端半自动面板执行 inspect → match → prepare。
- [ ] 人工审阅后才 approve + execute。
- [ ] 每次执行后人工核对 BOSS 页面，并写入 `check.jsonl`。
- [ ] 至少完成 10 次连续无事故记录，其中至少 2 次 duplicate。
- [ ] 若出现事故，停止累计，创建修复任务，计数归零。

## Validation Commands

```bash
docker compose up -d --build
curl -sS http://localhost:8000/api/v1/health
curl -sS http://localhost:8000/api/v1/userscript-bridge/status
./scripts/e2e-smoke.sh --skip-up
```

每轮后查看日志：

```bash
docker compose logs --tail=200 backend
docker compose logs --tail=200 worker
```

## Review Gates

- 无 approve/execute 人工动作时不得真实发送。
- wrong-tab 一次即失败。
- unknown 必须人工对账；若页面状态明确但系统 unknown，计为事故。
- 通过后只说明“允许进入推荐列表 auto-execute 设计/启用评审”，不是默认打开批量发送。

## Handoff

这个任务暂不适合编码智能体开工。它适合在其它修复完成后由 Codex 或人工操作者执行真实页面验证。
