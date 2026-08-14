FROM mcr.microsoft.com/playwright:v1.61.1-noble
WORKDIR /e2e
RUN npm init -y >/dev/null && npm install @playwright/test@1.61.1
COPY playwright.config.ts ./
COPY e2e ./e2e
CMD ["npx", "playwright", "test"]
