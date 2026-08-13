FROM node:24-bookworm-slim

WORKDIR /app

COPY web/package.json web/package-lock.json /app/
RUN npm ci

COPY web /app
RUN npm run build

ENV API_UPSTREAM=http://api:8000
EXPOSE 80

CMD ["npm", "run", "preview"]
