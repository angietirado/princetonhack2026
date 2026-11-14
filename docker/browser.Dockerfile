FROM mcr.microsoft.com/playwright:v1.65.1-noble
WORKDIR /app
COPY tests/browser/package.json tests/browser/package-lock.json ./
RUN npm ci --ignore-scripts --no-audit --no-fund
COPY playwright.config.cjs ./
COPY tests/browser/ tests/browser/
CMD ["npx", "--no-install", "playwright", "test", "--config", "playwright.config.cjs"]