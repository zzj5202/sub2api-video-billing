"""
前置代理：在画布端和 Sub2API 之间，根据分辨率和时长修改模型名，实现按秒/分辨率计费。
监听 8082 端口，转发到 Sub2API 的 8080 端口。
"""
import json
import logging
import requests
from flask import Flask, request, Response, jsonify

logging.basicConfig(level=logging.INFO, format='%(asctime)s %(levelname)s %(message)s')
app = Flask(__name__)

SUB2API_URL = "http://sub2api:8080"

# 支持的视频模型基础名
VIDEO_MODELS = {"seedance-2.0-fast", "seedance-2.0-pro", "seedance-2.5", "seedance-2.5-pro"}

def normalize_resolution(resolution):
    """归一化分辨率"""
    res = (resolution or "480p").lower()
    if "1080" in res:
        return "1080p"
    elif "720" in res:
        return "720p"
    else:
        return "480p"

def map_model_name(model, resolution):
    """根据分辨率生成计费模型名（不包含时长，时长由 token 数体现）"""
    base = model.strip()
    # 检查是否是视频模型
    is_video = False
    for vm in VIDEO_MODELS:
        if base == vm or base.startswith(vm + "-"):
            is_video = True
            # 提取基础模型名（去掉已有的分辨率后缀）
            for suffix in ["-480p", "-720p", "-1080p"]:
                if base.endswith(suffix):
                    base = base[:-len(suffix)]
                    break
            break

    if not is_video:
        return model  # 非视频模型不修改

    res = normalize_resolution(resolution)
    return f"{base}-{res}"

@app.route("/v1/chat/completions", methods=["POST"])
def chat_completions():
    try:
        body = request.get_json(force=True)
    except Exception:
        body = {}

    model = body.get("model", "")
    resolution = body.get("resolution") or body.get("video_resolution")

    # 判断是否为 seedance 视频模型
    is_seedance = any(model.strip() == vm or model.strip().startswith(vm + "-") for vm in VIDEO_MODELS)

    # 补全时长为最低 4 秒（仅对 seedance 视频模型生效，同时作用于计费和实际生成）
    duration = body.get("duration") or body.get("duration_sec") or body.get("video_duration")
    try:
        dur_val = int(duration)
    except (TypeError, ValueError):
        dur_val = 4
    if is_seedance and dur_val < 4:
        dur_val = 4
        logging.info(f"Duration补全: {duration} -> 4s (最低4秒限制, model={model})")
    # 同步更新请求体中所有可能的时长字段
    for key in ("duration", "duration_sec", "video_duration"):
        if key in body:
            body[key] = dur_val
    if "duration" not in body and "duration_sec" not in body and "video_duration" not in body:
        body["duration"] = dur_val

    # 修改模型名（只按分辨率，不按时长）
    new_model = map_model_name(model, resolution)
    if new_model != model:
        body["model"] = new_model
        logging.info(f"Model mapped: {model} -> {new_model} (resolution={resolution}, duration={dur_val}s)")

    # 转发到 Sub2API
    headers = {k: v for k, v in request.headers.items() if k.lower() not in ("host", "content-length")}
    try:
        resp = requests.post(
            f"{SUB2API_URL}/v1/chat/completions",
            json=body,
            headers=headers,
            stream=body.get("stream", False),
            timeout=600
        )
    except Exception as e:
        logging.error(f"Forward error: {e}")
        return jsonify({"error": {"message": str(e), "type": "upstream_error"}}), 502

    if body.get("stream", False):
        def generate():
            for chunk in resp.iter_content(chunk_size=8192):
                if chunk:
                    yield chunk
        return Response(generate(), status=resp.status_code, content_type=resp.headers.get("content-type", "text/event-stream"))
    else:
        return Response(resp.content, status=resp.status_code, content_type=resp.headers.get("content-type", "application/json"))

@app.route("/health")
def health():
    return jsonify({"status": "ok"})

# 转发所有其他请求
@app.route("/", defaults={"path": ""}, methods=["GET", "POST", "PUT", "DELETE"])
@app.route("/<path:path>", methods=["GET", "POST", "PUT", "DELETE"])
def proxy(path):
    url = f"{SUB2API_URL}/{path}"
    headers = {k: v for k, v in request.headers.items() if k.lower() not in ("host", "content-length")}
    try:
        resp = requests.request(
            method=request.method,
            url=url,
            headers=headers,
            data=request.get_data(),
            params=request.args,
            timeout=60
        )
        return Response(resp.content, status=resp.status_code, content_type=resp.headers.get("content-type", "application/json"))
    except Exception as e:
        return jsonify({"error": {"message": str(e)}}), 502

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=8082, threaded=True)
