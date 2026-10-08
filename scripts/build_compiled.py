"""Build a source-free (own Python/C# sources) WRelay installer on Windows.

Run with .runtime/compile-env/Scripts/python.exe after installing both lock files.
Every build gets a new directory. Only the explicitly staged application code is
compiled; the installer never copies from the live checkout.
"""
import argparse
from datetime import datetime, timezone
import hashlib
from importlib import metadata
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tomllib
import zipfile
from xml.etree import ElementTree

ROOT = Path(__file__).resolve().parents[1]
FORBIDDEN = {".py", ".pyw", ".pyc", ".pyo", ".cs", ".c", ".cpp", ".h", ".pdb", ".map"}
WEB_FILES = ("index.html", "app.js", "style.css", "desktop.js", "local-api.js", "discord-page.js",
             "ui-components.js", "ui-components.css", "onboarding.js", "onboarding.css", "wrelay-logo.svg")


def run(args, **kwargs):
    subprocess.run([str(arg) for arg in args], check=True, **kwargs)


def worktree_changes():
    result = subprocess.check_output(
        ["git", "status", "--porcelain=v1", "--untracked-files=all"],
        cwd=ROOT, text=True, encoding="utf-8",
    )
    changes = []
    for entry in result.splitlines():
        path = entry[3:].replace("\\", "/")
        if path == ".codex-remote-attachments" or path.startswith(".codex-remote-attachments/"):
            continue
        changes.append(entry)
    return changes


def find_signtool():
    configured = os.environ.get("WRELAY_SIGNTOOL", "").strip()
    if configured:
        path = Path(configured).expanduser()
        if not path.is_file():
            raise RuntimeError("WRELAY_SIGNTOOL does not point to signtool.exe")
        return path
    located = shutil.which("signtool.exe") or shutil.which("signtool")
    if located:
        return Path(located)
    candidates = []
    for variable in ("ProgramFiles(x86)", "ProgramFiles"):
        root = os.environ.get(variable)
        if not root:
            continue
        sdk_bin = Path(root) / "Windows Kits" / "10" / "bin"
        if sdk_bin.is_dir():
            candidates.extend(sdk_bin.glob("*/x64/signtool.exe"))
    if not candidates:
        raise RuntimeError(
            "Release signing requires signtool.exe from the Windows SDK. "
            "Install the Windows SDK or set WRELAY_SIGNTOOL to its full path."
        )

    def version_key(path):
        return tuple(int(part) if part.isdigit() else 0 for part in path.parent.parent.name.split("."))

    return max(candidates, key=version_key)


def signing_configuration():
    thumbprint = os.environ.get("WRELAY_SIGN_CERT_SHA1", "").replace(" ", "").upper()
    if not re.fullmatch(r"[0-9A-F]{40}", thumbprint):
        raise RuntimeError(
            "Release signing requires WRELAY_SIGN_CERT_SHA1: the 40-digit thumbprint "
            "of a trusted code-signing certificate installed with its private key."
        )
    timestamp_url = os.environ.get("WRELAY_TIMESTAMP_URL", "").strip()
    if (not timestamp_url.startswith("https://") or any(ch.isspace() for ch in timestamp_url)
            or any(ch in timestamp_url for ch in '"$')):
        raise RuntimeError(
            "Set WRELAY_TIMESTAMP_URL to the HTTPS RFC 3161 timestamp URL from the certificate provider."
        )

    powershell = shutil.which("pwsh") or shutil.which("powershell")
    if not powershell:
        raise RuntimeError("PowerShell is required to validate the release signing certificate")
    certificate_check = (
        "$thumb = '" + thumbprint + "'; "
        "$cert = Get-ChildItem Cert:\\CurrentUser\\My -ErrorAction SilentlyContinue | "
        "Where-Object { $_.Thumbprint -eq $thumb } | Select-Object -First 1; "
        "if (-not $cert) { $cert = Get-ChildItem Cert:\\LocalMachine\\My -ErrorAction SilentlyContinue | "
        "Where-Object { $_.Thumbprint -eq $thumb } | Select-Object -First 1 }; "
        "if (-not $cert) { Write-Error 'Certificate not found in the personal certificate stores'; exit 2 }; "
        "if (-not $cert.HasPrivateKey) { Write-Error 'Certificate has no private key'; exit 3 }; "
        "$eku = @($cert.EnhancedKeyUsageList | ForEach-Object { $_.ObjectId.Value }); "
        "if ($eku -notcontains '1.3.6.1.5.5.7.3.3') { Write-Error 'Certificate lacks Code Signing usage'; exit 4 }; "
        "if ($cert.NotAfter -le (Get-Date)) { Write-Error 'Certificate has expired'; exit 5 }; "
        "Write-Output 'Release signing certificate is available.'"
    )
    run([powershell, "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass", "-Command", certificate_check])
    return find_signtool(), thumbprint, timestamp_url


