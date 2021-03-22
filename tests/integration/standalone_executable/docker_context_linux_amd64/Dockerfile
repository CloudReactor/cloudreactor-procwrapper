FROM debian:buster-slim

LABEL maintainer="jeff@cloudreactor.io"

RUN apt-get update \
  && apt-get install -y --no-install-recommends \
  openssl=1.1.1d-0+deb10u5 libexpat1=2.2.6-2+deb10u1 \
  ca-certificates=20200601~deb10u2 \
  && apt-get clean && rm -rf /var/lib/apt/lists/*

WORKDIR /usr/src/app

# Run as non-root user for better security
RUN groupadd appuser && useradd -g appuser --create-home appuser
USER appuser
WORKDIR /home/appuser

COPY --chown=appuser:appuser entrypoint.sh .
COPY --chown=appuser:appuser proc_wrapper .
COPY --chown=appuser:appuser common_env.sh .
COPY --chown=appuser:appuser secret_env.sh .


ENTRYPOINT ["./entrypoint.sh"]
CMD ["echo", "linux-amd64 standalone executable passed!"]
