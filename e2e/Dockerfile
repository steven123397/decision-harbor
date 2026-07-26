FROM mcr.microsoft.com/playwright:v1.49.0-noble

# 依赖源可按网络环境覆盖（见 .env.example 的 NPM_CONFIG_REGISTRY）
ARG NPM_CONFIG_REGISTRY=https://registry.npmjs.org
ENV NPM_CONFIG_REGISTRY=${NPM_CONFIG_REGISTRY}

WORKDIR /e2e

# npm ci 严格按 package-lock.json 安装：lockfile 与 package.json 不一致即失败
COPY package.json package-lock.json ./
RUN npm ci

COPY . .

CMD ["npx", "playwright", "test"]
