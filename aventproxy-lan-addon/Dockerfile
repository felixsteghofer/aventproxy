FROM rust:1.90.0-bookworm AS builder
RUN apt-get update && apt-get install -y --no-install-recommends curl patch && rm -rf /var/lib/apt/lists/*
WORKDIR /src
COPY live-probe.patch /src/live-probe.patch
RUN curl -fLsS https://github.com/eisbaw/babymonitor-client/archive/61439ccdb7328ddd7d8183ed981d073d777988e8.tar.gz -o source.tgz \
    && echo 'ce4bf4e914ace292580e7ea983a1ad4d98b7a991ae79f28c73653ebea9487035  source.tgz' | sha256sum -c - \
    && tar -xzf source.tgz --strip-components=1 \
    && patch -p1 < live-probe.patch
RUN cd babymonitor && CARGO_BUILD_JOBS=2 cargo build --locked --release -p babymonitor-cli --features live
ARG TARGETARCH
RUN case "$TARGETARCH" in \
      arm64) GOARCH=arm64; SHA=359fabade8a7a51e81a55fe6df6b0ef81764a5e1d63179577534eaaa71904b50 ;; \
      amd64) GOARCH=amd64; SHA=32d616af226bd731678ffde328b94cfb94e30339bfefc469cfb76323144615a6 ;; \
      *) exit 1 ;; \
    esac \
    && curl -fLsS "https://github.com/AlexxIT/go2rtc/releases/download/v1.9.14/go2rtc_linux_$GOARCH" -o /src/go2rtc \
    && echo "$SHA  /src/go2rtc" | sha256sum -c - \
    && chmod 755 /src/go2rtc \
    && curl -fLsS https://raw.githubusercontent.com/AlexxIT/go2rtc/v1.9.14/LICENSE -o /src/LICENSE-go2rtc

FROM debian:bookworm-slim
ARG BUILD_VERSION=0.1.0
ARG BUILD_ARCH
LABEL io.hass.type="app" io.hass.version="${BUILD_VERSION}" io.hass.arch="${BUILD_ARCH}"
RUN apt-get update && apt-get install -y --no-install-recommends ffmpeg python3 tini ca-certificates \
    && rm -rf /var/lib/apt/lists/* \
    && find / -xdev -type f \( -perm -4000 -o -perm -2000 \) -exec chmod a-s {} +
COPY --from=builder /src/babymonitor/target/release/babymonitor-cli /src/go2rtc /usr/local/bin/
COPY --from=builder /src/LICENSE /src/NOTICES.txt /src/LICENSE-go2rtc /usr/share/licenses/avent-lan-bridge/
COPY producer.py run.py /opt/avent/
ENTRYPOINT ["/usr/bin/tini", "--", "/usr/bin/python3", "/opt/avent/run.py"]
