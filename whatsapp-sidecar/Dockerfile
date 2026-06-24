# WhatsApp sidecar image. Built + run by the control-plane box compose project
# (control-plane/docker-compose.box.yml) so it deploys via the same GitOps pipeline.
# Two-stage: build with dev deps (tsc), ship only prod deps + compiled dist.
FROM node:20-bookworm-slim AS build
WORKDIR /app
COPY package.json package-lock.json ./
RUN npm ci --no-audit --no-fund
COPY tsconfig.json ./
COPY src ./src
RUN npm run build

FROM node:20-bookworm-slim
WORKDIR /app
ENV NODE_ENV=production
COPY package.json package-lock.json ./
RUN npm ci --omit=dev --no-audit --no-fund && npm cache clean --force
COPY --from=build /app/dist ./dist
# store/ (Baileys session) and accounts.json are bind-mounted at runtime from a
# durable host path — never baked into the image (they are state/secrets).
CMD ["node", "dist/index.js"]
