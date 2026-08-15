FROM node:22-alpine AS builder
WORKDIR /app
# VITE_DEMO_PASSWORD is a deliberately public demo credential (see design.md §2).
# It is baked into the static bundle at build time so the "one-click demo login"
# button works without exposing any real secrets.
ARG VITE_DEMO_PASSWORD
ENV VITE_DEMO_PASSWORD=$VITE_DEMO_PASSWORD
COPY package.json pnpm-lock.yaml pnpm-workspace.yaml ./
RUN corepack enable && corepack prepare pnpm@latest --activate && pnpm install --frozen-lockfile
COPY . .
RUN pnpm build

FROM nginx:alpine AS runtime
COPY --from=builder /app/dist /usr/share/nginx/html
COPY nginx.conf /etc/nginx/conf.d/default.conf
EXPOSE 80
CMD ["nginx", "-g", "daemon off;"]
