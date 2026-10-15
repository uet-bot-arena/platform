FROM gcc:13.3.0-bookworm@sha256:1d71f0f3450214bef38fe09e6f610fb6cca90cf97b43f4ce845bfc32a4168818
RUN apt-get update && apt-get install -y --no-install-recommends python3 libseccomp2 ca-certificates && rm -rf /var/lib/apt/lists/*
COPY arena /opt/arena/arena
ENV PYTHONPATH=/opt/arena PYTHONDONTWRITEBYTECODE=1
WORKDIR /tmp
ENTRYPOINT ["python3", "-u", "-m", "arena.supervisor"]
