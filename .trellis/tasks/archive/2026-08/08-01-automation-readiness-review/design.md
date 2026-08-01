# Design

## Review Artifact

Create `review.md` in this task when it runs. It should contain:

- current implementation evidence;
- checklist results;
- risk table;
- go/no-go decision;
- first platform pilot proposal or hardening backlog.

## Pilot Shape If Go

First pilot should be guided submit:

```text
user selects application record
  -> agent dry-runs platform navigation/fill
  -> agent stops before final submit
  -> user reviews exact page/payload
  -> user confirms
  -> agent executes or user manually submits
  -> timeline records result/unknown
```

## Platform Failure Matrix

| Failure | Required behavior |
| --- | --- |
| Login expired | stop, ask user to log in |
| CAPTCHA | pause, user handles challenge |
| Selector drift | fail with diagnostic screenshot/log reference |
| Rate limit | stop, retry later/manual |
| Duplicate detected | stop, mark duplicate/manual review |
| Upload failure | retry if safe, otherwise manual |
| Unknown final result | mark `unknown`, ask user to reconcile |

## Evidence Sources

- backend tests;
- frontend build/type checks;
- application timeline samples;
- dry-run logs if any exist;
- manual UX screenshots if relevant.

