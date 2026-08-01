# --- Build stage ---
FROM node:20-slim AS builder
WORKDIR /build
COPY package*.json ./
RUN npm ci
COPY . .
RUN npm run build

# --- Runtime stage ---
FROM node:20-slim AS runtime
WORKDIR /app
RUN npm install -g serve
COPY --from=builder /build/dist ./dist
EXPOSE 4173
CMD ["serve", "-s", "dist", "-l", "4173"]