def sign_and_verify(signtool, file, thumbprint, timestamp_url, description="WRelay"):
    run([signtool, "sign", "/sha1", thumbprint, "/fd", "SHA256", "/tr", timestamp_url,
         "/td", "SHA256", "/d", description, file])
    run([signtool, "verify", "/pa", "/all", "/v", file])


def inno_sign_tool(signtool, thumbprint, timestamp_url):
    # Inno Setup expands $q to a quote and $f to the file being signed.
    path = str(signtool).replace("$", "$$")
    return ("wrelay-authenticode=$q" + path + "$q sign /sha1 $q" + thumbprint +
            "$q /fd SHA256 /tr $q" + timestamp_url + "$q /td SHA256 /d $qWRelay$q $f")


def compiled_audit(build):
    report = ElementTree.parse(build / "compilation-report.xml").getroot()
    if report.get("completion") != "yes":
        raise RuntimeError("Nuitka report does not confirm completed compilation")
    own = [node for node in report.findall("module") if
           node.get("name", "").startswith(("plugins.", "panel.", "wrelay_")) or
           node.get("name") in {"__main__", "bot", "desktop_controls", "windows_titlebar"}]
    names = {node.get("name") for node in own}
    if not {"bot", "wrelay_panel", "wrelay_desktop", "wrelay_restart", "wrelay_diagnose"} <= names:
        raise RuntimeError("Required application entry missing from compilation report")
    if any(not (node.get("kind", "").startswith("CompiledPython") or
                (node.get("name") == "__main__" and node.get("kind") == "PythonMainModule"))
           for node in own):
        raise RuntimeError("Own module was not compiled to native code")
    return len(own)


def audit(folder, installed=False):
    """Fail closed for source files and archives containing sources."""
    errors = []
    for path in folder.rglob("*"):
        if not path.is_file():
            continue
        rel = path.relative_to(folder)
        if path.suffix.lower() in FORBIDDEN or path.name.lower() in ({".env", "agents.md"} if installed else {".env.prod", ".env", "agents.md"}):
            errors.append(str(rel))
        blocked_dirs = {".git", "scripts", "__pycache__"} | (set() if installed else {"data"})
        if any(part.lower() in blocked_dirs for part in rel.parts):
            errors.append(str(rel))
        if path.suffix.lower() in {".zip", ".whl"}:
            with zipfile.ZipFile(path) as archive:
                errors.extend(str(rel) + ":" + name for name in archive.namelist()
                              if Path(name).suffix.lower() in FORBIDDEN)
    if errors:
        raise RuntimeError("Source/private/development files in distribution: " + ", ".join(errors))


