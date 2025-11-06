import os
import shutil
import socket
import tempfile

import typer
import uvicorn
from fastapi import FastAPI, File, Form, UploadFile
from fastapi.responses import FileResponse, HTMLResponse

app = FastAPI()
UPLOAD_DIR = "uploads"  # default, overridden by typer later
NOTES_FILE = "notes.txt"  # default, overridden by typer later


def get_local_ip():
    """Get the local IP address."""
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
            s.connect(("8.8.8.8", 80))
            return s.getsockname()[0]
    except Exception:
        return "127.0.0.1"


def human_readable_size(num_bytes: float) -> str:
    """Convert bytes to a friendly string."""
    for unit in ["B", "KB", "MB", "GB", "TB"]:
        if num_bytes < 1024:
            return f"{num_bytes:.1f} {unit}"
        num_bytes /= 1024
    return f"{num_bytes:.1f} PB"


@app.get("/", response_class=HTMLResponse)
async def index(sort: str = "newest", mode: str = "top"):
    """Serve the main index page with file listings."""
    items = []

    # gather files and folders info
    if mode == "flat":
        # collect all files recursively
        for root, _, filenames in os.walk(UPLOAD_DIR):
            for f in filenames:
                rel_path = os.path.relpath(os.path.join(root, f), UPLOAD_DIR)
                full_path = os.path.join(root, f)
                size = os.path.getsize(full_path)
                is_nested = os.path.relpath(root, UPLOAD_DIR) != "."
                items.append(
                    (
                        rel_path,
                        "nested_file" if is_nested else "file",
                        os.path.getmtime(full_path),
                        size,
                    )
                )
    else:  # top
        # top-level files and folders only
        for name in os.listdir(UPLOAD_DIR):
            full = os.path.join(UPLOAD_DIR, name)
            if os.path.isdir(full):
                # compute folder size recursively
                size = sum(
                    os.path.getsize(os.path.join(dirpath, f))
                    for dirpath, _, filenames in os.walk(full)
                    for f in filenames
                )
                items.append((name, "folder", os.path.getmtime(full), size))
            else:
                size = os.path.getsize(full)
                items.append((name, "file", os.path.getmtime(full), size))

    # sort items based on sort parameter
    if sort == "oldest":
        items.sort(key=lambda x: x[2])
    elif sort == "az":
        items.sort(key=lambda x: x[0].lower())
    elif sort == "za":
        items.sort(key=lambda x: x[0].lower(), reverse=True)
    elif sort == "size_asc":
        items.sort(key=lambda x: x[3])
    elif sort == "size_desc":
        items.sort(key=lambda x: x[3], reverse=True)
    else:  # newest
        items.sort(key=lambda x: x[2], reverse=True)

    # gather files and folders served
    links = ""
    for name, kind, _, size in items:
        size_str = human_readable_size(size)
        icon = "📁" if kind == "folder" else "📄" if kind == "file" else "📂📄"
        href = f"/download_folder/{name}" if kind == "folder" else f"/files/{name}"
        links += (
            f'<a href="{href}" class="list-group-item list-group-item-action">'
            f'{icon} {name} <span class="file-size">({size_str})</span>'
            f"</a>"
        )

    # load html template file, and inject dynamic variables
    with open("index.html", "r", encoding="utf-8") as f:
        html = f.read()
    links_or_warning = links if items else "<i>No files yet.</i>"
    html = html.replace("{{ LINKS_PLACEHOLDER }}", links_or_warning)
    html = html.replace("{{ NOTES_FILE_PLACEHOLDER }}", NOTES_FILE)
    html = html.replace("{{ SORT_SELECTED_PLACEHOLDER }}", sort)
    html = html.replace("{{ MODE_SELECTED_PLACEHOLDER }}", mode)

    return HTMLResponse(content=html)


@app.get("/download_folder/{folder_name}")
async def download_folder(folder_name: str):
    """Zip a folder in UPLOAD_DIR and return it."""
    folder_path = os.path.join(UPLOAD_DIR, folder_name)
    if not os.path.isdir(folder_path):
        return HTMLResponse("<h3>Folder not found</h3>", status_code=404)

    tmp_zip = tempfile.NamedTemporaryFile(delete=False, suffix=".zip")
    shutil.make_archive(tmp_zip.name[:-4], "zip", folder_path)
    return FileResponse(tmp_zip.name, filename=f"{folder_name}.zip")


@app.post("/upload", response_class=HTMLResponse)
async def upload(files: list[UploadFile] = File(...)):
    """Handle file uploads, save to UPLOAD_DIR."""
    for file in files:
        if not file.filename:
            continue
        subdir = os.path.dirname(file.filename)
        save_dir = os.path.join(UPLOAD_DIR, subdir)
        os.makedirs(save_dir, exist_ok=True)
        path = os.path.join(save_dir, os.path.basename(file.filename))
        with open(path, "wb") as f:
            f.write(await file.read())
    return HTMLResponse("OK")


@app.get("/files/{path:path}")
async def get_file(path: str):
    """Get full path given relative path."""
    full_path = os.path.join(UPLOAD_DIR, path)
    if not os.path.exists(full_path):
        return HTMLResponse("<h3>File not found</h3>", status_code=404)
    return FileResponse(full_path)


@app.get("/shared_text", response_class=HTMLResponse)
async def get_shared_text():
    """Return the content of the shared NOTES_FILE file."""
    path = os.path.join(UPLOAD_DIR, NOTES_FILE)
    if os.path.exists(path):
        with open(path, "r", encoding="utf-8") as f:
            content = f.read()
    else:
        content = ""
    return HTMLResponse(content)


@app.post("/shared_text", response_class=HTMLResponse)
async def save_shared_text(content: str = Form(...)):
    """Save text to the shared NOTES_FILE file."""
    path = os.path.join(UPLOAD_DIR, NOTES_FILE)
    normalized = content.replace("\r\n", "\n")
    with open(path, "w", encoding="utf-8", newline="\n") as f:
        f.write(normalized)
    return HTMLResponse("OK")


@app.get("/favicon.ico")
async def favicon():
    """Return favicon."""
    return FileResponse("favicon.ico")


cli = typer.Typer(help="Local file server with upload/download UI.")


@cli.command()
def serve(
    folder: str = typer.Argument(UPLOAD_DIR, help="Folder to serve files from"),
    notes: str = typer.Option(NOTES_FILE, "--notes", help="Shared notes file name"),
):
    """Run the FastAPI file server serving the given folder."""
    global UPLOAD_DIR, NOTES_FILE
    UPLOAD_DIR = os.path.abspath(folder)
    NOTES_FILE = notes

    # create upload dir if not existent
    os.makedirs(UPLOAD_DIR, exist_ok=True)

    # print server info
    url = get_local_ip()
    typer.echo(f"📂 Serving folder: {UPLOAD_DIR}")
    typer.echo(f"📝 Shared notes file: {NOTES_FILE}")
    typer.echo(f"🌐 Open in browser: http://{url}:8000")

    # serve
    uvicorn.run(app, host="0.0.0.0", port=8000)


if __name__ == "__main__":
    cli()
