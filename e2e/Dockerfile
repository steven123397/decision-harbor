FROM mcr.microsoft.com/playwright:v1.61.1-noble

WORKDIR /e2e
COPY package.json ./
RUN npm install --no-audit --no-fund
COPY . .

CMD ["npx", "playwright", "test"]
