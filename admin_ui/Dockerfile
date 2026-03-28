FROM node:22-alpine AS build

WORKDIR /app

COPY package.json package-lock.json* ./
RUN npm ci

COPY . .
RUN npm run build

FROM nginx:alpine

COPY nginx.conf /etc/nginx/nginx.conf
COPY --from=build /app/dist /usr/share/nginx/html

RUN rm -f /docker-entrypoint.d/10-listen-on-ipv6-by-default.sh \
        /etc/nginx/conf.d/default.conf \
    && mkdir -p /var/cache/nginx /var/log/nginx /tmp \
    && chown -R nginx:nginx /var/cache/nginx /var/log/nginx /tmp /usr/share/nginx/html \
    && chmod -R 755 /var/cache/nginx /var/log/nginx /tmp

USER nginx

EXPOSE 8080

HEALTHCHECK --interval=10s --timeout=3s --retries=3 --start-period=5s \
    CMD wget -qO- http://127.0.0.1:8080/ || exit 1

CMD ["nginx", "-g", "daemon off;"]
