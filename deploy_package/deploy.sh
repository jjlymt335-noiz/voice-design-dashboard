#!/usr/bin/env bash
#
# 部署 Voice Design 日报 → Cloud Run Job + Cloud Scheduler
#
# 使用前请先设置:
#   export LARK_WEBHOOK_URL="https://open.larksuite.com/open-apis/bot/v2/hook/xxxxx"
#
# 然后执行:
#   bash deploy.sh
#
set -euo pipefail

# ── 配置（按需修改）─────────────────────────────────
PROJECT_ID="${GCP_PROJECT_ID:-noiz-430406}"
RUN_REGION="${RUN_REGION:-asia-east1}"
AR_REPO="${AR_REPO:-cloud-run-source-deploy}"
AR_LOCATION="${AR_LOCATION:-}"
BUILD_REGION="${BUILD_REGION:-}"
JOB_NAME="voice-design-daily-report"
SCHEDULER_NAME="trigger-voice-design-daily-report"

# 每天早上 10:00 (Asia/Taipei) 触发
SCHEDULE="0 10 * * *"
TIME_ZONE="Asia/Taipei"

# ── 校验 ─────────────────────────────────────────────
if [ -z "${LARK_WEBHOOK_URL:-}" ]; then
  echo "Error: Please set LARK_WEBHOOK_URL environment variable"
  echo "   export LARK_WEBHOOK_URL=\"https://open.larksuite.com/open-apis/bot/v2/hook/xxxxx\""
  exit 1
fi

echo "Project: $PROJECT_ID"
echo "Cloud Run Region: $RUN_REGION"
echo "Lark Webhook: ${LARK_WEBHOOK_URL:0:50}..."
echo ""

# ── 解析 Artifact Registry 仓库位置 ───────────────────
if [ -z "$AR_LOCATION" ]; then
  AR_LOCATION="$(gcloud artifacts repositories list \
    --project="$PROJECT_ID" \
    --filter="name:$AR_REPO" \
    --format="value(location)" \
    --limit=1 || true)"
fi

if [ -z "$AR_LOCATION" ]; then
  echo "Creating Artifact Registry repo $AR_REPO in $RUN_REGION..."
  AR_LOCATION="$RUN_REGION"
  gcloud artifacts repositories create "$AR_REPO" \
    --project="$PROJECT_ID" \
    --repository-format=docker \
    --location="$AR_LOCATION" \
    --description="Images for Cloud Run jobs"
else
  echo "Artifact Registry repo: $AR_REPO"
  echo "Artifact Registry location: $AR_LOCATION"
fi

IMAGE="$AR_LOCATION-docker.pkg.dev/$PROJECT_ID/$AR_REPO/$JOB_NAME"

# ── Step 1: 构建并推送镜像 ────────────────────────────
echo "Step 1/3: Building Docker image..."
if [ -n "$BUILD_REGION" ]; then
  gcloud builds submit \
    --project="$PROJECT_ID" \
    --region="$BUILD_REGION" \
    --tag="$IMAGE" \
    .
else
  gcloud builds submit \
    --project="$PROJECT_ID" \
    --tag="$IMAGE" \
    .
fi

# ── Step 2: 创建/更新 Cloud Run Job ──────────────────
echo "Step 2/3: Deploying Cloud Run Job..."
if gcloud run jobs describe "$JOB_NAME" --project="$PROJECT_ID" --region="$RUN_REGION" &>/dev/null; then
  echo "   (Updating existing Job)"
  gcloud run jobs update "$JOB_NAME" \
    --project="$PROJECT_ID" \
    --region="$RUN_REGION" \
    --image="$IMAGE" \
    --set-env-vars="LARK_WEBHOOK_URL=$LARK_WEBHOOK_URL,GCP_PROJECT_ID=$PROJECT_ID" \
    --task-timeout=300 \
    --max-retries=1
else
  echo "   (Creating new Job)"
  gcloud run jobs create "$JOB_NAME" \
    --project="$PROJECT_ID" \
    --region="$RUN_REGION" \
    --image="$IMAGE" \
    --set-env-vars="LARK_WEBHOOK_URL=$LARK_WEBHOOK_URL,GCP_PROJECT_ID=$PROJECT_ID" \
    --task-timeout=300 \
    --max-retries=1
fi

# ── Step 3: 创建/更新 Cloud Scheduler ─────────────────
echo "Step 3/3: Setting up Cloud Scheduler ($SCHEDULE $TIME_ZONE)..."

JOB_URI="https://$RUN_REGION-run.googleapis.com/apis/run.googleapis.com/v1/namespaces/$PROJECT_ID/jobs/$JOB_NAME:run"

SA_EMAIL="$(gcloud iam service-accounts list \
  --project="$PROJECT_ID" \
  --filter="email:compute@developer.gserviceaccount.com" \
  --format="value(email)" \
  --limit=1)"

if [ -z "$SA_EMAIL" ]; then
  SA_EMAIL="${PROJECT_ID//-/_}@appspot.gserviceaccount.com"
fi

if gcloud scheduler jobs describe "$SCHEDULER_NAME" --project="$PROJECT_ID" --location="$RUN_REGION" &>/dev/null; then
  echo "   (Updating existing Scheduler)"
  gcloud scheduler jobs update http "$SCHEDULER_NAME" \
    --project="$PROJECT_ID" \
    --location="$RUN_REGION" \
    --schedule="$SCHEDULE" \
    --time-zone="$TIME_ZONE" \
    --uri="$JOB_URI" \
    --http-method=POST \
    --oauth-service-account-email="$SA_EMAIL"
else
  echo "   (Creating new Scheduler)"
  gcloud scheduler jobs create http "$SCHEDULER_NAME" \
    --project="$PROJECT_ID" \
    --location="$RUN_REGION" \
    --schedule="$SCHEDULE" \
    --time-zone="$TIME_ZONE" \
    --uri="$JOB_URI" \
    --http-method=POST \
    --oauth-service-account-email="$SA_EMAIL"
fi

echo ""
echo "Done!"
echo ""
echo "Commands:"
echo "   # Manual trigger"
echo "   gcloud run jobs execute $JOB_NAME --project=$PROJECT_ID --region=$RUN_REGION"
echo ""
echo "   # View logs"
echo "   gcloud run jobs executions list --job=$JOB_NAME --project=$PROJECT_ID --region=$RUN_REGION"
echo ""
echo "   # Pause/Resume scheduler"
echo "   gcloud scheduler jobs pause  $SCHEDULER_NAME --project=$PROJECT_ID --location=$RUN_REGION"
echo "   gcloud scheduler jobs resume $SCHEDULER_NAME --project=$PROJECT_ID --location=$RUN_REGION"
