from flask import Flask, request, jsonify, Response
import requests
import json
import time
import os
import logging

app = Flask(__name__)
logging.basicConfig(level=logging.INFO)

XYQ_BASE_URL = "https://xyq.jianying.com/api/biz/v1"
XYQ_API_KEY = os.environ.get("XYQ_API_KEY", "ak-dNeWSIToePFC8B5I0TIsWjqboFXU5habOW7OayCWRVM")

MODEL_MAP = {
    "seedance-2.0": "seedance2.0_fast_vision",
    "seedance-2.0-fast": "seedance2.0_fast_vision",
    "seedance-2.0-pro": "seedance2.0_vision",
    "seedance-2.5": "seedance2.5_vision",
    "seedance-2.5-pro": "seedance2.5_vision",
    "seedance_2.5": "seedance2.5_vision",
    "seedance2.0": "seedance2.0_fast_vision",
    "seedance2.5": "seedance2.5_vision",
}

def get_xyq_headers():
    return {
        "Authorization": f"Bearer {XYQ_API_KEY}",
        "Content-Type": "application/json",
        "Accept": "application/json"
    }

@app.before_request
def log_request():
    logging.info(f"REQUEST: {request.method} {request.path} | Body: {request.get_data(as_text=True)[:200]}")

def submit_run(message, video_model=None, resolution=None, duration_sec=None, ratio=None):
    # 使用沉浸式短片视频 agent
    body = {
        "message": message,
        "agent_name": "pippit_video_part_agent",
    }
    # 传递视频参数（分辨率、时长、比例），确保使用沉浸式短片而非全能创作
    video_part_tool_param = {}
    if video_model:
        video_part_tool_param["model"] = video_model
    if resolution:
        video_part_tool_param["resolution"] = resolution
    if duration_sec:
        video_part_tool_param["duration_sec"] = duration_sec
    if ratio:
        video_part_tool_param["ratio"] = ratio
    if message:
        video_part_tool_param["prompt"] = message
    if video_part_tool_param:
        body["video_part_tool_param"] = video_part_tool_param

    resp = requests.post(
        f"{XYQ_BASE_URL}/skill/submit_run",
        headers=get_xyq_headers(),
        json=body,
        timeout=30
    )
    data = resp.json()
    if data.get("ret") != "0":
        raise Exception(f"submit_run failed: {data.get('errmsg')}")

    run = data["data"]["run"]
    return run["run_id"], run["thread_id"]

def get_thread(run_id, thread_id):
    resp = requests.post(
        f"{XYQ_BASE_URL}/skill/get_thread",
        headers=get_xyq_headers(),
        json={"run_id": run_id, "thread_id": thread_id},
        timeout=30
    )
    data = resp.json()
    if data.get("ret") != "0":
        raise Exception(f"get_thread failed: {data.get('errmsg')}")
    return data["data"]["thread"]

def extract_video_url(thread):
    for run in thread.get("run_list", []):
        for entry in run.get("entry_list", []):
            artifact = entry.get("artifact")
            if artifact:
                for content in artifact.get("content", []):
                    if content.get("sub_type") == "biz/x_data_video":
                        try:
                            video_data = json.loads(content["data"])
                            video = video_data.get("video", {})
                            url = video.get("url") or video.get("download_url")
                            if url:
                                return {
                                    "url": url,
                                    "cover_url": video.get("cover_url"),
                                    "width": video.get("width"),
                                    "height": video.get("height"),
                                    "duration_ms": video.get("metadata", {}).get("duration_ms", 0)
                                }
                        except:
                            pass
    return None

def wait_for_video(run_id, thread_id, timeout=300, poll_interval=10):
    start_time = time.time()
    while time.time() - start_time < timeout:
        thread = get_thread(run_id, thread_id)
        run = thread["run_list"][0]
        state = run.get("state")
        
        if state == 3:
            video = extract_video_url(thread)
            if video:
                return video, None
            return None, "任务完成但未找到视频"
        elif state == 4:
            fail_reason = run.get("fail_reason")
            return None, f"任务失败: {fail_reason}"
        
        time.sleep(poll_interval)
    
    return None, "超时"

def sse_event(data):
    """生成 SSE 事件"""
    return f"data: {json.dumps(data, ensure_ascii=False)}\n\n"

