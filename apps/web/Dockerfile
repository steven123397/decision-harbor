FROM node:24-bookworm-slim AS deps

WORKDIR /app
COPY apps/web/package.json apps/web/package-lock.json ./
RUN npm ci

FROM deps AS build
COPY apps/web/ ./
RUN npm run build

FROM mcr.microsoft.com/playwright:v1.61.1-noble AS test

WORKDIR /app
COPY apps/web/package.json apps/web/package-lock.json ./
RUN npm ci
COPY apps/web/ ./

FROM node:24-bookworm-slim AS runtime

ENV NODE_ENV=production
WORKDIR /app
COPY --from=deps /app/node_modules ./node_modules
COPY apps/web/package.json apps/web/vite.config.ts ./
COPY --from=build /app/dist ./dist

EXPOSE 4173
CMD ["npm", "run", "preview"]
