#!/usr/bin/env bash
# Dựng rootfs chỉ đọc cho backend bwrap từ đúng Dockerfile của worker Docker
# (GCC 13.3, PyPy 3.11 v7.3.23, libseccomp, PCH json.hpp). Chạy một lần trên máy có Docker.
# Kết quả: <out>/rootfs-<sha>.tar.zst và <out>/rootfs-<sha>.tar.zst.sha256
# Máy chấm Linux chỉ cần file này (không cần Docker); xem docs/SANDBOX.md.
set -euo pipefail
repo="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd -P)"
out="${1:-$repo/build/rootfs}"
command -v docker >/dev/null || { echo "Cần Docker để dựng rootfs" >&2; exit 1; }
command -v zstd >/dev/null || { echo "Cần zstd (sudo apt-get install zstd)" >&2; exit 1; }
mkdir -p "$out"
work="$(mktemp -d)"
cid=""
cleanup() { [[ -n "$cid" ]] && docker rm -f "$cid" >/dev/null 2>&1; rm -rf "$work"; }
trap cleanup EXIT

docker build -q -t uet-arena-rootfs:build "$repo/arena-backend" >/dev/null
cid="$(docker create uet-arena-rootfs:build)"
mkdir "$work/root"
# Device nodes cannot be created without root; bwrap mounts its own /dev and /proc.
docker export "$cid" | tar -x -C "$work/root" --exclude='./dev/*' --exclude='dev/*' --no-same-owner 2>/dev/null || true
root="$work/root"
before="$(du -sm "$root" | cut -f1)"

# Supervisor code comes from the grader install (bind-mounted read-only), not the rootfs.
rm -rf "$root/opt/arena"
# Not needed to compile or run bots: documentation, locales, package caches, VCS tools.
rm -rf "$root"/usr/share/{doc,man,info,locale,lintian,bash-completion,gtk-doc,help} \
       "$root"/usr/local/share/{doc,man,info,locale} \
       "$root"/var/lib/apt/lists/* "$root"/var/cache/* "$root"/var/log/* \
       "$root"/usr/lib/git-core "$root"/usr/share/git-core "$root"/usr/bin/{git,hg,svn,svnadmin,svnserve} \
       "$root"/usr/lib/python3/dist-packages/mercurial "$root"/root/.cache
find "$root" -name '__pycache__' -prune -exec rm -rf {} + 2>/dev/null || true
# Only C (c11), C++ (cpp17) and PyPy are offered, with fixed flags (no -flto, no sanitizers):
# drop the Go and Fortran front ends, LTO, sanitizers, Debian's unused GCC 12 and debug symbols.
gccexec="$root/usr/local/libexec/gcc/x86_64-linux-gnu/13.3.0"
rm -f "$gccexec"/{go1,cgo,f951,buildid,test2json,vet,lto1} \
      "$root"/usr/local/bin/{gccgo,go,gofmt,gfortran,x86_64-linux-gnu-gccgo,x86_64-linux-gnu-gfortran,lto-dump} \
      "$root"/usr/local/lib64/{libgo*,libgfortran*,libasan*,libtsan*,liblsan*,libubsan*,libhwasan*} \
      "$root"/usr/lib/x86_64-linux-gnu/{libasan*,libtsan*,liblsan*,libubsan*,libhwasan*} \
      "$root"/opt/pypy/bin/*.debug
rm -rf "$root"/usr/local/lib/go "$root"/usr/local/lib64/go "$root"/usr/lib/gcc/x86_64-linux-gnu/12 \
       "$root"/usr/libexec/gcc/x86_64-linux-gnu/12 "$root"/usr/share/perl "$root"/usr/lib/x86_64-linux-gnu/perl
find "$root/usr/bin" -name '*-12' -delete
# Static archives of unrelated -dev packages (keep glibc's, needed for C/C++ links).
find "$root/usr/lib/x86_64-linux-gnu" -maxdepth 1 -name '*.a' \
  ! -name 'libc.a' ! -name 'libc_nonshared.a' ! -name 'libm.a' ! -name 'libm-*.a' ! -name 'libmvec.a' \
  ! -name 'libpthread.a' ! -name 'libdl.a' ! -name 'librt.a' ! -name 'libutil.a' ! -name 'libanl.a' \
  ! -name 'libresolv.a' ! -name 'libcrypt.a' ! -name 'libg.a' ! -name 'libmcheck.a' ! -name 'libBrokenLocale.a' \
  ! -name 'libseccomp.a' -delete
# Large media/GUI libraries pulled in by buildpack-deps; nothing we run links them.
rm -f "$root"/usr/lib/x86_64-linux-gnu/{libicu*,libx265*,librsvg*,libMagick*,libaom*,libOpenEXR*,libdjvulibre*,libabigail*,libdav1d*,libde265*,libheif*,libvpx*,libwebp*,libgdk*,libgtk*,libcairo*,libpango*,libharfbuzz*,libfreetype*,libtiff*,libjxl*,libgs*,liblcms*}
rm -f "$root"/usr/bin/sq "$root"/usr/lib/file/magic.mgc
# Every program the supervisor or compilers start must still resolve all shared libraries.
# ldd runs inside the rootfs itself (bwrap), so the host's glibc is never mixed in.
gccexec_in=/usr/local/libexec/gcc/x86_64-linux-gnu/13.3.0
missing="$(bwrap --ro-bind "$root" / --proc /proc --dev /dev --unshare-all /bin/sh -c '
  for exe in /usr/local/bin/gcc /usr/local/bin/g++ /usr/local/bin/cpp '"$gccexec_in"'/cc1 '"$gccexec_in"'/cc1plus              '"$gccexec_in"'/collect2 '"$gccexec_in"'/lto-wrapper '"$gccexec_in"'/liblto_plugin.so /usr/bin/as /usr/bin/ld.bfd              /usr/bin/python3.11 /opt/pypy/bin/pypy3.11 /opt/pypy/bin/libpypy3.11-c.so /usr/lib/x86_64-linux-gnu/libseccomp.so.2; do
    ldd "$exe" 2>&1 | grep "not found" | sed "s|^|$exe: |"
  done; true')"
if [[ -n "$missing" ]]; then echo "Thiếu thư viện sau khi cắt:" >&2; echo "$missing" >&2; exit 4; fi
mkdir -p "$root"/{proc,dev,tmp,sys/fs/cgroup,artifact,source,out,sdk,opt/arena/arena}
after="$(du -sm "$root" | cut -f1)"

tarball="$work/rootfs.tar.zst"
tar --sort=name --mtime='2026-01-01 00:00Z' --owner=0 --group=0 --numeric-owner -C "$root" -cf - . \
  | zstd -q -19 -T0 -o "$tarball"
sha="$(sha256sum "$tarball" | cut -d' ' -f1)"
name="rootfs-$sha.tar.zst"
mv "$tarball" "$out/$name"
(cd "$out" && sha256sum "$name" > "$name.sha256")
size="$(du -m "$out/$name" | cut -f1)"
echo "rootfs: $out/$name"
echo "sha256: $sha"
echo "giải nén: ${before} MiB trước khi cắt, ${after} MiB sau khi cắt; nén zstd: ${size} MiB"
if (( size > 300 )); then echo "CẢNH BÁO: file nén vượt 300 MB" >&2; exit 3; fi
