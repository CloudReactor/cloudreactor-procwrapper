FROM debian:bullseye-slim

LABEL maintainer="jeff@cloudreactor.io"

RUN apt-get update \
  && apt-get upgrade -y \
  && apt-get install -y --no-install-recommends \
  openssl libexpat1 \
  ca-certificates \
  && apt-get clean && rm -rf /var/lib/apt/lists/*

WORKDIR /usr/src/app

# Run as non-root user for better security
RUN groupadd appuser && useradd -g appuser --create-home appuser
USER appuser
WORKDIR /home/appuser

COPY --chown=appuser:appuser entrypoint.sh .
COPY --chown=appuser:appuser common.env .
COPY --chown=appuser:appuser secret.env .
COPY --chown=appuser:appuser proc_wrapper .

ENTRYPOINT ["./entrypoint.sh"]
CMD ["echo", "linux-amd64 standalone executable passed!"]