@app.route("/v1/chat/completions", methods=["POST"])
def chat_completions():
    body = request.json or {}
    messages = body.get("messages", [])
    model = body.get("model", "seedance-2.0")
    stream = body.get("stream", False)
    
    prompt = ""
    for msg in messages:
        if msg.get("role") == "user":
            content = msg.get("content", "")
            if isinstance(content, str):
                prompt = content
            elif isinstance(content, list):
                for item in content:
                    if isinstance(item, dict) and item.get("type") == "text":
                        prompt += item.get("text", "")
    
    if not prompt:
        error = {"error": {"message": "缺少用户消息", "type": "invalid_request_error"}}
        if stream:
            return Response(sse_event(error), mimetype="text/event-stream")
        return jsonify(error), 400
    
    # 测试消息快速返回（不生成视频）
    test_messages = {"hi", "hello", "test", "测试", "你好", "ping"}
    is_test = prompt.strip().lower() in test_messages or model not in MODEL_MAP

    # 从请求中提取视频时长（用于按秒计费：1秒 = 1000 tokens）
    duration_sec = body.get("duration") or body.get("duration_sec") or body.get("video_duration")
    try:
        duration_sec = int(duration_sec)
    except (TypeError, ValueError):
        duration_sec = 4
    # 限制最低 4 秒
    if duration_sec < 4:
        duration_sec = 4
    completion_tokens = duration_sec * 1000

    if is_test:
        content = f"连接成功！小云雀 API 代理正常工作。支持的模型: {', '.join(MODEL_MAP.keys())}"
        resp_data = {
            "id": f"test-{int(time.time())}",
            "object": "chat.completion",
            "created": int(time.time()),
            "model": model,
            "choices": [{
                "index": 0,
                "message": {"role": "assistant", "content": content},
                "finish_reason": "stop"
            }],
            "usage": {
                "prompt_tokens": 0,
                "completion_tokens": completion_tokens,
                "total_tokens": completion_tokens
            }
        }
        if stream:
            def generate():
                yield sse_event({
                    "id": resp_data["id"],
                    "object": "chat.completion.chunk",
                    "created": resp_data["created"],
                    "model": model,
                    "choices": [{"index": 0, "delta": {"role": "assistant", "content": content}, "finish_reason": None}]
                })
                yield sse_event({
                    "id": resp_data["id"],
                    "object": "chat.completion.chunk",
                    "created": resp_data["created"],
                    "model": model,
                    "choices": [{"index": 0, "delta": {}, "finish_reason": "stop"}],
                    "usage": resp_data["usage"]
                })
            return Response(generate(), mimetype="text/event-stream")
        return jsonify(resp_data), 200
    
    video_model = MODEL_MAP.get(model, "seedance2.0_fast_vision")
    
    def wait_for_video_streaming(run_id, thread_id, timeout=600, poll_interval=10, keepalive_interval=15):
        """流式等待视频，定期发送 keep-alive 事件"""
        start_time = time.time()
        last_keepalive = time.time()
        while time.time() - start_time < timeout:
            thread = get_thread(run_id, thread_id)
            run = thread["run_list"][0]
            state = run.get("state")
            
            if state == 3:
                video = extract_video_url(thread)
                if video:
                    return video, None
                return None, "任务完成但未找到视频"
            elif state == 4:
                fail_reason = run.get("fail_reason")
                return None, f"任务失败: {fail_reason}"
            
            # 定期发送 keep-alive 事件
            if time.time() - last_keepalive >= keepalive_interval:
                elapsed = int(time.time() - start_time)
                yield sse_event({
                    "id": run_id,
                    "object": "chat.completion.chunk",
                    "created": int(time.time()),
                    "model": model,
                    "choices": [{"index": 0, "delta": {"content": f"视频生成中... 已等待 {elapsed} 秒"}, "finish_reason": None}]
                })
                last_keepalive = time.time()
            
            time.sleep(poll_interval)
        
        return None, "超时"
    
    def generate_video():
        try:
            run_id, thread_id = submit_run(prompt, video_model, resolution=body.get("resolution"), duration_sec=duration_sec, ratio=body.get("ratio"))
            logging.info(f"Task submitted: run_id={run_id}, resolution={body.get('resolution')}, duration={duration_sec}s")
            
            # 发送开始事件
            if stream:
                yield sse_event({
                    "id": run_id,
                    "object": "chat.completion.chunk",
                    "created": int(time.time()),
                    "model": model,
                    "choices": [{"index": 0, "delta": {"role": "assistant", "content": "正在生成视频..."}, "finish_reason": None}]
                })
                
                # 流式等待，带 keep-alive
                video = None
                error = None
                for event in wait_for_video_streaming(run_id, thread_id):
                    if isinstance(event, tuple):
                        video, error = event
                        break
                    yield event
                
                if error:
                    content = f"视频生成失败: {error}"
                else:
                    content = (
                        f"视频生成成功！\n\n"
                        f"视频地址: {video['url']}\n"
                        f"封面地址: {video.get('cover_url', '')}\n"
                        f"分辨率: {video.get('width', '')}x{video.get('height', '')}\n"
                        f"时长: {video.get('duration_ms', 0) // 1000}秒\n"
                    )
                
                yield sse_event({
                    "id": run_id,
                    "object": "chat.completion.chunk",
                    "created": int(time.time()),
                    "model": model,
                    "choices": [{"index": 0, "delta": {"content": content}, "finish_reason": None}],
                    "video": video if not error else None
                })
                yield sse_event({
                    "id": run_id,
                    "object": "chat.completion.chunk",
                    "created": int(time.time()),
                    "model": model,
                    "choices": [{"index": 0, "delta": {}, "finish_reason": "stop"}],
                    "usage": {
                        "prompt_tokens": 0,
                        "completion_tokens": completion_tokens,
                        "total_tokens": completion_tokens
                    }
                })
            else:
                video, error = wait_for_video(run_id, thread_id)
                
                if error:
                    content = f"视频生成失败: {error}"
                else:
                    content = (
                        f"视频生成成功！\n\n"
                        f"视频地址: {video['url']}\n"
                        f"封面地址: {video.get('cover_url', '')}\n"
                        f"分辨率: {video.get('width', '')}x{video.get('height', '')}\n"
                        f"时长: {video.get('duration_ms', 0) // 1000}秒\n"
                    )
                
                return jsonify({
                    "id": run_id,
                    "object": "chat.completion",
                    "created": int(time.time()),
                    "model": model,
                    "choices": [{"index": 0, "message": {"role": "assistant", "content": content}, "finish_reason": "stop"}],
                    "video": video if not error else None
                }), 200
                
        except Exception as e:
            logging.error(f"Error: {e}")
            if stream:
                yield sse_event({"error": {"message": str(e), "type": "server_error"}})
            else:
                return jsonify({"error": {"message": str(e), "type": "server_error"}}), 500
    
    if stream:
        return Response(generate_video(), mimetype="text/event-stream")
    else:
        # 非流式：直接生成并返回
        try:
            run_id, thread_id = submit_run(prompt, video_model)
            video, error = wait_for_video(run_id, thread_id)
            
            if error:
                content = f"视频生成失败: {error}"
            else:
                content = (
                    f"视频生成成功！\n\n"
                    f"视频地址: {video['url']}\n"
                    f"封面地址: {video.get('cover_url', '')}\n"
                    f"分辨率: {video.get('width', '')}x{video.get('height', '')}\n"
                    f"时长: {video.get('duration_ms', 0) // 1000}秒\n"
                )
            
            return jsonify({
                "id": run_id,
                "object": "chat.completion",
                "created": int(time.time()),
                "model": model,
                "choices": [{"index": 0, "message": {"role": "assistant", "content": content}, "finish_reason": "stop"}],
                "video": video if not error else None,
                "usage": {
                    "prompt_tokens": 0,
                    "completion_tokens": completion_tokens,
                    "total_tokens": completion_tokens
                }
            }), 200
        except Exception as e:
            return jsonify({"error": {"message": str(e), "type": "server_error"}}), 500

@app.route("/v1/models", methods=["GET"])
def models():
    return jsonify({
        "object": "list",
        "data": [
            {"id": m, "object": "model", "owned_by": "xiaoyunque"}
            for m in MODEL_MAP.keys()
        ]
    })

@app.route("/health", methods=["GET"])
def health():
    return jsonify({"status": "ok"})

@app.route("/", defaults={"path": ""})
@app.route("/<path:path>", methods=["GET", "POST", "PUT", "DELETE"])
def catch_all(path):
    logging.warning(f"UNHANDLED: {request.method} /{path}")
    return jsonify({"error": f"Not found: {request.method} /{path}", "type": "invalid_request_error"}), 404

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=8081, debug=False)
