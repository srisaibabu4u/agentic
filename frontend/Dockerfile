# Build the UI, then serve the static files with nginx, which also forwards /api to the API container.
#   docker build -t kyc-ui frontend
FROM node:24-alpine AS build
WORKDIR /app
COPY package.json package-lock.json ./
RUN npm ci --no-audit --no-fund
COPY . .
RUN npm run build

FROM nginx:1.27-alpine
COPY nginx.conf /etc/nginx/conf.d/default.conf
COPY nginx-security.conf /etc/nginx/snippets/security.conf
COPY --from=build /app/dist /usr/share/nginx/html
EXPOSE 8080