def licenses(target):
    target.mkdir(parents=True, exist_ok=True)
    for dist in metadata.distributions():
        name = dist.metadata["Name"]
        if name.lower() in {"ordered-set", "zstandard", "pip", "setuptools", "wheel"}:
            continue
        dest = target / name
        dest.mkdir(exist_ok=True)
        (dest / "METADATA.txt").write_text(dist.read_text("METADATA") or dist.read_text("PKG-INFO") or name, encoding="utf-8")
        for file in dist.files or []:
            if any(x in file.name.lower() for x in ("license", "licence", "copying", "notice")) and file.suffix.lower() not in FORBIDDEN:
                source = Path(dist.locate_file(file))
                if source.is_file():
                    # Keep distinct upstream notices with the same filename.
                    relative = Path(*[part for part in file.parts if part not in {"..", "."}])
                    notice = dest / relative
                    notice.parent.mkdir(parents=True, exist_ok=True)
                    shutil.copy2(source, notice)
    python_license = Path(sys.base_prefix) / "LICENSE.txt"
    if not python_license.exists():
        raise RuntimeError("Python license missing: " + str(python_license))
    shutil.copy2(python_license, target / "Python-LICENSE.txt")
    shutil.copy2(ROOT / "vendor" / "README.md", target / "QQ-adapter-patch.md")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--compile-only", action="store_true")
    parser.add_argument("--unsigned", action="store_true", help="Explicitly build an unsigned installer; published as unsigned")
    parser.add_argument("--edition", choices=("test", "release"), default="test")
    parser.add_argument("--owner", default="", help="Copyright holder supplied by the project owner")
    parser.add_argument("--resume", type=Path, help="Resume a build directory under build/compiled")
    parser.add_argument("--package-only", action="store_true", help="Package a completed --resume build")
    args = parser.parse_args()
    if metadata.version("Nuitka") != "4.2.2":
        raise RuntimeError("Install requirements-build.txt in the dedicated build environment")
    changes = worktree_changes()
    if args.edition == "release" and changes:
        raise RuntimeError(
            "Release builds require a clean, committed Git worktree. Remaining changes: " +
            ", ".join(changes[:12])
        )
    signing = signing_configuration() if args.edition == "release" and not args.compile_only and not args.unsigned else None
    run([sys.executable, ROOT / "scripts" / "check_security_dependencies.py"])
    version = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))["project"]["version"]
    builds = ROOT / "build" / "compiled"
    build = args.resume.resolve() if args.resume else builds / datetime.now().strftime("%Y%m%d-%H%M%S")
    if not build.resolve().is_relative_to(builds.resolve()):
        raise ValueError("Build directory must be within build/compiled")
    source = build / "source"
    source.mkdir(parents=True, exist_ok=True)
    expected_sources = set()
    def stage(original, dest):
        expected_sources.add(dest)
        if args.package_only:
            if not dest.is_file() or original.read_bytes() != dest.read_bytes():
                raise RuntimeError("Source changed since compilation; compile again: " + str(original))
        else:
            shutil.copy2(original, dest)
    for package in ("plugins", "panel"):
        (source / package).mkdir(exist_ok=True)
        (source / package / "__init__.py").write_text("", encoding="utf-8")
        expected_sources.add(source / package / "__init__.py")
        for path in (ROOT / package).glob("*.py"):
            stage(path, source / package / path.name)
    mapping = {"bot.py": "bot.py", "scripts/compiled_entry.py": "launcher.py",
               "panel/desktop_app.pyw": "wrelay_desktop.py", "panel/管理面板.pyw": "wrelay_panel.py",
               "panel/restart_worker.pyw": "wrelay_restart.py", "scripts/diagnose_channels.py": "wrelay_diagnose.py",
               "panel/desktop_controls.py": "desktop_controls.py", "panel/windows_titlebar.py": "windows_titlebar.py"}
    for original, staged in mapping.items():
        stage(ROOT / original, source / staged)
    if set(source.rglob("*.py")) != expected_sources:
        raise RuntimeError("Staged source set changed; start a fresh build without --resume")
    os.environ["NUITKA_CACHE_DIR"] = str(ROOT / ".runtime" / "nuitka-cache")
    command = [sys.executable, "-m", "nuitka", "--mode=standalone", "--mingw64",
               "--assume-yes-for-downloads", "--jobs=4", "--windows-console-mode=disable",
               "--output-filename=WRelay.exe", "--output-dir=" + str(build),
               "--report=" + str(build / "compilation-report.xml"),
               "--windows-icon-from-ico=" + str(ROOT / "panel" / "wrelay_panel.ico"),
               "--product-name=WRelay", "--file-description=WRelay",
               "--file-version=" + version, "--product-version=" + version,
               "--include-package=plugins", "--include-package=panel",
               "--include-module=wrelay_panel", "--include-module=wrelay_desktop",
               "--include-module=wrelay_restart", "--include-module=wrelay_diagnose",
               "--include-package=nonebot.drivers", "--include-package=nonebot.adapters.qq",
               "--include-package=nonebot.adapters.discord", "--include-package=uvicorn",
               "--include-package=websockets.asyncio",
               "--include-module=webview.platforms.winforms", "--include-module=pystray._win32",
               "--include-module=pythonnet", "--include-module=clr_loader",
               "--include-package-data=webview", "--include-package-data=pythonnet",
               "--include-package-data=clr_loader", "--nofollow-import-to=tkinter,pytest,unittest",
               "--python-flag=no_docstrings", "--python-flag=isolated"]
    for name in ("nonebot2", "nonebot-adapter-qq", "nonebot-adapter-discord", "fastapi",
                 "pydantic", "pydantic_core", "pythonnet", "pywebview", "cryptography"):
        command.append("--include-distribution-metadata=" + name)
    command.append(str(source / "launcher.py"))
    print("Build directory: " + str(build), flush=True)
    if args.package_only and not args.resume:
        raise ValueError("--package-only requires --resume")
    if not args.package_only:
        run(command, cwd=source)
    compiled_count = compiled_audit(build)
    output = build / "launcher.dist"
    # clr_loader ships optional debugging symbols alongside its runtime DLLs.
    # Remove only these known symbols; the audit still rejects unexpected files.
    for architecture in ("amd64", "x86"):
        (output / "clr_loader" / "ffi" / "dlls" / architecture / "ClrLoader.pdb").unlink(missing_ok=True)
    for name in ("WRelay.Bot.exe", "WRelay.Restart.exe", "WRelay.Guardian.exe"):
        shutil.copy2(output / "WRelay.exe", output / name)
    (output / "panel" / "web").mkdir(parents=True, exist_ok=True)
    for name in WEB_FILES:
        shutil.copy2(ROOT / "panel" / "web" / name, output / "panel" / "web" / name)
    shutil.copy2(ROOT / "panel" / "wrelay_panel.ico", output / "panel")
    csc = Path(os.environ["WINDIR"]) / "Microsoft.NET" / "Framework64" / "v4.0.30319" / "csc.exe"
    run([csc, "/nologo", "/target:library", "/optimize+",
         "/out:" + str(output / "panel" / "WRelay.WindowChrome.dll"),
         "/reference:System.Windows.Forms.dll", "/reference:System.Drawing.dll", ROOT / "panel" / "windows_titlebar.cs"])
    shutil.copy2(ROOT / ".env.example", output)
    # Version-only metadata is sufficient for the panel; development settings are excluded.
    (output / "pyproject.toml").write_text('[project]\nversion = "' + version + '"\n', encoding="utf-8")
    info = {"version": version, "edition": args.edition, "build": build.name, "nuitka": metadata.version("Nuitka"),
            "commit": subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT, text=True).strip(),
            "source_modified": bool(changes), "authenticode_signed": bool(signing),
            "built_at": datetime.now(timezone.utc).isoformat(), "copyright_holder": args.owner,
            "compiled_own_modules": compiled_count}
    (output / "build-info.json").write_text(json.dumps(info, ensure_ascii=False, indent=2), encoding="utf-8")
    shutil.copy2(ROOT / "LICENSE", output / "LICENSE")
    shutil.copy2(ROOT / "docs" / "TEST_PACKAGE.md", output / "TEST_PACKAGE.md")
    notices = output / "THIRD_PARTY_NOTICES"
    licenses(notices)
    (notices / "WRelay-THIRD_PARTY_NOTICES.md").write_text(
        """# WRelay third-party notices

WRelay is licensed under GPL-3.0-or-later. Third-party components retain their own licenses:

- **nonebot-adapter-qq 1.7.2+wrelay.1**: the MIT license is in `QQ-adapter-MIT.txt`; patch details are in `QQ-adapter-patch.md`.
- **MingCute icons**: licensed under Apache-2.0; the license text is in `MingCute-Apache-2.0.txt`.
- **Python runtime dependencies**: each included distribution's metadata and available license files are in its directory. The Python runtime license is `Python-LICENSE.txt`.
""",
        encoding="utf-8",
    )
    shutil.copy2(ROOT / "vendor" / "qq-adapter-LICENSE", notices / "QQ-adapter-MIT.txt")
    shutil.copy2(ROOT / "third_party_licenses" / "Apache-2.0.txt",
                 notices / "MingCute-Apache-2.0.txt")
    if args.owner:
        (output / "COPYRIGHT.txt").write_text("WRelay\nCopyright (c) " + str(datetime.now().year) + " " + args.owner +
                                             "\nThird-party components retain their respective licenses.\n", encoding="utf-8")
    if signing:
        signtool, thumbprint, timestamp_url = signing
        for name in ("WRelay.exe", "WRelay.Bot.exe", "WRelay.Restart.exe", "WRelay.Guardian.exe", "panel/WRelay.WindowChrome.dll"):
            sign_and_verify(signtool, output / name, thumbprint, timestamp_url)
    audit(output)
    (build / "artifact-path.txt").write_text(str(output), encoding="utf-8")
    print("Audited compiled directory: " + str(output), flush=True)
    if args.compile_only:
        return
    bootstrapper = ROOT / ".runtime" / "prerequisites" / "MicrosoftEdgeWebview2Setup.exe"
    verify = "$sig = Get-AuthenticodeSignature -LiteralPath $args[0]; if ($sig.Status -ne 'Valid' -or $sig.SignerCertificate.Subject -notmatch 'Microsoft Corporation') { exit 1 }"
    # A temporary script avoids interpolating filesystem paths into shell code.
    verify_script = build / "verify-bootstrapper.ps1"
    verify_script.write_text(verify, encoding="utf-8")
    verify_env = os.environ.copy()
    # A host running PowerShell 7 may pass module paths incompatible with 5.1.
    verify_env.pop("PSModulePath", None)
    verification_shell = shutil.which("pwsh") or shutil.which("powershell")
    if not verification_shell:
        raise RuntimeError("PowerShell is required to verify the WebView2 bootstrapper")
    run([verification_shell, "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", verify_script, bootstrapper], env=verify_env)
    candidates = [os.environ.get("INNO_SETUP_ISCC", ""), shutil.which("ISCC.exe") or ""]
    for base in (os.environ.get("ProgramFiles"), os.environ.get("ProgramFiles(x86)"), str(Path(os.environ["LOCALAPPDATA"]) / "Programs")):
        for major in ("7", "6"):
            candidates.append(str(Path(base or "") / ("Inno Setup " + major) / "ISCC.exe"))
    compiler = next((Path(p) for p in candidates if p and Path(p).is_file()), None)
    if not compiler:
        raise RuntimeError("Install Inno Setup or set INNO_SETUP_ISCC")
    (ROOT / "dist").mkdir(exist_ok=True)
    installer_script = "WRelay.iss" if args.edition == "release" else "WRelay-Test.iss"
    installer_name = "WRelay-Setup.exe" if args.edition == "release" else "WRelay-Test-" + build.name + "-Setup.exe"
    staging_output = build / "installer-output"
    staging_output.mkdir(exist_ok=True)
    # Inno Setup 6 的 ISCC 只接受短选项；/O<path> 在 6 和 7 中都有效，
    # 因此不要使用 --output-dir= 这类长选项。
    compiler_args = [compiler, "/Qp", "/DCompiledRoot=" + str(output), "/DBuildVersion=" + version,
                     "/DBuildId=" + build.name, "/O" + str(staging_output)]
    if args.unsigned:
        compiler_args.append("/DUnsigned=1")
    if signing:
        compiler_args.extend(["--no-ide-signtools", "--signtool=" + inno_sign_tool(*signing)])
    compiler_args.append(ROOT / installer_script)
    run(compiler_args, cwd=ROOT)
    staged_installer = staging_output / installer_name
    if not staged_installer.is_file():
        raise RuntimeError("Inno Setup finished without creating " + installer_name)
    if signing:
        run([signing[0], "verify", "/pa", "/all", "/v", staged_installer])
    installer = ROOT / "dist" / installer_name
    os.replace(staged_installer, installer)
    checksum = hashlib.sha256(installer.read_bytes()).hexdigest().upper()
    checksum_path = installer.with_suffix(".sha256")
    temporary_checksum = checksum_path.with_suffix(checksum_path.suffix + ".tmp")
    temporary_checksum.write_text(checksum + "  " + installer.name + "\n", encoding="ascii")
    os.replace(temporary_checksum, checksum_path)
    print("Installer: " + str(installer), flush=True)
    print("SHA256: " + checksum, flush=True)
    if args.edition == "release":
        return
    bundle = installer.with_name("WRelay-Test-" + build.name + "-ForTesters.zip")
    with zipfile.ZipFile(bundle, "w", compression=zipfile.ZIP_STORED) as archive:
        archive.write(installer, installer.name)
        archive.write(installer.with_suffix(".sha256"), installer.with_suffix(".sha256").name)
        archive.write(ROOT / "docs" / "TEST_PACKAGE.md", "TEST_PACKAGE.md")
    print("Tester bundle: " + str(bundle), flush=True)


if __name__ == "__main__":
    main()
