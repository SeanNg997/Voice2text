#!/usr/bin/env python3
"""Local web UI for Voice2Text."""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import random
import string
import time
import urllib.parse
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from threading import Lock, Thread

import requests
from flask import Flask, jsonify, render_template, request, send_from_directory

APPID = "b09eeeaa"
API_SECRET = "Mjg5ZWFlZDhkYWU0ZjA0YzU1MTRlMTRk"
API_KEY = "35d99b34b1f1ff342c44d9242d7c4f77"
BASE_URL = "https://office-api-ist-dx.iflyaisol.com"

BASE_DIR = Path(__file__).resolve().parent
UPLOAD_DIR = BASE_DIR / "uploads"
OUTPUT_DIR = BASE_DIR / "outputs"
UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

app = Flask(__name__)

TASKS: dict[str, dict] = {}
TASK_LOCK = Lock()


def get_datetime_str() -> str:
    tz = timezone(timedelta(hours=8))
    return datetime.now(tz).strftime("%Y-%m-%dT%H:%M:%S+0800")


def random_string(length: int = 16) -> str:
    return "".join(random.choices(string.ascii_letters + string.digits, k=length))


def generate_signature(params: dict, secret_key: str) -> str:
    signing_items = (
        (str(k), str(v))
        for k, v in sorted(params.items())
        if k != "signature" and v is not None and str(v) != ""
    )
    base_string = "&".join(
        f"{urllib.parse.quote_plus(k, safe='')}={urllib.parse.quote_plus(v, safe='')}"
        for k, v in signing_items
    )
    sig = hmac.new(
        secret_key.encode("utf-8"),
        base_string.encode("utf-8"),
        hashlib.sha1,
    ).digest()
    return base64.b64encode(sig).decode("utf-8")


def upload_audio(file_path: str, language: str = "autodialect") -> tuple[str, str]:
    fp = Path(file_path)
    file_size = fp.stat().st_size
    sig_random = random_string()

    params = {
        "appId": APPID,
        "accessKeyId": API_KEY,
        "dateTime": get_datetime_str(),
        "signatureRandom": sig_random,
        "fileSize": str(file_size),
        "fileName": fp.name,
        "language": language,
        "durationCheckDisable": "true",
    }
    signature = generate_signature(params, API_SECRET)

    with open(fp, "rb") as f:
        audio_data = f.read()

    resp = requests.post(
        f"{BASE_URL}/v2/upload",
        params=params,
        headers={
            "Content-Type": "application/octet-stream",
            "signature": signature,
        },
        data=audio_data,
        timeout=300,
    )

    result = resp.json()
    if str(result.get("code")) != "000000":
        raise RuntimeError(f"上传失败: {result}")

    return result["content"]["orderId"], sig_random


def result_to_markdown(content: dict) -> str:
    order_result = content.get("orderResult", "")
    if isinstance(order_result, str):
        order_result = json.loads(order_result) if order_result else {}

    lattice_list = order_result.get("lattice", [])
    if not lattice_list:
        return "# 转写结果\n\n（无内容）\n"

    lines = []
    for item in lattice_list:
        json_1best = item.get("json_1best", "")
        if isinstance(json_1best, str):
            json_1best = json.loads(json_1best) if json_1best else {}

        st = json_1best.get("st", {})
        speaker = st.get("rl", "")
        words = []
        for rt_item in st.get("rt", []):
            for ws_item in rt_item.get("ws", []):
                for cw_item in ws_item.get("cw", []):
                    words.append(cw_item.get("w", ""))

        text = "".join(words).strip()
        if not text:
            continue

        bg_ms = int(st.get("bg", "0"))
        ed_ms = int(st.get("ed", "0"))
        bg_str = f"{bg_ms // 3600000:02d}:{(bg_ms % 3600000) // 60000:02d}:{(bg_ms % 60000) // 1000:02d}"
        ed_str = f"{ed_ms // 3600000:02d}:{(ed_ms % 3600000) // 60000:02d}:{(ed_ms % 60000) // 1000:02d}"

        if speaker:
            lines.append(f"**[{bg_str} - {ed_str}] 说话人{speaker}**: {text}")
        else:
            lines.append(f"**[{bg_str} - {ed_str}]** {text}")

    md = "# 语音转写结果\n\n"
    md += "\n\n".join(lines)
    md += "\n"
    return md


def _timestamp() -> str:
    return datetime.now().strftime("%H:%M:%S")


def _create_task(filename: str) -> str:
    task_id = uuid.uuid4().hex
    with TASK_LOCK:
        TASKS[task_id] = {
            "task_id": task_id,
            "status": "running",
            "phase": "uploading",
            "filename": filename,
            "message": "准备上传文件...",
            "logs": [{"time": _timestamp(), "message": "任务已创建"}],
            "download_url": None,
            "output_name": None,
            "created_at": int(time.time()),
        }
    return task_id


