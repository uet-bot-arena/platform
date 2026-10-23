#!/usr/bin/env bash
# Build the Linux grader's read-only toolchain without root, using a pinned Ubuntu snapshot.
set -euo pipefail
repo="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd -P)"
out="${1:-$repo/build/rootfs}"
snapshot=20261022T000000Z
mirror="https://snapshot.ubuntu.com/ubuntu/$snapshot"
gcc_package='13.3.0-6ubuntu2~24.04.1'
pypy=pypy3.11-v7.3.23-linux64
pypy_sha=16f9f56e82d1f4ec95a324c1a8cacfd78afc7f0656c0a809a18725ef4391453a
for tool in mmdebstrap newuidmap newgidmap bwrap zstd curl tar bzip2; do
  command -v "$tool" >/dev/null || { echo "Missing $tool; install mmdebstrap uidmap bubblewrap zstd curl bzip2." >&2; exit 1; }
done
[[ "$(uname -m)" == x86_64 ]] || { echo 'Only x86_64 is pinned.' >&2; exit 1; }
mkdir -p "$out"
out="$(realpath "$out")"
work="$(mktemp -d -t arena-rootfs.XXXXXXXX)"
trap 'rm -rf -- "$work"' EXIT
root="$work/root"
mmdebstrap --mode=unshare --variant=minbase --format=tar --skip=output/mknod \
  --architectures=amd64 --keyring=/usr/share/keyrings/ubuntu-archive-keyring.gpg \
  --aptopt='Acquire::Check-Valid-Until "false"' \
  --include="gcc-13=$gcc_package,g++-13=$gcc_package,python3,libseccomp2" \
  --dpkgopt='path-exclude=/usr/share/doc/*' --dpkgopt='path-exclude=/usr/share/man/*' \
  --dpkgopt='path-exclude=/usr/share/locale/*' \
  noble "$work/base.tar" \
  "deb $mirror noble main" "deb $mirror noble-updates main" "deb $mirror noble-security main"
mkdir "$root"
tar -xf "$work/base.tar" -C "$root" --no-same-owner --exclude='./dev/*'
rm -- "$work/base.tar"
curl --fail --location --retry 3 --output "$work/pypy.tar.bz2" "https://downloads.python.org/pypy/$pypy.tar.bz2"
printf '%s  %s\n' "$pypy_sha" "$work/pypy.tar.bz2" | sha256sum --check --status
mkdir -p "$root/opt/pypy" "$root/opt/pch" "$root"/{proc,dev,tmp,sys/fs/cgroup,artifact,source,out,sdk,opt/arena/arena}
tar -xjf "$work/pypy.tar.bz2" -C "$root/opt/pypy" --strip-components=1
ln -s gcc-13 "$root/usr/bin/gcc"
ln -s g++-13 "$root/usr/bin/g++"
ln -s cpp-13 "$root/usr/bin/cpp"
ln -s /opt/pypy/bin/pypy3 "$root/usr/bin/pypy3"
cp "$repo/arena-backend/sdk/json.hpp" "$root/opt/pch/json.hpp"
printf '#include "json.hpp"\ntemplate class nlohmann::basic_json<>;\n' > "$root/opt/pch/json.cpp"
inside() {
  bwrap --ro-bind "$root" / --proc /proc --dev /dev --tmpfs /tmp \
    --bind "$root/opt/pch" /opt/pch --unshare-all --clearenv --setenv PATH /usr/bin:/bin --setenv LANG C.UTF-8 "$@"
}
[[ "$(inside g++ -dumpfullversion)" == 13.3.0 ]] || { echo 'Unexpected GCC version.' >&2; exit 1; }
inside g++ -std=c++17 -O2 -pipe -Wall -Wextra -c /opt/pch/json.cpp -o /opt/pch/json.o
printf '\nextern template class nlohmann::basic_json<>;\n' >> "$root/opt/pch/json.hpp"
inside g++ -std=c++17 -O2 -pipe -Wall -Wextra -x c++-header /opt/pch/json.hpp -o /opt/pch/json.hpp.gch
inside python3 -c 'import ast,ctypes,hashlib,json,resource,zipfile; ctypes.CDLL("libseccomp.so.2")'
inside pypy3 -c 'import sys; assert sys.version_info[:2] == (3, 11)'
inside dpkg-query -W '-f=${Package}\t${Version}\n' > "$root/packages.tsv"
compiler="$(inside g++ --version | head -1)"
printf '{"ubuntu":"24.04","snapshot":"%s","gcc":"%s","pypy":"%s","pypy_sha256":"%s"}\n' \
  "$snapshot" "$compiler" "$pypy" "$pypy_sha" > "$root/rootfs-manifest.json"
before="$(du -sm "$root" | cut -f1)"
# Keep only the C/C++/Python runtime and compiler paths; no package manager caches or debug data.
rm -rf -- "$root"/usr/share/{doc,man,info,locale,lintian} "$root"/var/lib/apt/lists/* \
  "$root"/var/cache/* "$root"/var/log/* "$root"/usr/share/perl* "$root"/opt/pypy/lib/pypy3.11/test \
  "$root"/usr/lib/gcc/x86_64-linux-gnu/13/{cc1obj,cc1objplus,f951,lto1}
find "$root" -name '__pycache__' -type d -prune -exec rm -rf -- {} +
find "$root/opt/pypy" -name '*.debug' -delete
rm -f -- "$root"/usr/lib/x86_64-linux-gnu/{libasan*,libtsan*,liblsan*,libubsan*,libhwasan*}
missing="$(inside sh -c '
  for exe in /usr/bin/gcc-13 /usr/bin/g++-13 /usr/bin/cpp-13 /usr/libexec/gcc/x86_64-linux-gnu/13/cc1 \
    /usr/libexec/gcc/x86_64-linux-gnu/13/cc1plus /usr/libexec/gcc/x86_64-linux-gnu/13/collect2 \
    /usr/bin/as /usr/bin/ld.bfd /usr/bin/python3 /opt/pypy/bin/pypy3.11 \
    /opt/pypy/bin/libpypy3.11-c.so /usr/lib/x86_64-linux-gnu/libseccomp.so.2; do
    test -e "$exe" || { echo "missing executable: $exe"; continue; }
    ldd "$exe" 2>&1 | grep "not found" | sed "s|^|$exe: |" || true
  done')"
[[ -z "$missing" ]] || { printf 'Missing dependencies:\n%s\n' "$missing" >&2; exit 4; }
chmod -R a+rX "$root"
after="$(du -sm "$root" | cut -f1)"
tar --sort=name --mtime='2026-01-01 00:00Z' --owner=0 --group=0 --numeric-owner -C "$root" -cf - . \
  | zstd -q -19 -T2 -o "$work/rootfs.tar.zst"
sha="$(sha256sum "$work/rootfs.tar.zst" | cut -d' ' -f1)"
name="rootfs-$sha.tar.zst"
mv -- "$work/rootfs.tar.zst" "$out/$name"
(cd "$out" && sha256sum "$name" > "$name.sha256")
cp "$root/rootfs-manifest.json" "$out/$name.manifest.json"
size="$(stat -c %s "$out/$name")"
printf 'rootfs: %s/%s\ncompiler: %s\nsize: %s bytes; unpacked %s -> %s MiB\n' "$out" "$name" "$compiler" "$size" "$before" "$after"
if (( size > 300000000 )); then echo '[HOI] Archive exceeds 300 MB; stop for owner review.' >&2; exit 3; fi
