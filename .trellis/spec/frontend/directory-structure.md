# Frontend Directory Structure

The frontend source tree does not exist yet. Use this structure when creating
the React application unless later code establishes a different local pattern.

```text
frontend/
  src/
    app/
      routes/
      providers/
    pages/
      dashboard/
      resumes/
      jobs/
      applications/
      interview-prep/
    features/
      profile/
      resumes/
      jobs/
      applications/
      agent-runs/
    components/
      common/
      layout/
    api/
      client.ts
      types/
    hooks/
    styles/
    test/
```

## Placement Rules

- Put route-level screens under `pages/` or the chosen router's route folder.
- Put domain UI under `features/{domain}/`.
- Put reusable shell, empty states, loading states, and feedback components under
  `components/`.
- Put API client code and generated types under `api/`.
- Put hooks near the domain that owns them unless they are truly shared.

Avoid scattering job, resume, application, and agent-run logic across generic
utility folders.

