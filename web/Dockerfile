FROM node:24-slim

# 依赖源可按网络环境覆盖（见 .env.example 的 NPM_CONFIG_REGISTRY）
ARG NPM_CONFIG_REGISTRY=https://registry.npmjs.org
ENV NPM_CONFIG_REGISTRY=${NPM_CONFIG_REGISTRY}

WORKDIR /app

# npm ci 严格按 package-lock.json 安装：lockfile 与 package.json 不一致即失败
COPY package.json package-lock.json ./
RUN npm ci

COPY . .

EXPOSE 5173
CMD ["npm", "run", "dev", "--", "--host", "0.0.0.0"]
