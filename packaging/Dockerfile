# Build from the repository root:  docker build -f packaging/Dockerfile -t wigix .
FROM python:3.12-slim
RUN apt-get update \
 && apt-get install -y --no-install-recommends libusb-1.0-0 fonts-dejavu-core \
 && rm -rf /var/lib/apt/lists/*
WORKDIR /src
COPY pyproject.toml README.md LICENSE ./
COPY src ./src
RUN pip install --no-cache-dir .
# Run as an unprivileged user. Access to the USB node comes from a supplementary group (compose: group_add).
RUN useradd --system --uid 10001 --create-home --home-dir /home/wigix wigix \
 && mkdir /config && chown wigix /config
USER wigix
VOLUME /config
EXPOSE 8765
ENTRYPOINT ["wigix"]
CMD ["run", "-c", "/config/dashboard.yaml"]
