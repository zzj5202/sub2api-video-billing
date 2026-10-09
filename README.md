# Sub2API 视频按秒/分辨率计费方案

通过前置代理 + 小云雀代理，实现 Sub2API 视频生成的**动态按秒计费**，支持任意时长，无需为每种时长单独配置模型。

## 方案原理

利用 Sub2API 的 `per_token` 计费模式，把"秒"转换成"token"：

```
1秒 = 1000 tokens
价格 = 每秒单价 × 时长(秒)
```

### 三个组件配合

1. **前置代理 (front_proxy, 端口 8082)**
   - 按分辨率修改模型名：`seedance-2.0-fast` + `480p` → `seedance-2.0-fast-480p`
   - 时长小于 4 秒自动补全为 4 秒
   - 转发到 Sub2API (8080)

2. **小云雀代理 (xyq_proxy, 端口 8081)**
   - 在响应中返回 `usage.completion_tokens = duration × 1000`
   - Sub2API 按 `output_price × token数` 自动扣费

3. **Sub2API**
   - 为每个 `模型-分辨率` 配置 `output_price`（每秒单价 / 1000）

## 当前价格表

| 模型 | 480p | 720p | 1080p |
|------|------|------|-------|
| seedance-2.0-fast | $0.20/s | $0.30/s | $0.50/s |
| seedance-2.0-mini | $0.10/s | $0.25/s | $0.40/s |
| seedance-2.0-pro | $0.30/s | $0.50/s | $0.80/s |

## 部署步骤

### 1. 部署 Sub2API

使用仓库中的 `docker-compose.yml` 启动 Sub2API（端口 8080）。

### 2. 部署小云雀代理 (xyq_proxy)

```bash
cd xyq_proxy
docker build -f Dockerfile.xyq -t xyq-proxy:latest .
docker run -d --name xyq-proxy --network sub2api_sub2api-network -p 8081:8081 \
  -e XYQ_API_KEY=你的小云雀APIKey \
  --restart unless-stopped xyq-proxy:latest
```

### 3. 部署前置代理 (front_proxy)

```bash
cd front_proxy
docker build -t front-proxy .
docker run -d --name front-proxy --network sub2api_sub2api-network -p 8082:8082 \
  --restart unless-stopped front-proxy
```

### 4. 配置 Sub2API 定价

在 Sub2API 管理后台 → 分组 → 模型定价中，为每个 `模型-分辨率` 配置：

- billing_mode: `per_token`
- input_price: `0`
- output_price: `每秒单价 / 1000`

例如：480p 每秒 $0.20 → output_price = `0.0002`

### 5. 画布端配置

在画布设置中，视频大模型 Base URL 改为：
```
http://你的服务器IP:8082
```

## 目录结构

```
.
├── docker-compose.yml          # Sub2API 部署配置
├── front_proxy/
│   ├── front_proxy.py          # 前置代理（改模型名 + 最低4秒补全）
│   └── Dockerfile
└── xyq_proxy/
    ├── xyq_proxy.py            # 小云雀代理（返回 usage token）
    ├── Dockerfile.xyq
    └── requirements_xyq.txt
```

## 计费示例

| 分辨率 | 时长 | 计算 | 费用 |
|--------|------|------|------|
| 480p | 7秒 | $0.20 × 7 | $1.40 |
| 720p | 5秒 | $0.30 × 5 | $1.50 |
| 1080p | 3秒 | $0.50 × 3（补全为4秒） | $2.00 |
