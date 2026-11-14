# syntax=docker/dockerfile:1
FROM node:22-bookworm-slim AS build
WORKDIR /app
COPY apps/frontend/package.json apps/frontend/package-lock.json ./
RUN --mount=type=cache,target=/root/.npm npm ci --no-audit --no-fund
COPY apps/frontend/ ./
RUN npm run build

# Keep the React scaffold build available while the dashboard is migrated.
FROM nginx:stable-alpine AS react-frontend
COPY docker/nginx.conf /etc/nginx/nginx.conf
COPY --from=build /app/dist /usr/share/nginx/html
USER nginx
EXPOSE 8080
HEALTHCHECK --interval=10s --timeout=3s --start-period=5s --retries=5 \
    CMD ["wget", "-q", "-O", "/dev/null", "http://127.0.0.1:8080/"]
ENTRYPOINT ["nginx"]
CMD ["-g", "daemon off;"]

FROM node:22-bookworm-slim AS dashboard-tests
WORKDIR /app
COPY package.json package-lock.json ./
RUN --mount=type=cache,target=/root/.npm npm ci --no-audit --no-fund
COPY index.html app.js styles.css ./
COPY tests/dashboard.test.cjs tests/dashboard.test.cjs
USER node
CMD ["npm", "test"]

# The shipped dashboard needs only static assets and nginx.
FROM nginx:stable-alpine AS frontend
COPY docker/nginx.conf /etc/nginx/nginx.conf
COPY index.html app.js styles.css /usr/share/nginx/html/
USER nginx
EXPOSE 8080
HEALTHCHECK --interval=10s --timeout=3s --start-period=5s --retries=5 \
    CMD ["wget", "-q", "-O", "/dev/null", "http://127.0.0.1:8080/"]
ENTRYPOINT ["nginx"]
CMD ["-g", "daemon off;"]
