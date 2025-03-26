FROM public.ecr.aws/amazonlinux/amazonlinux:2023.6.20250303.0

LABEL maintainer="jeff@cloudreactor.io"

RUN dnf install -y shadow-utils openssl ca-certificates wget

WORKDIR /usr/src/app

# Run as non-root user for better security
RUN groupadd appuser && useradd -g appuser --create-home appuser
USER appuser
WORKDIR /home/appuser

COPY --chown=appuser:appuser common.env .
COPY --chown=appuser:appuser secret.env .
COPY --chown=appuser:appuser proc_wrapper.bin .

ENTRYPOINT ["./proc_wrapper.bin", "-e", "common.env", "-e", "secret.env" ]
