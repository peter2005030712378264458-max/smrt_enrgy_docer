FROM nginx:1.27-alpine

ARG NGINX_CONFIG=nginx.conf
COPY ${NGINX_CONFIG} /etc/nginx/templates/default.conf.template
ADD frontend-dist.tar.gz /usr/share/nginx/html/

ENV API_PROXY_TARGET=http://127.0.0.1:5000

EXPOSE 80 443
