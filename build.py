import shutil
import PyInstaller.__main__
import os
import datetime

dist_path = f"build"
start_file = "main.py"
icon_name = "icon.ico"
no_console = False
run_exe = False
current_directory = os.path.dirname(os.path.abspath(__file__))

major_version = "2"
minor_version = "1"
patch_version = "0"
build_number = "0"
debug = False
company_name = "savelychercov"
product_name = "KeysAccountingBot"
description = "Telegram bot for accounting keys"

title = f"KeysAccountingBot v{major_version}.{minor_version}.{patch_version}"

version_template = f"""
VSVersionInfo(
    ffi=FixedFileInfo(
        filevers=({major_version}, {minor_version}, {patch_version}, {build_number}),
        prodvers=({major_version}, {minor_version}, {patch_version}, {build_number}),
        mask=0x3f,
        flags={"0x1" if debug else "0x0"},
        OS=0x40004,
        fileType=0x1,
        subtype=0x0,
        date={datetime.datetime.now().year, datetime.datetime.now().month}
    ),
    kids=[
        StringFileInfo([
            StringTable(
                u'040904E4',
                [
                    StringStruct(u'CompanyName', u'{company_name}'),
                    StringStruct(u'FileDescription', u'{description}'),
                    StringStruct(u'FileVersion', u'{f"{major_version}.{minor_version}.{patch_version}.{build_number}"}'),
                    StringStruct(u'LegalCopyright', u'© {datetime.datetime.now().year} {company_name}'),
                    StringStruct(u'ProductName', u'{product_name}'),
                    StringStruct(u'ProductVersion', u'1.0.0.0'),
                ]
            )
        ]),
        VarFileInfo([VarStruct(u'Translation', [1033, 1252])])
    ]
)
"""


dirs = [
    "credentials",
]

files = [
    "icon.ico",
    "logger.py",
    "sheets.py",
    "bot.py"
]

command = [
    start_file,
    "--noconfirm",
    "--onefile",
    f"--icon={icon_name}",
    f"--name={title}",
    "--clean",
    f"--distpath={dist_path}",
    f"--version-file={dist_path}/version_info.txt",
]

if no_console:
    command.append("--noconsole")

for d in dirs:
    command.append(f"--add-data={d};{d}./")

for filename in files:
    filename = os.path.join(current_directory, filename)
    print("Adding file:", filename)
    command.append(f"--add-data={filename};.")


def build():
    shutil.rmtree(dist_path, ignore_errors=True)
    os.makedirs(dist_path, exist_ok=True)
    with open(f"{dist_path}/version_info.txt", "w", encoding="utf-8") as f:
        f.write(version_template)

    PyInstaller.__main__.run(command)

    shutil.rmtree(f"{dist_path}/{title}")
    os.unlink(f"{title}.spec")
    os.unlink(f"{dist_path}/version_info.txt")


if __name__ == "__main__":
    for d in dirs:
        if not os.path.exists(d):
            raise Exception(f"Directory {d} not found")
    for filename in files+[start_file, icon_name]:
        if not os.path.exists(filename):
            raise Exception(f"File {filename} not found")

    build()

    if run_exe:
        os.startfile(f"{dist_path}\\{title}.exe")
