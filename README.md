# AI 排班系統 - Backend API

Django REST API 後端服務，提供排班管理、出勤打卡、AI 自動排班等功能。

## 技術棧

- Django 5 + Django REST Framework
- PostgreSQL 16
- Redis + Celery
- Google OR-Tools (AI 排班引擎)
- Firebase Admin SDK (認證)
- drf-spectacular (OpenAPI/Swagger)

## 快速開始

### 使用 Docker Compose（推薦）

```bash
# 啟動所有服務（Django + PostgreSQL + Redis）
docker-compose up -d

# 執行資料庫遷移
docker-compose exec web python manage.py migrate

# 建立超級使用者
docker-compose exec web python manage.py createsuperuser

# 查看日誌
docker-compose logs -f web
```

### 本地開發

```bash
# 安裝依賴
pip install -r requirements/development.txt

# 設定環境變數
cp .env.example .env
# 編輯 .env 填入 Firebase 憑證等

# 執行遷移
python manage.py migrate

# 啟動開發伺服器
python manage.py runserver
```

## API 文件

- Swagger UI: http://localhost:8000/api/docs/
- ReDoc: http://localhost:8000/api/redoc/
- OpenAPI JSON: http://localhost:8000/api/schema/

## 專案結構

```
scheduling-api/
├── config/              # Django 設定
├── apps/                # 應用程式模組
├── docs/                # API 文件與前端開發者指南
├── requirements/       # Python 依賴
└── docker-compose.yml   # Docker 開發環境
```

## 開發指南

詳見 `docs/FRONTEND_DEVELOPER_GUIDE.md`（給前端工程師）
