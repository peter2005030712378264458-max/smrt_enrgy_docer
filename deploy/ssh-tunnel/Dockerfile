FROM alpine:3.21
RUN apk add --no-cache openssh-client sshpass postgresql-client
COPY entrypoint.sh /usr/local/bin/tunnel
ENTRYPOINT ["sh", "/usr/local/bin/tunnel"]
