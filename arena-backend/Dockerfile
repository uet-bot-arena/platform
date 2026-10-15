FROM gcc:13.3.0-bookworm@sha256:1d71f0f3450214bef38fe09e6f610fb6cca90cf97b43f4ce845bfc32a4168818
# Bot Python chạy bằng PyPy chính thức (Python 3.11), tải từ downloads.python.org và
# kiểm SHA-256 theo https://pypy.org/checksums.html. CPython 3.11 của Debian chỉ chạy supervisor.
ARG PYPY_RELEASE=pypy3.11-v7.3.23-linux64
ARG PYPY_SHA256=16f9f56e82d1f4ec95a324c1a8cacfd78afc7f0656c0a809a18725ef4391453a
RUN apt-get update && apt-get install -y --no-install-recommends python3 libseccomp2 ca-certificates curl bzip2 \
    && rm -rf /var/lib/apt/lists/* \
    && curl -fsSL -o /tmp/pypy.tar.bz2 "https://downloads.python.org/pypy/${PYPY_RELEASE}.tar.bz2" \
    && echo "${PYPY_SHA256}  /tmp/pypy.tar.bz2" | sha256sum -c - \
    && mkdir -p /opt/pypy && tar -xjf /tmp/pypy.tar.bz2 -C /opt/pypy --strip-components=1 && rm /tmp/pypy.tar.bz2 \
    && ln -s /opt/pypy/bin/pypy3 /usr/bin/pypy3 \
    && (pypy3 -m compileall -q /opt/pypy/lib >/dev/null 2>&1 || true) \
    && pypy3 -c "import sys; assert sys.version_info >= (3, 11)" \
    && chmod -R a+rX /opt/pypy
COPY sdk/json.hpp /opt/pch/json.hpp
RUN printf '#include "json.hpp"\ntemplate class nlohmann::basic_json<>;\n' > /opt/pch/json.cpp \
    && g++ -std=c++17 -O2 -pipe -Wall -Wextra -c /opt/pch/json.cpp -o /opt/pch/json.o \
    && printf '\nextern template class nlohmann::basic_json<>;\n' >> /opt/pch/json.hpp \
    && g++ -std=c++17 -O2 -pipe -Wall -Wextra -x c++-header /opt/pch/json.hpp -o /opt/pch/json.hpp.gch \
    && chmod -R a+rX /opt/pch
COPY arena /opt/arena/arena
ENV PYTHONPATH=/opt/arena PYTHONDONTWRITEBYTECODE=1
WORKDIR /tmp
ENTRYPOINT ["python3", "-u", "-m", "arena.supervisor"]
