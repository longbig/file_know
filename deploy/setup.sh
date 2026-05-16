#!/bin/bash
# 学术评论句提取工具 - 腾讯云 Ubuntu 一键部署脚本
# 用法: bash setup.sh
set -e

APP_NAME="file_know"
APP_DIR="/opt/$APP_NAME"
APP_USER="www-data"
PYTHON_VERSION="3.10"

echo "=========================================="
echo " 学术评论句提取工具 - 部署脚本"
echo "=========================================="

# 1. 系统依赖
echo "[1/6] 安装系统依赖..."
apt-get update -qq
apt-get install -y -qq python3 python3-pip python3-venv git > /dev/null

# 检查 Python 版本
PY_VER=$(python3 -c "import sys; print(f'{sys.version_info.major}.{sys.version_info.minor}')")
echo "  Python 版本: $PY_VER"

# 2. 创建项目目录
echo "[2/6] 创建项目目录..."
mkdir -p "$APP_DIR"

# 如果当前目录有项目文件，复制过去
if [ -f "app.py" ]; then
    echo "  从当前目录复制项目文件..."
    rsync -a --exclude='.git' --exclude='output' --exclude='__pycache__' \
          --exclude='.DS_Store' --exclude='deploy' --exclude='english' \
          . "$APP_DIR/"
else
    echo "  请先将项目文件上传到 $APP_DIR"
    echo "  或在项目根目录下运行此脚本"
fi

# 3. 创建虚拟环境 & 安装依赖
echo "[3/6] 创建虚拟环境并安装依赖..."
python3 -m venv "$APP_DIR/venv"
"$APP_DIR/venv/bin/pip" install --upgrade pip -q
"$APP_DIR/venv/bin/pip" install -r "$APP_DIR/requirements.txt" -q
echo "  依赖安装完成"

# 4. 创建必要目录 & 设置权限
echo "[4/6] 设置目录权限..."
mkdir -p "$APP_DIR/output" "$APP_DIR/logs"
chown -R "$APP_USER:$APP_USER" "$APP_DIR"

# 5. 配置环境变量
echo "[5/6] 配置环境变量..."
ENV_FILE="$APP_DIR/.env"
if [ ! -f "$ENV_FILE" ]; then
    cat > "$ENV_FILE" << 'EOF'
ANTHROPIC_API_KEY=your_api_key_here
ANTHROPIC_BASE_URL=https://timesniper.club
EOF
    chown "$APP_USER:$APP_USER" "$ENV_FILE"
    chmod 600 "$ENV_FILE"
    echo "  已创建 $ENV_FILE，请编辑填入真实的 API Key："
    echo "    nano $ENV_FILE"
else
    echo "  .env 文件已存在，跳过"
fi

# 6. 安装 systemd 服务
echo "[6/6] 配置 systemd 服务..."
cat > /etc/systemd/system/${APP_NAME}.service << EOF
[Unit]
Description=学术评论句提取工具
After=network.target

[Service]
Type=simple
User=$APP_USER
Group=$APP_USER
WorkingDirectory=$APP_DIR
EnvironmentFile=$APP_DIR/.env
ExecStart=$APP_DIR/venv/bin/python app.py
Restart=on-failure
RestartSec=5
StandardOutput=append:$APP_DIR/logs/service.log
StandardError=append:$APP_DIR/logs/service.log

[Install]
WantedBy=multi-user.target
EOF

systemctl daemon-reload
systemctl enable "$APP_NAME"

echo ""
echo "=========================================="
echo " 部署完成！后续操作："
echo "=========================================="
echo ""
echo " 1. 编辑 API Key："
echo "    nano $APP_DIR/.env"
echo ""
echo " 2. 启动服务："
echo "    systemctl start $APP_NAME"
echo ""
echo " 3. 查看状态："
echo "    systemctl status $APP_NAME"
echo ""
echo " 4. 查看日志："
echo "    tail -f $APP_DIR/logs/service.log"
echo ""
echo " 5. 开放防火墙端口（如使用腾讯云安全组）："
echo "    在腾讯云控制台 -> 安全组 -> 入站规则 -> 添加 TCP 7860 端口"
echo ""
echo " 6. 访问地址："
echo "    http://<你的服务器公网IP>:7860"
echo ""
