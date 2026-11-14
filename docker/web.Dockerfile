# syntax=docker/dockerfile:1
FROM node:22-bookworm-slim AS dashboard-tests
WORKDIR /app
COPY package.json package-lock.json ./
RUN --mount=type=cache,target=/root/.npm npm ci --no-audit --no-fund
COPY apps/participant/ apps/participant/
COPY apps/analytics/ apps/analytics/
COPY tests/ tests/
USER node
CMD ["npm","test"]

FROM nginx:stable-alpine AS web-base
COPY docker/nginx.conf /etc/nginx/nginx.conf
USER nginx
EXPOSE 8080
HEALTHCHECK --interval=5s --timeout=3s --start-period=5s --retries=5 CMD ["wget","-q","-O","/dev/null","http://127.0.0.1:8080/"]
ENTRYPOINT ["nginx"]
CMD ["-g","daemon off;"]

FROM web-base AS participant
COPY apps/participant/ /usr/share/nginx/html/

FROM web-base AS analytics
COPY apps/analytics/ /usr/share/nginx/html/
