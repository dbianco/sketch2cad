# syntax=docker/dockerfile:1

# Build stage: install dependencies from the lockfile and produce dist/.
FROM node:24.22.1-alpine AS build

ARG PNPM_VERSION=11.20.0
RUN npm install -g pnpm@${PNPM_VERSION}

WORKDIR /app

# Copy only the manifests first so the dependency layer is cached.
COPY package.json pnpm-lock.yaml ./
RUN pnpm install --frozen-lockfile

COPY . .
RUN pnpm build

# Runtime stage: nginx serving the static bundle as the unprivileged user.
FROM nginx:1.30.6-alpine

RUN rm -f /etc/nginx/conf.d/default.conf
COPY nginx.conf /etc/nginx/nginx.conf
COPY --from=build /app/dist /usr/share/nginx/html

USER nginx

EXPOSE 80

HEALTHCHECK --interval=10s --timeout=3s --start-period=5s --retries=3 \
  CMD wget -q --spider http://127.0.0.1/ || exit 1
