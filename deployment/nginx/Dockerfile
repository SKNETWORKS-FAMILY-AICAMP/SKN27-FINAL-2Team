FROM nginxinc/nginx-unprivileged:1.29.5-alpine@sha256:42a7d7f2ee23e9f5a1dcdf3647ba5c585bbd18f79e79cd817e70e8cd61c55779

USER root

RUN apk upgrade --no-cache

COPY --chown=101:101 nginx.conf.template /etc/nginx/nginx.conf.template
COPY --chown=101:101 --chmod=0555 entrypoint.sh /usr/local/bin/himate-nginx-entrypoint

USER 101:101

EXPOSE 8080 8443

HEALTHCHECK --interval=15s --timeout=5s --start-period=15s --retries=4 \
    CMD wget -q -O /dev/null http://127.0.0.1:8080/health/live/ || exit 1

ENTRYPOINT ["/usr/local/bin/himate-nginx-entrypoint"]
