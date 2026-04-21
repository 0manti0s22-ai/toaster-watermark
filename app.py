from __future__ import annotations

import base64
import shutil
import uuid
import zipfile
from pathlib import Path

from flask import Flask, jsonify, render_template, request, send_file
from flask_cors import CORS
from werkzeug.utils import secure_filename

from watermark import WatermarkError, WatermarkProcessor

BASE_DIR = Path(__file__).resolve().parent
UPLOADS_DIR = BASE_DIR / "uploads"
OUTPUTS_DIR = BASE_DIR / "outputs"

MAX_FILE_SIZE_BYTES = 20 * 1024 * 1024
MAX_BATCH_COUNT = 50
ALLOWED_EXTENSIONS = {".jpg", ".jpeg", ".png", ".webp"}

app = Flask(__name__)
CORS(app)
app.config["MAX_CONTENT_LENGTH"] = MAX_FILE_SIZE_BYTES * MAX_BATCH_COUNT + MAX_FILE_SIZE_BYTES

processor = WatermarkProcessor()

for folder in (UPLOADS_DIR, OUTPUTS_DIR):
    folder.mkdir(parents=True, exist_ok=True)


def _cleanup_job(job_id: str) -> None:
    shutil.rmtree(UPLOADS_DIR / job_id, ignore_errors=True)
    shutil.rmtree(OUTPUTS_DIR / job_id, ignore_errors=True)


@app.errorhandler(413)
def request_entity_too_large(_error):
    return (
        jsonify(
            {
                "ok": False,
                "message": "Слишком большой объем данных. Максимум 20MB на файл и до 50 файлов за раз.",
            }
        ),
        413,
    )


@app.route("/", methods=["GET"])
def index():
    return render_template("index.html")


@app.route("/api/process", methods=["POST"])
def process_images():
    if not processor.is_logo_available():
        return jsonify({"ok": False, "message": "Логотип static/logo.png не найден на сервере."}), 400

    selected_format = request.form.get("format", "")
    try:
        processor.validate_format(selected_format)
    except WatermarkError as exc:
        return jsonify({"ok": False, "message": str(exc)}), 400

    files = request.files.getlist("images")
    if not files:
        return jsonify({"ok": False, "message": "Выберите изображения для обработки."}), 400

    try:
        processor.validate_images([f.filename for f in files], MAX_BATCH_COUNT)
    except WatermarkError as exc:
        return jsonify({"ok": False, "message": str(exc)}), 400

    job_id = str(uuid.uuid4())
    upload_job_dir = UPLOADS_DIR / job_id
    output_job_dir = OUTPUTS_DIR / job_id
    upload_job_dir.mkdir(parents=True, exist_ok=True)
    output_job_dir.mkdir(parents=True, exist_ok=True)

    try:
        photos_base64: list[str] = []
        for file_storage in files:
            original_name = secure_filename(file_storage.filename or "")
            if not original_name:
                raise WatermarkError("Один из файлов имеет некорректное имя.")

            ext = Path(original_name).suffix.lower()
            if ext not in ALLOWED_EXTENSIONS:
                raise WatermarkError(f"Формат файла {original_name} не поддерживается.")

            data = file_storage.read()
            if not data:
                raise WatermarkError(f"Файл {original_name} пустой.")
            if len(data) > MAX_FILE_SIZE_BYTES:
                raise WatermarkError(f"Файл {original_name} превышает лимит 20MB.")

            input_path = upload_job_dir / original_name
            input_path.write_bytes(data)

            processed_bytes = processor.apply_to_file(input_path, selected_format)
            photos_base64.append(base64.b64encode(processed_bytes).decode("utf-8"))
            output_path = output_job_dir / f"wm_{original_name}"
            output_path.write_bytes(processed_bytes)

        zip_path = output_job_dir / "watermarked.zip"
        with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as zipf:
            for file in output_job_dir.iterdir():
                if file.is_file() and file.name != zip_path.name:
                    zipf.write(file, arcname=file.name)

        return jsonify(
            {
                "ok": True,
                "job_id": job_id,
                "download_url": f"/download/{job_id}",
                "photos": photos_base64,
            }
        )
    except WatermarkError as exc:
        _cleanup_job(job_id)
        return jsonify({"ok": False, "message": str(exc)}), 400
    except OSError:
        _cleanup_job(job_id)
        return jsonify({"ok": False, "message": "Ошибка обработки файлов на сервере."}), 500


@app.route("/download/<job_id>", methods=["GET"])
def download_result(job_id: str):
    zip_path = OUTPUTS_DIR / job_id / "watermarked.zip"
    if not zip_path.exists():
        return "Архив не найден или уже удален.", 404

    response = send_file(zip_path, as_attachment=True, download_name="watermarked.zip")

    @response.call_on_close
    def _remove_temp_files() -> None:
        _cleanup_job(job_id)

    return response


if __name__ == "__main__":
    app.run(debug=True)
