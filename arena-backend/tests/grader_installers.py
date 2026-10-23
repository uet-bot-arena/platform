"""Exercise both installers with real graders on the existing Windows/WSL accounts.

Run from WSL. Creates a temporary Startup shortcut and user service and removes both.
The fresh-account / logout acceptance check remains a manual step in docs/MAY-CHAM.md.
"""
import hashlib
import os
from pathlib import Path
import subprocess
import time
import uuid

from grader_cluster import Cluster, ROOT, windows_path


def main():
    cluster = Cluster()
    cluster.results["mode"] = "installers"
    profile = "m1-smoke-" + uuid.uuid4().hex[:10]
    repo = ROOT.parent
    ps_script = windows_path(repo / "scripts" / "install-grader.ps1")
    ps_base = ["powershell.exe", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", ps_script, "-Profile", profile]
    local = subprocess.check_output(["cmd.exe", "/c", "echo %LOCALAPPDATA%"], cwd="/mnt/c", text=True).strip()
    win_base = Path(subprocess.check_output(["wslpath", "-u", local], text=True).strip()) / "uet-arena" / ("grader-install-" + profile)
    linux_base = Path(os.environ.get("XDG_DATA_HOME") or Path.home() / ".local/share") / "uet-arena" / ("grader-install-" + profile)
    unit = f"uet-arena-grader-{profile}.service"

    def run(args):
        result = subprocess.run(args, cwd=repo, text=True, capture_output=True, timeout=240)
        assert result.returncode == 0, (args[0], result.stdout[-3000:], result.stderr[-3000:])
        return result.stdout

    try:
        for script in ("install-grader.ps1", "install-grader.sh"):
            path = repo / "scripts" / script
            expected = path.with_name(script + ".sha256").read_text().split()[0]
            cluster.check(script + "_sha256", hashlib.sha256(path.read_bytes()).hexdigest() == expected)
        cluster.start_hub()
        for name, base, args in (("installed-windows", win_base, ps_base + ["-HubUrl", cluster.origin]),
                                 ("installed-bwrap", linux_base, ["bash", str(repo / "scripts/install-grader.sh"),
                                  "--profile", profile, "--hub", cluster.origin])):
            token = cluster.api(cluster.admin, "POST", "/admin/graders", expected=201, json={"name": name})
            base.mkdir(parents=True)
            path = base / "grader-token"
            path.write_text(token["token"])
            if name == "installed-bwrap":
                path.chmod(0o600)
            run(args)
            items = cluster.wait_online([name])
            cluster.check(name + "_online", items[name]["backend"] == ("windows" if name.endswith("windows") else "bwrap"))
            if name == "installed-windows":
                until = time.monotonic() + 20
                log = base / "grader.log"
                while time.monotonic() < until and (not log.is_file() or "Đã kết nối hub" not in log.read_text(encoding="utf-8")):
                    time.sleep(0.1)
                cluster.check("windows_pythonw_connected_and_pid_created", (base / "grader.pid").is_file()
                              and log.is_file() and "Đã kết nối hub" in log.read_text(encoding="utf-8"))
                startup = subprocess.check_output(["powershell.exe", "-NoProfile", "-Command",
                    "[Environment]::GetFolderPath('Startup')"], text=True).strip()
                shortcut = Path(subprocess.check_output(["wslpath", "-u", startup], text=True).strip()) / f"UET Arena grader {profile}.lnk"
                cluster.check("windows_startup_shortcut_created", shortcut.is_file())
                run(ps_base + ["-Stop"])
                cluster.check("windows_stop_exits_pythonw_and_removes_pid", not (base / "grader.pid").exists())
                run(ps_base + ["-Uninstall"])
                cluster.check("windows_uninstall_removes_install_and_shortcut", not base.exists() and not shortcut.exists())
            else:
                config = run(["systemctl", "--user", "cat", unit])
                cluster.check("systemd_uses_loadcredential", f"LoadCredential=grader-token:{base}/grader-token" in config
                              and "--token-file" not in config)
                cluster.check("systemd_service_is_running", run(["systemctl", "--user", "show", unit, "-p", "ActiveState", "--value"]).strip() == "active")
                until = time.monotonic() + 20
                while time.monotonic() < until:
                    journal = run(["journalctl", "--user", "-u", unit, "--no-pager", "-n", "30"])
                    if "Đã kết nối hub" in journal:
                        break
                    time.sleep(0.2)
                cluster.check("systemd_grader_authenticated_using_credential", "Đã kết nối hub" in journal)
                cluster.api(cluster.admin, "POST", f"/admin/graders/{token['id']}/revoke")
                until = time.monotonic() + 10
                while time.monotonic() < until:
                    state = run(["systemctl", "--user", "show", unit, "-p", "ActiveState", "--value"]).strip()
                    if state == "failed":
                        break
                    time.sleep(0.2)
                status = run(["systemctl", "--user", "show", unit, "-p", "ExecMainStatus", "--value"]).strip()
                cluster.check("revoked_service_exits_without_restart", state == "failed" and status == "3" and
                    run(["systemctl", "--user", "show", unit, "-p", "NRestarts", "--value"]).strip() == "0", state=state, process_status=status)
                run(["bash", str(repo / "scripts/install-grader.sh"), "--profile", profile, "--uninstall"])
                cluster.check("linux_uninstall_removes_install_and_unit", not base.exists() and
                    not (Path(os.environ.get("XDG_CONFIG_HOME") or Path.home() / ".config") / "systemd/user" / unit).exists())
        bad_rootfs = cluster.data / "wrong-rootfs.tar.zst"
        bad_rootfs.write_bytes(b"untrusted rootfs; this must never be unpacked")
        rejected = subprocess.run(["bash", str(repo / "scripts/install-grader.sh"), "--profile", profile,
            "--hub", cluster.origin, "--rootfs", str(bad_rootfs), "--sha256", "0" * 64], cwd=repo,
            capture_output=True, text=True, timeout=60)
        unit_path = Path(os.environ.get("XDG_CONFIG_HOME") or Path.home() / ".config") / "systemd/user" / unit
        cluster.check("wrong_rootfs_sha256_rejected_before_service_creation", rejected.returncode != 0 and not unit_path.exists())
        run(["bash", str(repo / "scripts/install-grader.sh"), "--profile", profile, "--uninstall"])
        cluster.check("linux_partial_install_can_be_uninstalled", not linux_base.exists())
        print("ALL GRADER INSTALLER CHECKS PASSED", flush=True)
    finally:
        # Only these unique test profiles are touched, including when installation fails halfway.
        subprocess.run(ps_base + ["-Uninstall"], cwd=repo, capture_output=True, timeout=60)
        subprocess.run(["bash", str(repo / "scripts/install-grader.sh"), "--profile", profile, "--uninstall"],
                       cwd=repo, capture_output=True, timeout=240)
        cluster.stop()


if __name__ == "__main__":
    main()
