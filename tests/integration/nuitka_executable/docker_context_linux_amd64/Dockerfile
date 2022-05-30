FROM debian:bullseye-slim

LABEL maintainer="jeff@cloudreactor.io"

# Otherwise we get messages like:
#
# debconf: unable to initialize frontend: Dialog
#
# during apt-get.
#
# See https://github.com/phusion/baseimage-docker/issues/58
ENV DEBIAN_FRONTEND noninteractive

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

COPY --chown=appuser:appuser common.env .
COPY --chown=appuser:appuser secret.env .
COPY --chown=appuser:appuser proc_wrapper.bin .

# Use the standalone executable for x64/AMD64 Linux.
# Unfortunately, it is an AppImage that needs to be extracted to
# run in a container, since FUSE inside a container is problematic.
RUN ./proc_wrapper.bin --appimage-extract

ENTRYPOINT ["squashfs-root/proc_wrapper.bin", \
  "-e", "common.env", "-e", "secret.env" ]