def _append_log(task_id: str, message: str, *, status: str | None = None, phase: str | None = None) -> None:
    with TASK_LOCK:
        task = TASKS.get(task_id)
        if not task:
            return
        if status:
            task["status"] = status
        if phase:
            task["phase"] = phase
        task["message"] = message
        task["logs"].append({"time": _timestamp(), "message": message})
        task["logs"] = task["logs"][-60:]


def _set_done(task_id: str, output_name: str) -> None:
    _append_log(task_id, "转写完成，文件已生成。", status="done", phase="done")
    with TASK_LOCK:
        task = TASKS.get(task_id)
        if not task:
            return
        task["output_name"] = output_name
        task["download_url"] = f"/api/download/{output_name}"


def _set_error(task_id: str, err: str) -> None:
    _append_log(task_id, f"任务失败：{err}", status="error", phase="error")


def poll_result_with_status(order_id: str, signature_random: str, max_wait: int, task_id: str) -> dict:
    start = time.time()
    while time.time() - start < max_wait:
        params = {
            "accessKeyId": API_KEY,
            "dateTime": get_datetime_str(),
            "signatureRandom": signature_random,
            "orderId": order_id,
            "resultType": "transfer",
        }
        signature = generate_signature(params, API_SECRET)

        resp = requests.post(
            f"{BASE_URL}/v2/getResult",
            params=params,
            headers={
                "Content-Type": "application/json",
                "signature": signature,
            },
            json={},
            timeout=30,
        )

        result = resp.json()
        if str(result.get("code")) != "000000":
            raise RuntimeError(f"查询失败: {result}")

        content = result.get("content", {})
        order_info = content.get("orderInfo", {})
        status = order_info.get("status")

        if status == 4:
            _append_log(task_id, "转写完成，正在整理 Markdown...", phase="processing")
            return content
        if status == -1:
            raise RuntimeError(f"转写失败: {order_info}")

        estimate = content.get("taskEstimateTime", 0)
        status_map = {0: "创建中", 3: "处理中"}
        readable_status = status_map.get(status, f"状态{status}")
        _append_log(
            task_id,
            f"转写状态：{readable_status}，预计剩余 {estimate // 1000}s",
            phase="processing",
        )
        time.sleep(5)

    raise TimeoutError(f"等待超过 {max_wait} 秒，转写未完成")


def _process_task(task_id: str, audio_path: Path, language: str, timeout: int) -> None:
    try:
        _append_log(task_id, "正在上传文件到转写服务...", phase="uploading")
        order_id, signature_random = upload_audio(str(audio_path), language=language)
        _append_log(task_id, f"上传完成（orderId: {order_id}），开始转写...", phase="processing")

        content = poll_result_with_status(order_id, signature_random, timeout, task_id)
        markdown = result_to_markdown(content)

        base_name = audio_path.name
        if "_" in base_name:
            base_name = base_name.split("_", 1)[1]
        stem = Path(base_name).stem
        output_name = f"{stem}_{datetime.now().strftime('%Y%m%d_%H%M%S')}.md"
        output_path = OUTPUT_DIR / output_name
        output_path.write_text(markdown, encoding="utf-8")
        _set_done(task_id, output_name)
    except Exception as exc:  # noqa: BLE001
        _set_error(task_id, str(exc))
    finally:
        try:
            audio_path.unlink(missing_ok=True)
        except OSError:
            pass


@app.get("/")
def index():
    return render_template("index.html")


@app.post("/api/transcribe")
def transcribe():
    file = request.files.get("file")
    if not file or not file.filename:
        return jsonify({"error": "请选择音频文件"}), 400

    language = request.form.get("language", "autodialect")
    timeout = request.form.get("timeout", "600")
    try:
        timeout_int = max(60, min(int(timeout), 3600))
    except ValueError:
        timeout_int = 600

    safe_name = Path(file.filename).name
    upload_name = f"{uuid.uuid4().hex}_{safe_name}"
    upload_path = UPLOAD_DIR / upload_name
    file.save(upload_path)

    task_id = _create_task(safe_name)
    worker = Thread(
        target=_process_task,
        args=(task_id, upload_path, language, timeout_int),
        daemon=True,
    )
    worker.start()
    return jsonify({"task_id": task_id})


@app.get("/api/task/<task_id>")
def task_status(task_id: str):
    with TASK_LOCK:
        task = TASKS.get(task_id)
        if not task:
            return jsonify({"error": "任务不存在"}), 404
        return jsonify(task)


@app.get("/api/files")
def list_files():
    items = []
    for path in sorted(OUTPUT_DIR.glob("*.md"), key=lambda p: p.stat().st_mtime, reverse=True):
        stat = path.stat()
        items.append(
            {
                "name": path.name,
                "size": stat.st_size,
                "updated_at": datetime.fromtimestamp(stat.st_mtime).strftime("%Y-%m-%d %H:%M:%S"),
                "download_url": f"/api/download/{path.name}",
            }
        )
    return jsonify({"files": items})


@app.get("/api/download/<path:filename>")
def download_file(filename: str):
    return send_from_directory(OUTPUT_DIR, filename, as_attachment=True)


if __name__ == "__main__":
    app.run(host="127.0.0.1", port=8000, debug=False)
