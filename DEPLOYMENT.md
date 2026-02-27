# 部署指南

## Railway 部署

### 1. 準備工作

1. 在 Railway 建立新專案
2. 新增 PostgreSQL 服務
3. 新增 Redis 服務（可選，用於 Celery）

### 2. 環境變數設定

在 Railway 專案設定中新增以下環境變數：

```
DEBUG=0
SECRET_KEY=your-secret-key-here
ALLOWED_HOSTS=your-domain.railway.app

DATABASE_URL=postgresql://user:password@host:port/dbname
REDIS_URL=redis://host:port/0

FIREBASE_CREDENTIALS_JSON={"type":"service_account",...}
AI_SCHEDULE_PROVIDER=apps.ai_engine.providers.ortools_provider.ORToolsProvider

CORS_ALLOWED_ORIGINS=https://your-frontend-domain.com
```

### 3. 部署

Railway 會自動偵測 Dockerfile 並部署。

### 4. 執行遷移

部署後，在 Railway 的服務中執行：

```bash
python manage.py migrate
python manage.py createsuperuser
```

## GCP Cloud Run 部署

### 1. 建立 Cloud SQL 資料庫

```bash
gcloud sql instances create scheduling-db \
  --database-version=POSTGRES_16 \
  --tier=db-f1-micro \
  --region=asia-east1
```

### 2. 建立 Cloud Storage Bucket（用於靜態檔案）

```bash
gsutil mb gs://your-bucket-name
```

### 3. 建置並推送 Docker 映像

```bash
gcloud builds submit --tag gcr.io/your-project-id/scheduling-api
```

### 4. 部署到 Cloud Run

```bash
gcloud run deploy scheduling-api \
  --image gcr.io/your-project-id/scheduling-api \
  --platform managed \
  --region asia-east1 \
  --allow-unauthenticated \
  --set-env-vars="DEBUG=0,SECRET_KEY=..."
```

## CI/CD

### GitHub Actions 範例

建立 `.github/workflows/deploy.yml`：

```yaml
name: Deploy to Railway

on:
  push:
    branches: [main]

jobs:
  deploy:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v3
      - uses: bervProject/railway-deploy@v1.0.0
        with:
          railway_token: ${{ secrets.RAILWAY_TOKEN }}
          service: scheduling-api
```
