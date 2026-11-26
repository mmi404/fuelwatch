FROM node:22-alpine AS build
WORKDIR /app
COPY package.json package-lock.json ./
RUN npm ci
COPY . .
# Both values are fixed at build time: the /api rewrite is compiled into the server, and NEXT_PUBLIC_* values are
# compiled into the JavaScript that every browser downloads.
ARG BACKEND_URL=http://backend:8080
# Leave PREFILL_TOKEN empty unless the console is only reachable by people allowed to approve shipments:
# whatever is put here can be read, and used, by anyone who can open the page.
ARG PREFILL_TOKEN=
ENV BACKEND_URL=$BACKEND_URL NEXT_PUBLIC_DEFAULT_TOKEN=$PREFILL_TOKEN NEXT_TELEMETRY_DISABLED=1
RUN npm run build

FROM node:22-alpine
WORKDIR /app
ENV NODE_ENV=production NEXT_TELEMETRY_DISABLED=1 PORT=3000 HOSTNAME=0.0.0.0
COPY --from=build /app/.next/standalone ./
COPY --from=build /app/.next/static ./.next/static
USER node
EXPOSE 3000
HEALTHCHECK --interval=10s --timeout=3s --retries=5 CMD wget -q -O /dev/null http://127.0.0.1:3000/ || exit 1
CMD ["node", "server.js"]
